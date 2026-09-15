#!/usr/bin/env python3
"""Matrix-shape sweep: latency, bandwidth and GFLOPS vs matrix size.

WHY THIS IS NOT JUST "RUN EACH SHAPE ONCE". Real DNN layer shapes are tiny for
this engine. A 768x768 GEMV at 2:4 is 2,304 weight beats = 7.7 us of compute
against ~600 us of fixed launch overhead, so a naive measurement would be 99%
harness and 1% architecture. Plotting that would compare our OpenCL host to
itself across eleven x-axis labels.

THE FIX: BATCHING. One lap is 64 rows, so an M x N GEMV is (M/64) laps at
nwin = N/32. Running the SAME matrix R times back-to-back is just R x (M/64)
laps -- the engine cannot tell the difference, and the activation vector is
replayed exactly as it would be in the real workload. Pick R so the whole run
clears ~2M weight beats, then

    per-matrix latency = (measured latency - launch overhead) / R

That is the STEADY-STATE cost of one matrix of that shape, which is what a
deployed accelerator actually experiences: the kernel stays resident and work is
streamed at it, rather than being enqueued afresh per matrix.

WHAT VARIES, AND WHY IT MIGHT MATTER. The interesting axis is N (matrix width).
    weight beats per matrix = (M/64) x (N/32) x freeze
so beats scale with M x N, and latency should too -- a flat line on a
per-element basis. Where it might NOT be flat is small N: few windows per lap
means the per-lap accumulator flush and pipeline drain are amortised over less
work, and that penalty should be WORSE at high sparsity (2:32 has one compute
cycle per window against 2:4's eight). If that shows up, "the sparse engine
wants wide matrices, more so at high sparsity" is a real architectural finding.

CONSTRAINTS, ENFORCED BELOW
    N % 32 == 0     one activation window
    N <= 8192       the replay buffer holds 256 beats (fifo_gen_vector_cycle)

FAMILY CONFIGURATIONS (--cores / --blocks). One lap = cores x blocks rows, so an
M-row matrix is ceil(M / lanes) laps. Where M is not a multiple of the lap size
(512 rows on 16x3's 48-row laps, for instance) the last lap carries PADDING rows
that a real deployment would also compute and discard. That cost is real, so it is
measured, recorded in `padding_rows`, and NOT credited in GFLOPS_effective (which
uses the true M x N). Mixed quarters are ceil(M / (4 x lanes)) laps each.

LAUNCH OVERHEAD IS PER CONFIGURATION. The OVERHEAD table below was measured on the
8x8 build at 300 MHz. Overhead is the host launching every compute unit -- 18 on
8x8, 7 on 4x4, 33 on 4x32 -- so that table is wrong for every other shape.
`--overhead auto` measures it for THIS xclbin, THIS sparsity and THIS clock: three
runs sizes at N=1024, a least-squares line through span vs beats, intercept =
overhead. It is the same method the table was originally built with, just not
stale. Default: `table` at 8x8 (reproduces the published sweep), `auto` otherwise.

Python 3.6 compatible.

Usage
-----
  python3 run_shapes.py --design sparse --sparsity 01 \\
      --host ~/GEMV_Sparse/Vitis/host_sparse \\
      --xclbin ~/GEMV_Sparse/Vitis_slr/gemv_sparse_slr.xclbin \\
      --emu ~/GEMV_Sparse/GEMV_4.0_Source/Emulation \\
      --clock 300 --csv shapes_2to8_300MHz.csv

  python3 run_shapes.py --design dense --host ~/GEMV_Dense/Vitis/host ...
"""

from __future__ import print_function

import argparse
import csv
import math
import os
import re
import subprocess
import sys

# (label, M rows, N cols) -- GEMV shapes taken from real transformer/CNN layers
SHAPES = [
    ("G1",   512,  512),
    ("G2",   768,  768),
    ("G3",  1024, 1024),
    ("G4",  3072,  768),
    ("G5",   768, 3072),
    ("G6",  4096, 1024),
    ("G7",  1024, 4096),
    ("G8",  2048, 2048),
    ("G9",  4096, 4096),
    ("G10", 8192, 2048),
    ("G11", 2048, 8192),
]

# OPT-IN shapes. NOT part of the default sweep -- reachable only via --only.
#
# H1 exists for ONE purpose: a same-board comparison against Serpens (Song,
# Chi, Guo, Cong, DAC'22) at EQUAL NON-ZERO COUNT. Their hollywood row (their
# Table 3: 1.07M vertices, 113M edges) is measured at 6.20 ms on Serpens-A16
# (their Table 4). At 2:32 this engine's non-zero count is
#     M x N / 16 = 220,672 x 8,192 / 16 = 112,984,064
# which matches 113M to within 0.014%. M divides by 64 (3,448 laps) and also by
# 256, so --sparsity mix stays legal on it.
#
# RUN IT AT --sparsity 11 ONLY. At any other sparsity the freeze changes, the
# non-zero count changes with it, and the equal-NNZ match is gone.
#
# It is deliberately kept OUT of SHAPES: GEMV_Charts_Final.xlsx addresses the
# merged shapes sheet by absolute cell reference and geomeans over exactly 11
# shapes, so a 12th default row would silently move every chart series.
EXTRA_SHAPES = [
    ("H1", 220672, 8192),
]

FREEZE = {"00": 8, "01": 4, "10": 2, "11": 1}          # 32/M for 2:M
LABEL = {"00": "2:4", "01": "2:8", "10": "2:16", "11": "2:32", "mix": "MIXED"}
DENSE_FREEZE = 16                                       # dense: 512 beats/lap at nwin=32

# "mix" splits each matrix into four EQUAL-ROW quarters at 2:4 / 2:8 / 2:16 /
# 2:32. A quarter is M/4 rows = M/256 laps, so M must divide by 256 (every shape
# in SHAPES does). Its cost per matrix is the sum of the four quarters:
#   (M/256) x nwin x (8+4+2+1) = (M/256) x nwin x 15
# which is why MIXED lands between all-2:4 and all-2:32, as measured.
MIX_FREEZE_SUM = 8 + 4 + 2 + 1

# Launch overhead per configuration, measured as the y-intercept of the 300 MHz
# four-point sweeps. It is a host-side cost -- buffer setup, queue dispatch, CU
# launch and drain -- so it is taken as constant across shape and clock. The
# activation image does grow with N, but only to 8 KB at N=8192, which is noise
# against a 500-700 us launch.
OVERHEAD = {"dense": 457.9, "2:4": 619.1, "2:8": 569.5, "2:16": 603.7,
            "2:32": 685.5,
            # MIXED is not a sweep intercept -- it comes from the mixed run's
            # additivity test: measured span minus the sum of its four segments
            # at their own uniform per-beat rates (7400.7 - 6716.7).
            "MIXED": 684.0}

MAX_BEATS_PER_PC = 256 * 1024 * 1024 // 32              # one HBM pseudo-channel

RE_SPAN = re.compile(r"kernel span\s*:\s*([0-9.]+)\s*us")
RE_HOST_ROWS = re.compile(r"(\d+)\s+output beats\s*=\s*(\d+)\s+rows")
RE_HOST_BEATS = re.compile(r"stimulus:\s*(\d+)\s+weight beats")


def ceildiv(a, b):
    return -(-a // b)


def fit_line(xs, ys):
    """Least squares y = a + b x. Returns (intercept, slope, r2, intercept_se).

    THE INTERCEPT IS AN EXTRAPOLATION, and R^2 does not say how good it is. On 4x4 @
    400 MHz 2:32, two runs of the same sweep both fitted with R^2 > 0.9995 and still
    gave 293.5 and 355.4 us -- a 62 us move that shifts every per-matrix latency in the
    sweep by ~1.2%. The standard error of the intercept is the honest error bar:
        se(a) = sqrt( s^2 (1/n + mean(x)^2 / Sxx) ),  s^2 = SS_res / (n - 2)
    """
    n = float(len(xs))
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    a0 = my - b * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (a0 + b * x)) ** 2 for x, y in zip(xs, ys))
    r2 = (1.0 - ss_res / ss_tot) if ss_tot > 0 else 1.0
    se = ((ss_res / (n - 2)) * (1.0 / n + mx * mx / sxx)) ** 0.5 if n > 2 else float("nan")
    return a0, b, r2, se


def require_xrt():
    """Fail fast if the XRT environment is not sourced.

    Without XILINX_XRT the host aborts inside the XRT runtime with
    `terminate called ... XILINX_XRT not set` and a SIGABRT, which arrives only
    AFTER the stimulus for the first shape has been generated -- 60+ seconds and
    ~700 MB of writes wasted, and the failure looks like a crash in our own
    code. This has now cost time twice, so check it before touching anything.
    """
    if not os.environ.get("XILINX_XRT"):
        raise SystemExit(
            "XILINX_XRT is not set -- the XRT environment is not sourced in "
            "this shell. The host aborts inside XRT with SIGABRT, and only "
            "AFTER the first stimulus is generated, so the failure looks like "
            "a crash in this script. Fix: source /opt/xilinx/xrt/setup.sh")

def run(cmd, what):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = p.communicate()[0].decode("utf-8", "replace")
    if p.returncode != 0:
        sys.stderr.write(out)
        raise SystemExit("{} failed (exit {})".format(what, p.returncode))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--design", required=True, choices=["sparse", "dense"])
    ap.add_argument("--sparsity", default=None,
                    choices=["00", "01", "10", "11", "mix"],
                    help="required for --design sparse; 'mix' splits every matrix "
                         "into four equal-row quarters at 2:4/2:8/2:16/2:32")
    ap.add_argument("--host", required=True)
    ap.add_argument("--xclbin", required=True)
    ap.add_argument("--emu", required=True)
    ap.add_argument("--clock", type=float, required=True)
    ap.add_argument("--reps", type=int, default=3, help="runs per shape")
    ap.add_argument("--target-beats", type=int, default=2097152,
                    help="batch each shape up to at least this many weight beats "
                         "so the launch overhead is a small, subtractable fraction")
    ap.add_argument("--only", default=None,
                    help="comma-separated shape names to run INSTEAD of the "
                         "full 11-shape sweep, e.g. --only H1. Names may come "
                         "from the default sweep or from EXTRA_SHAPES.")
    ap.add_argument("--cores", type=int, default=8,
                    help="engine CORES_NUM of this xclbin (sparse only)")
    ap.add_argument("--blocks", type=int, default=8,
                    help="engine BLOCKS_NUM of this xclbin (sparse only)")
    ap.add_argument("--overhead", default=None,
                    help="'auto' = measure it now for this xclbin/sparsity/clock "
                         "(default for any shape other than 8x8); 'table' = the "
                         "8x8 @ 300 MHz sweep intercepts (default at 8x8); or a "
                         "number in us")
    ap.add_argument("--csv", required=True)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and exit -- no card, no XRT needed")
    a = ap.parse_args()

    if a.design == "sparse" and not a.sparsity:
        raise SystemExit("--design sparse needs --sparsity")
    if a.design == "dense" and (a.cores, a.blocks) != (8, 8):
        raise SystemExit("--cores/--blocks apply to the sparse engine only")
    lab = "dense" if a.design == "dense" else LABEL[a.sparsity]
    mixed = (a.sparsity == "mix")
    freeze = (DENSE_FREEZE if a.design == "dense"
              else (None if mixed else FREEZE[a.sparsity]))
    lanes = a.cores * a.blocks
    macs_per_beat = 2 * lanes
    config = "{}x{}".format(a.cores, a.blocks)
    # input PCs that carry per-beat traffic: weights + indices (activations are
    # loaded once and replayed, so they are not per-beat bandwidth)
    if a.design == "dense":
        pcs = 8
    else:
        pcs = ceildiv(lanes * 32, 256) + ceildiv(10 * lanes + 2, 256)
    ovh_mode = a.overhead or ("table" if (a.cores, a.blocks) == (8, 8) else "auto")
    if ovh_mode == "table" and (a.cores, a.blocks) != (8, 8):
        raise SystemExit("--overhead table holds 8x8 @ 300 MHz intercepts; they do not "
                         "apply to {} (different CU count). Use --overhead auto."
                         .format(config))
    if not a.dry_run:
        require_xrt()

    host = os.path.abspath(os.path.expanduser(a.host))
    xclbin = os.path.abspath(os.path.expanduser(a.xclbin))
    emu = os.path.abspath(os.path.expanduser(a.emu))
    gen = os.path.join(emu, "gen_timing_stimulus.py")
    if not a.dry_run:
        for p, what in ((host, "host"), (xclbin, "xclbin"), (gen, "generator")):
            if not os.path.exists(p):
                raise SystemExit("no such {}: {}".format(what, p))
    shape_args = (["--cores", str(a.cores), "--blocks", str(a.blocks)]
                  if a.design == "sparse" else [])

    # ---- select shapes -----------------------------------------------------
    # Default behaviour is unchanged: the same 11 shapes in the same order.
    catalogue = SHAPES + EXTRA_SHAPES
    if a.only:
        want = [s.strip() for s in a.only.split(",") if s.strip()]
        by_name = dict((n, (n, M, N)) for n, M, N in catalogue)
        unknown = [w for w in want if w not in by_name]
        if unknown:
            raise SystemExit("unknown shape(s): {} -- known: {}".format(
                ", ".join(unknown), ", ".join(n for n, _, _ in catalogue)))
        selected = [by_name[w] for w in want]
    else:
        selected = SHAPES

    # ---- plan every shape before touching the card -------------------------
    plan = []
    for name, M, N in selected:
        if N % 32:
            raise SystemExit("{}: N={} is not a multiple of 32".format(name, N))
        if N > 8192:
            raise SystemExit("{}: N={} exceeds the 8192 replay-buffer limit".format(name, N))
        nwin = N // 32
        if mixed:
            # four equal-row quarters, each a whole number of laps
            q_1 = ceildiv(M, 4 * lanes)                   # laps per quarter, per matrix
            laps_1 = 4 * q_1
            beats_1 = q_1 * nwin * MIX_FREEZE_SUM
        else:
            q_1 = 0
            laps_1 = ceildiv(M, lanes)
            beats_1 = laps_1 * nwin * freeze
        rows_1 = laps_1 * lanes
        padding = rows_1 - M
        reps_batch = max(1, int(math.ceil(float(a.target_beats) / beats_1)))
        total_beats = reps_batch * beats_1
        while total_beats > MAX_BEATS_PER_PC and reps_batch > 1:
            reps_batch -= 1
            total_beats = reps_batch * beats_1
        plan.append(dict(name=name, M=M, N=N, nwin=nwin, laps_1=laps_1, q_1=q_1,
                         rows_1=rows_1, padding=padding, beats_1=beats_1,
                         R=reps_batch, laps=reps_batch * laps_1, beats=total_beats))

    print("{} {} {} @ {:.0f} MHz -- {} shapes, {} runs each".format(
        a.design, config, lab, a.clock, len(plan), a.reps))
    print("{} rows per lap, {} MACs per beat, {} per-beat input PCs; each shape "
          "batched to >= {:,} weight beats\n".format(lanes, macs_per_beat, pcs,
                                                     a.target_beats))
    print("  %-5s %6s %6s %6s %9s %8s %12s %7s %13s" %
          ("shape", "M", "N", "nwin", "laps/mat", "padding", "beats/matrix", "R",
           "total beats"))
    for p in plan:
        print("  %-5s %6d %6d %6d %9d %8d %12s %7d %13s" %
              (p["name"], p["M"], p["N"], p["nwin"], p["laps_1"], p["padding"],
               "{:,}".format(p["beats_1"]), p["R"], "{:,}".format(p["beats"])))
    if any(p["padding"] for p in plan):
        print("\n  padding > 0: M is not a whole number of {}-row laps; the last lap's "
              "extra rows are computed and discarded, and that cost IS measured."
              .format(lanes))
    print("")

    def gen_cmd(nwin, laps=None, q=None):
        cmd = [sys.executable, gen, "--nwin", str(nwin)] + shape_args
        if mixed:
            cmd += ["--mix", "00:{0},01:{0},10:{0},11:{0}".format(q)]
        else:
            cmd += ["--nlaps", str(laps)]
            if a.design == "sparse":
                cmd += ["--sparsity", a.sparsity]
        return cmd

    def host_span(label, expect_rows, expect_beats):
        txt = run([host, xclbin, emu, str(a.clock)], "host run " + label)
        hr = RE_HOST_ROWS.search(txt)
        if hr and int(hr.group(2)) != expect_rows:
            sys.stderr.write(txt)
            raise SystemExit(
                "{}: the host reports {} rows, the plan expects {}. The host binary "
                "was built for a different shape than --cores {} --blocks {}."
                .format(label, hr.group(2), expect_rows, a.cores, a.blocks))
        hb = RE_HOST_BEATS.search(txt)
        if hb and int(hb.group(1)) != expect_beats:
            sys.stderr.write(txt)
            raise SystemExit("{}: the host reports {} weight beats, the plan expects {}"
                             .format(label, hb.group(1), expect_beats))
        m = RE_SPAN.search(txt)
        if not m:
            sys.stderr.write(txt)
            raise SystemExit("no 'kernel span' in host output")
        return float(m.group(1))

    # ---- launch overhead ---------------------------------------------------
    ovh_r2, ovh_slope, ovh_se = "", "", ""
    if ovh_mode == "table":
        ovh = OVERHEAD[lab]
        print("launch overhead: {:.1f} us from the 8x8 @ 300 MHz table".format(ovh))
    elif ovh_mode == "auto":
        # three sizes at N=1024 (nwin 32): ~1/4, ~1/2 and 1x target beats
        nw = 32
        unit = nw * (MIX_FREEZE_SUM if mixed else freeze)   # beats per lap (or per q)
        sizes = [max(1, a.target_beats // (k * unit)) for k in (4, 2, 1)]
        print("launch overhead: measuring for {} {} @ {:.0f} MHz ({} runs x {} sizes)"
              .format(config, lab, a.clock, a.reps, len(sizes)))
        if a.dry_run:
            print("  (dry run: sizes {} {} at nwin 32)".format(
                sizes, "quarters" if mixed else "laps"))
            ovh = 0.0
        else:
            xs, ys = [], []
            for s in sizes:
                if mixed:
                    run(gen_cmd(nw, q=s), "overhead stimulus")
                    beats, rows = s * nw * MIX_FREEZE_SUM, 4 * s * lanes
                else:
                    run(gen_cmd(nw, laps=s), "overhead stimulus")
                    beats, rows = s * nw * freeze, s * lanes
                for _ in range(a.reps):
                    xs.append(float(beats))
                    ys.append(host_span("overhead fit", rows, beats))
            ovh, ovh_slope, ovh_r2, ovh_se = fit_line(xs, ys)
            print("  intercept {:.1f} +/- {:.1f} us (1 se)   slope {:.6f} us/beat ({:.1f} MHz "
                  "effective)   R^2 {:.5f}".format(ovh, ovh_se, ovh_slope, 1.0 / ovh_slope, ovh_r2))
            # what that error bar means for the numbers this sweep will report
            print("  -> per-matrix latency carries about +/-{:.1f}% from the overhead fit alone"
                  .format(100.0 * ovh_se / max(1.0, (a.target_beats * ovh_slope))))
            if ovh <= 0:
                raise SystemExit("fitted overhead {:.1f} us is not positive -- the runs are "
                                 "too noisy to separate overhead from work (shared card?). "
                                 "Re-run, or pass --overhead <us>.".format(ovh))
            if ovh_r2 < 0.99:
                print("  WARNING: R^2 {:.4f} < 0.99 -- a noisy fit; check the card is "
                      "otherwise idle".format(ovh_r2))
            ovh_r2, ovh_slope, ovh_se = round(ovh_r2, 6), round(ovh_slope, 9), round(ovh_se, 2)
    else:
        try:
            ovh = float(ovh_mode)
        except ValueError:
            raise SystemExit("--overhead must be auto, table, or a number")
        print("launch overhead: {:.1f} us (given)".format(ovh))
    print("")

    if a.dry_run:
        print("dry run -- nothing generated, nothing run")
        return

    out = []
    for p in plan:
        if mixed:
            run(gen_cmd(p["nwin"], q=p["R"] * p["q_1"]), "stimulus for " + p["name"])
        else:
            run(gen_cmd(p["nwin"], laps=p["laps"]), "stimulus for " + p["name"])

        spans = []
        for i in range(a.reps):
            spans.append(host_span(p["name"], p["R"] * p["rows_1"], p["beats"]))

        mean = sum(spans) / len(spans)
        per = (mean - ovh) / p["R"]                    # one matrix, steady state
        if per <= 0:
            raise SystemExit("{}: overhead exceeds the measured span".format(p["name"]))
        flops = 2.0 * p["M"] * p["N"]
        gf = flops / (per * 1000.0)
        bytes_in = p["beats_1"] * pcs * 32.0
        bw = bytes_in / (per * 1e-6) / 1e9
        ideal = p["beats_1"] / a.clock
        out.append(dict(
            design=a.design, config=config, cores=a.cores, blocks=a.blocks, lanes=lanes,
            sparsity=lab, clock_mhz=a.clock, shape=p["name"],
            M_rows=p["M"], N_cols=p["N"], nwin=p["nwin"],
            laps_per_matrix=p["laps_1"], padding_rows=p["padding"],
            beats_per_matrix=p["beats_1"], batch_R=p["R"], total_beats=p["beats"],
            batched_latency_us=round(mean, 3),
            launch_overhead_us=round(ovh, 3),
            overhead_method=ovh_mode, overhead_fit_r2=ovh_r2, overhead_fit_se_us=ovh_se,
            latency_per_matrix_us=round(per, 4),
            ideal_per_matrix_us=round(ideal, 4),
            dsp_occupancy=round(ideal / per, 4),
            GFLOPS_effective=round(gf, 2),
            GFLOPS_actual=round(2.0 * p["beats_1"] * macs_per_beat / (per * 1000.0), 2),
            bandwidth_GBs=round(bw, 2),
            bytes_in_per_matrix=int(bytes_in),
            spread_pct=round(100.0 * (max(spans) - min(spans)) / min(spans), 3),
            runs=" ".join("%.3f" % x for x in spans)))
        print("  %-5s %5dx%-5d  per-matrix %9.2f us  %8.1f GFLOPS  %7.2f GB/s  "
              "occ %.3f" % (p["name"], p["M"], p["N"], per, gf, bw,
                            ideal / per))

    cols = ["design", "config", "cores", "blocks", "lanes", "sparsity", "clock_mhz",
            "shape", "M_rows", "N_cols", "nwin", "laps_per_matrix", "padding_rows",
            "beats_per_matrix", "batch_R", "total_beats", "batched_latency_us",
            "launch_overhead_us", "overhead_method", "overhead_fit_r2", "overhead_fit_se_us",
            "latency_per_matrix_us", "ideal_per_matrix_us",
            "dsp_occupancy", "GFLOPS_effective", "GFLOPS_actual", "bandwidth_GBs",
            "bytes_in_per_matrix", "spread_pct", "runs"]
    with open(a.csv, "w") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        for r in out:
            w.writerow(r)
    print("\nCSV written: {}".format(a.csv))
    print("latency_per_matrix_us is the number to plot. It is a STEADY-STATE cost:\n"
          "the batched span with the launch overhead removed, divided by R.")


if __name__ == "__main__":
    main()

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
    M % 64 == 0     one lap = 64 rows
    N % 32 == 0     one activation window
    N <= 8192       the replay buffer holds 256 beats (fifo_gen_vector_cycle)

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
MACS_PER_BEAT = 128

RE_SPAN = re.compile(r"kernel span\s*:\s*([0-9.]+)\s*us")


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
    ap.add_argument("--csv", required=True)
    a = ap.parse_args()
    require_xrt()

    if a.design == "sparse" and not a.sparsity:
        raise SystemExit("--design sparse needs --sparsity")
    lab = "dense" if a.design == "dense" else LABEL[a.sparsity]
    mixed = (a.sparsity == "mix")
    freeze = (DENSE_FREEZE if a.design == "dense"
              else (None if mixed else FREEZE[a.sparsity]))
    pcs = 8 if a.design == "dense" else 11
    ovh = OVERHEAD[lab]

    host = os.path.abspath(os.path.expanduser(a.host))
    xclbin = os.path.abspath(os.path.expanduser(a.xclbin))
    emu = os.path.abspath(os.path.expanduser(a.emu))
    gen = os.path.join(emu, "gen_timing_stimulus.py")
    for p, what in ((host, "host"), (xclbin, "xclbin"), (gen, "generator")):
        if not os.path.exists(p):
            raise SystemExit("no such {}: {}".format(what, p))

    # ---- plan every shape before touching the card -------------------------
    plan = []
    for name, M, N in SHAPES:
        if M % 64:
            raise SystemExit("{}: M={} is not a multiple of 64".format(name, M))
        if N % 32:
            raise SystemExit("{}: N={} is not a multiple of 32".format(name, N))
        if N > 8192:
            raise SystemExit("{}: N={} exceeds the 8192 replay-buffer limit".format(name, N))
        nwin = N // 32
        laps_1 = M // 64
        if mixed:
            if M % 256:
                raise SystemExit("{}: M={} must divide by 256 for four equal-row "
                                 "quarters".format(name, M))
            q_1 = M // 256                       # laps per quarter, per matrix
            beats_1 = q_1 * nwin * MIX_FREEZE_SUM
        else:
            beats_1 = laps_1 * nwin * freeze
        reps_batch = max(1, int(math.ceil(float(a.target_beats) / beats_1)))
        if mixed:
            beats_of = lambda R: R * (M // 256) * nwin * MIX_FREEZE_SUM
        else:
            beats_of = lambda R: R * laps_1 * nwin * freeze
        total_beats = beats_of(reps_batch)
        while total_beats > MAX_BEATS_PER_PC and reps_batch > 1:
            reps_batch -= 1
            total_beats = beats_of(reps_batch)
        total_laps = reps_batch * laps_1
        plan.append(dict(name=name, M=M, N=N, nwin=nwin, laps_1=laps_1,
                         beats_1=beats_1, R=reps_batch, laps=total_laps,
                         beats=total_beats))

    print("{} {} @ {:.0f} MHz -- {} shapes, {} runs each".format(
        a.design, lab, a.clock, len(plan), a.reps))
    print("each shape batched to >= {:,} weight beats; launch overhead {:.1f} us "
          "subtracted\n".format(a.target_beats, ovh))
    print("  %-5s %6s %6s %6s %12s %7s %13s" %
          ("shape", "M", "N", "nwin", "beats/matrix", "R", "total beats"))
    for p in plan:
        print("  %-5s %6d %6d %6d %12s %7d %13s" %
              (p["name"], p["M"], p["N"], p["nwin"], "{:,}".format(p["beats_1"]),
               p["R"], "{:,}".format(p["beats"])))
    print("")

    out = []
    for p in plan:
        cmd = [sys.executable, gen, "--nwin", str(p["nwin"])]
        if mixed:
            # one segment per sparsity, equal rows, batched R times over
            q = p["R"] * (p["M"] // 256)
            cmd += ["--mix", "00:{0},01:{0},10:{0},11:{0}".format(q)]
        else:
            cmd += ["--nlaps", str(p["laps"])]
            if a.design == "sparse":
                cmd += ["--sparsity", a.sparsity]
        run(cmd, "stimulus for " + p["name"])

        spans = []
        for i in range(a.reps):
            txt = run([host, xclbin, emu, str(a.clock)], "host run " + p["name"])
            m = RE_SPAN.search(txt)
            if not m:
                sys.stderr.write(txt)
                raise SystemExit("no 'kernel span' in host output")
            spans.append(float(m.group(1)))

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
            design=a.design, sparsity=lab, clock_mhz=a.clock, shape=p["name"],
            M_rows=p["M"], N_cols=p["N"], nwin=p["nwin"],
            beats_per_matrix=p["beats_1"], batch_R=p["R"], total_beats=p["beats"],
            batched_latency_us=round(mean, 3),
            launch_overhead_us=ovh,
            latency_per_matrix_us=round(per, 4),
            ideal_per_matrix_us=round(ideal, 4),
            dsp_occupancy=round(ideal / per, 4),
            GFLOPS_effective=round(gf, 2),
            GFLOPS_actual=round(2.0 * p["beats_1"] * MACS_PER_BEAT / (per * 1000.0), 2),
            bandwidth_GBs=round(bw, 2),
            bytes_in_per_matrix=int(bytes_in),
            spread_pct=round(100.0 * (max(spans) - min(spans)) / min(spans), 3),
            runs=" ".join("%.3f" % x for x in spans)))
        print("  %-5s %5dx%-5d  per-matrix %9.2f us  %8.1f GFLOPS  %7.2f GB/s  "
              "occ %.3f" % (p["name"], p["M"], p["N"], per, gf, bw,
                            ideal / per))

    cols = ["design", "sparsity", "clock_mhz", "shape", "M_rows", "N_cols", "nwin",
            "beats_per_matrix", "batch_R", "total_beats", "batched_latency_us",
            "launch_overhead_us", "latency_per_matrix_us", "ideal_per_matrix_us",
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

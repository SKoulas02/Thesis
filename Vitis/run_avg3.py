#!/usr/bin/env python3
"""Measure every configuration of one design at one clock, N runs each, averaged.

WHY THIS EXISTS. The workbook currently mixes three estimators: sweep_fit
(marginal, launch overhead removed by a least-squares fit over four matrix
sizes), power_soak (sustained over 60 s, overhead included) and the mixed-run
mean. They disagree by 5-6% for identical hardware, so any chart that crosses
them invents a difference that is not there.

This script produces ONE estimator for everything: the mean of N single runs at
a fixed matrix size, which is exactly what the mixed-sparsity run already used.
Run it for sparse@300, sparse@325, dense@300, dense@325 and every number in the
comparison becomes directly commensurable -- including the mixed run.

IDENTICAL BEAT RANGE IN EVERY CONFIGURATION. Each configuration is sized to move
the same 2,097,152 weight beats, which is the convention the sweeps already
used. The ROW counts therefore differ by design -- that IS the result. At 2:32
the same beats produce 16x the rows.

    dense   4096 laps x 512 beats/lap
    2:4     8192 laps x 256
    2:8    16384 laps x 128
    2:16   32768 laps x  64
    2:32   65536 laps x  32

The measured quantity is the kernel span: earliest CU start to latest CU end,
from device-side OpenCL counters. It excludes host setup, xclbin load, the H2D
staging write and the D2H readback -- i.e. exactly "first byte out of HBM to
last result byte into HBM". H2D alone is 7-10x the compute, so including it
would measure PCIe rather than either architecture.

Python 3.6 compatible (the server runs 3.6).

Usage
-----
  sparse @ 300:
    python3 run_avg3.py --design sparse \\
        --host ~/GEMV_Sparse/Vitis/host_sparse \\
        --xclbin ~/GEMV_Sparse/Vitis_slr/gemv_sparse_slr.xclbin \\
        --emu ~/GEMV_Sparse/GEMV_4.0_Source/Emulation \\
        --clock 300 --csv sparse_avg3_300MHz.csv

  dense @ 325:
    python3 run_avg3.py --design dense \\
        --host ~/GEMV_Dense/Vitis/host \\
        --xclbin ~/GEMV_Dense/Vitis_325/gemv_dense_325.xclbin \\
        --emu ~/GEMV_Dense/GEMV_Dense_Source/Emulation \\
        --clock 325 --csv dense_avg3_325MHz.csv
"""

from __future__ import print_function

import argparse
import csv
import os
import re
import subprocess
import sys

V_ELEMS = 1024                     # matrix columns; --nwin 32 -> V = 1024
TARGET_BEATS = 2097152             # same weight-beat count in every UNIFORM config

# label -> (extra generator args, weight beats, LAPS)
#
# The four uniform sparsities each move an identical 2,097,152 weight beats, so
# launch overhead is amortised the same way in all of them. Their ROW counts
# differ by 16x -- that is the result, not a confound.
#
# LAPS, NOT ROWS, ARE FIXED HERE. A lap is nwin x freeze weight beats whatever the
# engine shape, so the SAME lap counts give the SAME weight beats on every family
# configuration -- and rows = laps x (cores x blocks). These used to be written as
# row counts (laps x 64), which silently overstated every 4x4 row count, Mrow/s and
# GFLOPS figure by 4x. At the 8x8 default the numbers are exactly the old ones.
PLAN_SPARSE = [
    ("2:4",  ["--sparsity", "00", "--nlaps", "8192"],  2097152,  8192),
    ("2:8",  ["--sparsity", "01", "--nlaps", "16384"], 2097152, 16384),
    ("2:16", ["--sparsity", "10", "--nlaps", "32768"], 2097152, 32768),
    ("2:32", ["--sparsity", "11", "--nlaps", "65536"], 2097152, 65536),
    # ---- and once with ALL FOUR in a single matrix -----------------------
    # Four EQUAL-ROW quarters, 4096 laps each. Equal rows is what "split the
    # matrix in 4 parts" means, and it cannot also come to exactly 2,097,152
    # beats -- that would need 4369.07 laps per segment. So this one config moves
    # 1,966,080 beats, 6.3% fewer. Reported explicitly rather than hidden; every
    # rate below is per-beat or per-row, so nothing is distorted.
    ("MIXED", ["--mix", "00:4096,01:4096,10:4096,11:4096"], 1966080, 16384),
]
PLAN_DENSE = [("dense", ["--nlaps", "4096"], 2097152, 4096)]

RE_SPAN = re.compile(r"kernel span\s*:\s*([0-9.]+)\s*us")
# "1 lap(s) -> 1 output beats = 48 rows" -- the HOST's own row count, from its own
# LANES constant. Compared against ours before any number is kept: a host built
# for a different shape than --cores/--blocks describes is caught here.
RE_HOST_ROWS = re.compile(r"(\d+)\s+output beats\s*=\s*(\d+)\s+rows")
RE_HOST_BEATS = re.compile(r"stimulus:\s*(\d+)\s+weight beats")
RE_BW = re.compile(r"HBM bandwidth\s*:\s*([0-9.]+)\s*GB/s")
RE_BPC = re.compile(r"beats/cycle\s*:\s*([0-9.]+)")


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
    ap.add_argument("--host", required=True, help="path to host_sparse or host")
    ap.add_argument("--xclbin", required=True)
    ap.add_argument("--emu", required=True, help="Emulation dir (holds the generator)")
    ap.add_argument("--clock", type=float, required=True,
                    help="the clock the xclbin was LINKED at -- passed to the host")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--nwin", type=int, default=32)
    ap.add_argument("--cores", type=int, default=8,
                    help="engine CORES_NUM of this xclbin (sparse only)")
    ap.add_argument("--blocks", type=int, default=8,
                    help="engine BLOCKS_NUM of this xclbin (sparse only)")
    ap.add_argument("--csv", required=True)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and exit -- no card, no XRT needed")
    a = ap.parse_args()
    if a.design == "dense" and (a.cores, a.blocks) != (8, 8):
        raise SystemExit("--cores/--blocks apply to the sparse engine only")
    lanes = a.cores * a.blocks
    macs_per_beat = 2 * lanes          # cores x blocks x 2 multipliers, per weight beat
    config = "{}x{}".format(a.cores, a.blocks)
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

    base = PLAN_SPARSE if a.design == "sparse" else PLAN_DENSE
    plan = [(label, ga, beats, laps * lanes) for label, ga, beats, laps in base]
    print("{} {} @ {:.0f} MHz -- {} configuration(s), {} runs each, averaged"
          .format(a.design, config, a.clock, len(plan), a.reps))
    print("   {} rows per lap, {} MACs per weight beat".format(lanes, macs_per_beat))
    for label, _ga, beats, rows_out in plan:
        flag = ""
        if label == "MIXED":
            flag = "   <- all four sparsities in ONE matrix"
        elif beats != TARGET_BEATS:
            flag = "   <- NOTE: beat count differs from the rest"
        print("   {:<6} {:>10,} beats  {:>10,} rows{}".format(
            label, beats, rows_out, flag))
    print("")
    if a.dry_run:
        print("dry run -- nothing generated, nothing run")
        return

    out_rows = []
    for label, genargs, beats, rows_out in plan:
        cmd = [sys.executable, gen, "--nwin", str(a.nwin)] + genargs
        if a.design == "sparse":
            cmd += ["--cores", str(a.cores), "--blocks", str(a.blocks)]
        run(cmd, "stimulus generation for " + label)

        spans, bws, bpcs = [], [], []
        for i in range(a.reps):
            txt = run([host, xclbin, emu, str(a.clock)], "host run for " + label)
            hr = RE_HOST_ROWS.search(txt)
            if hr and int(hr.group(2)) != rows_out:
                sys.stderr.write(txt)
                raise SystemExit(
                    "{}: the host reports {} rows but {} x {} laps predicts {}. The "
                    "host binary was built for a different shape than --cores {} "
                    "--blocks {} -- wrong host for this xclbin, or wrong flags."
                    .format(label, hr.group(2), lanes, rows_out // lanes, rows_out,
                            a.cores, a.blocks))
            hb = RE_HOST_BEATS.search(txt)
            if hb and int(hb.group(1)) != beats:
                sys.stderr.write(txt)
                raise SystemExit("{}: the host reports {} weight beats, the plan says {}"
                                 .format(label, hb.group(1), beats))
            m = RE_SPAN.search(txt)
            if not m:
                sys.stderr.write(txt)
                raise SystemExit("could not find 'kernel span' in the host output")
            spans.append(float(m.group(1)))
            mb, mc = RE_BW.search(txt), RE_BPC.search(txt)
            if mb:
                bws.append(float(mb.group(1)))
            if mc:
                bpcs.append(float(mc.group(1)))
            print("  {:<6} run {}/{}  span {:10.3f} us".format(
                label, i + 1, a.reps, spans[-1]))

        mean = sum(spans) / len(spans)
        best, worst = min(spans), max(spans)
        # EFFECTIVE: the M x N GEMV the user received, zeros included -- they
        # never had to be computed but the workload still delivered them.
        gf_eff = 2.0 * rows_out * V_ELEMS / (mean * 1000.0)
        # ACTUAL: the multiplies the silicon really performed. beats x (2 x cores x
        # blocks) holds for every sparsity including MIXED, because the engine
        # issues that many MACs per weight beat whatever the sparsity. (128 at 8x8.)
        gf_act = 2.0 * beats * macs_per_beat / (mean * 1000.0)
        out_rows.append(dict(
            design=a.design, config=config, cores=a.cores, blocks=a.blocks,
            lanes=lanes, macs_per_beat=macs_per_beat,
            clock_mhz=a.clock, sparsity=label,
            estimator="run_avg{}".format(a.reps),
            M_rows=rows_out, N_cols=V_ELEMS, weight_beats=beats,
            beats_per_row=round(beats / float(rows_out), 4),
            latency_avg_us=round(mean, 3), latency_best_us=round(best, 3),
            latency_worst_us=round(worst, 3),
            spread_pct=round(100.0 * (worst - best) / best, 3),
            Mrow_s=round(rows_out / mean, 3),
            bandwidth_avg_GBs=(round(sum(bws) / len(bws), 3) if bws else ""),
            beats_per_cycle_raw=(round(sum(bpcs) / len(bpcs), 4) if bpcs else ""),
            GFLOPS_effective=round(gf_eff, 2),
            GFLOPS_actual=round(gf_act, 2),
            runs=" ".join("{:.3f}".format(x) for x in spans)))
        print("  {:<6} MEAN {:10.3f} us   best {:10.3f}   spread {:.3f}%   "
              "{:8.1f} GFLOPS\n".format(label, mean, best,
                                        100.0 * (worst - best) / best, gf_eff))

    cols = ["design", "config", "cores", "blocks", "lanes", "macs_per_beat",
            "clock_mhz", "sparsity", "estimator", "M_rows", "N_cols",
            "weight_beats", "beats_per_row", "latency_avg_us",
            "latency_best_us", "latency_worst_us", "spread_pct", "Mrow_s",
            "bandwidth_avg_GBs", "beats_per_cycle_raw", "GFLOPS_effective",
            "GFLOPS_actual", "runs"]
    with open(a.csv, "w") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        for r in out_rows:
            w.writerow(r)
    print("CSV written: {}".format(a.csv))
    print("\nNOTE the printed beats/cycle is SINGLE-SHOT and still contains launch\n"
          "overhead, so it reads lower than the fit-derived values elsewhere. That\n"
          "is expected; do not put the two side by side without saying so.")


if __name__ == "__main__":
    main()

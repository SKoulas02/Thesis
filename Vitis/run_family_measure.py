#!/usr/bin/env python3
"""Measure one family configuration on the card: throughput, power, and shapes.

    source ~/setup_vitis.sh
    cd ~/GEMV_Sparse/Vitis
    python3 run_family_measure.py 4x4 --dry-run     # check paths and the plan first
    python3 run_family_measure.py 4x4               # ~40 min; run inside tmux

The same campaign that produced the 8x8 @ 325 MHz dataset, applied to any of the six
family winners, with every per-configuration assumption made explicit:

  1. THROUGHPUT  run_avg3.py     2:4, 2:8, 2:16, 2:32 and MIXED, N runs each, averaged.
                                 Same weight-beat counts on every configuration, so
                                 rows (and Mrow/s) scale with cores x blocks.
  2. POWER       measure_power.py  one 60 s soak per sparsity + MIXED. The stimulus is
                                 REGENERATED before every soak and the host's row count
                                 is checked, because bin/ is shared mutable state.
  3. SHAPES      run_shapes.py   the 11-shape sweep per sparsity + MIXED, with launch
                                 overhead MEASURED for this xclbin (--overhead auto),
                                 never borrowed from the 8x8 table.

CONSISTENCY ACROSS THE FAMILY. All six configurations go through this script with the
same reps, soak length, beat counts and overhead method -- including 8x8, whose
earlier 325 MHz shapes used the 8x8 @ 300 MHz overhead table. For the family dataset
re-run 8x8 here too, so every row was produced the same way.

Every number is guarded by a row-count check: the stimulus generator, run_avg3,
run_shapes and measure_power all compare the host's own row count (from the LANES
constant it was compiled with) against cores x blocks x laps. A host built for the
wrong shape, or a stale bin/, stops the run before anything is recorded.

Python 3.6 compatible.
"""

from __future__ import print_function

import argparse
import datetime
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# tag -> shape, the clock it CLOSED at, its build dir, and candidate xclbin names
# (first that exists wins, so a renamed file is found either way).
CONFIGS = {
    "4x4":  dict(cores=4,  blocks=4,  clock=400, vitis="Vitis_4x4",
                 xclbins=["sparse_4x4_400.xclbin"]),
    "8x4":  dict(cores=8,  blocks=4,  clock=375, vitis="Vitis_8x4",
                 xclbins=["sparse_8x4_375.xclbin"]),
    "16x3": dict(cores=16, blocks=3,  clock=350, vitis="Vitis_16x3",
                 xclbins=["sparse_16x3_350.xclbin", "sparse_16x3_350_missed.xclbin"]),
    # the reused pre-campaign bitstream, driven by the campaign's 8x8 host build
    "8x8":  dict(cores=8,  blocks=8,  clock=325, vitis="Vitis_8x8",
                 xclbins=["../Vitis_325/gemv_sparse_325.xclbin", "sparse_8x8_325.xclbin"]),
    "4x24": dict(cores=4,  blocks=24, clock=300, vitis="Vitis_4x24",
                 xclbins=["sparse_4x24_300.xclbin"]),
    "4x32": dict(cores=4,  blocks=32, clock=250, vitis="Vitis_4x32",
                 xclbins=["sparse_4x32_250.xclbin"]),
}

# identical to run_avg3.py's PLAN_SPARSE: label, sparsity/mix args, laps, weight beats.
# Beats are passed to the soak's guard as well as rows: MIXED and 2:8 both have 16,384
# laps and so identical row counts -- only the beats (1,966,080 vs 2,097,152) differ.
POWER_PLAN = [
    ("2:4",   ["--sparsity", "00", "--nlaps", "8192"],  8192,  2097152),
    ("2:8",   ["--sparsity", "01", "--nlaps", "16384"], 16384, 2097152),
    ("2:16",  ["--sparsity", "10", "--nlaps", "32768"], 32768, 2097152),
    ("2:32",  ["--sparsity", "11", "--nlaps", "65536"], 65536, 2097152),
    ("MIXED", ["--mix", "00:4096,01:4096,10:4096,11:4096"], 16384, 1966080),
]
SHAPE_SPARSITIES = [("00", "2to4"), ("01", "2to8"), ("10", "2to16"),
                    ("11", "2to32"), ("mix", "MIXED")]


class Tee(object):
    def __init__(self, path):
        self.f = open(path, "a")

    def line(self, s=""):
        print(s)
        self.f.write(s + "\n")
        self.f.flush()


def step(log, title, cmd, dry):
    log.line("")
    log.line("=" * 78)
    log.line("{}  {}".format(datetime.datetime.now().strftime("%H:%M:%S"), title))
    log.line("  $ " + " ".join(cmd))
    log.line("=" * 78)
    if dry and "--dry-run" not in cmd:
        log.line("  (dry run: not executed)")
        return
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         universal_newlines=True)
    for line in p.stdout:
        log.line(line.rstrip("\n"))
    p.wait()
    if p.returncode != 0:
        log.line("!! FAILED (exit {}): {}".format(p.returncode, title))
        log.line("   stopping here -- nothing after this step was run")
        sys.exit(p.returncode)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config", choices=sorted(CONFIGS))
    ap.add_argument("--base", default=os.path.expanduser("~/GEMV_Sparse"))
    ap.add_argument("--steps", default="avg3,power,shapes",
                    help="comma list of avg3,power,shapes (default: all three)")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--soak", type=float, default=60.0)
    ap.add_argument("--xclbin", default=None, help="override the xclbin path")
    ap.add_argument("--host", default=None, help="override the host path")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    c = CONFIGS[a.config]
    C, B, clk = c["cores"], c["blocks"], c["clock"]
    lanes = C * B
    vdir = os.path.join(a.base, c["vitis"])
    emu = os.path.join(a.base, "GEMV_4.0_Source", "Emulation")
    host = a.host or os.path.join(vdir, "host_sparse")
    if a.xclbin:
        xclbin = a.xclbin
    else:
        cands = [os.path.normpath(os.path.join(vdir, x)) for x in c["xclbins"]]
        found = [x for x in cands if os.path.exists(x)]
        xclbin = found[0] if found else cands[0]
    out = os.path.join(a.base, "family_measurements", a.config)
    steps = [s.strip() for s in a.steps.split(",") if s.strip()]
    bad = [s for s in steps if s not in ("avg3", "power", "shapes")]
    if bad:
        raise SystemExit("unknown step(s): " + ", ".join(bad))

    missing = [(w, p) for w, p in (("host", host), ("xclbin", xclbin),
                                   ("emulation dir", emu),
                                   ("run_avg3.py", os.path.join(HERE, "run_avg3.py")),
                                   ("run_shapes.py", os.path.join(HERE, "run_shapes.py")),
                                   ("measure_power.py", os.path.join(HERE, "measure_power.py")))
               if not os.path.exists(p)]
    if missing and not a.dry_run:
        for w, p in missing:
            print("MISSING {}: {}".format(w, p))
        raise SystemExit("fix the paths above (or pass --xclbin / --host)")
    if not a.dry_run and not os.environ.get("XILINX_XRT"):
        raise SystemExit("XILINX_XRT is not set -- source ~/setup_vitis.sh first")

    if not os.path.isdir(out):
        os.makedirs(out)
    log = Tee(os.path.join(out, "run_{}.log".format(
        datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))))

    log.line("family measurement: {} ({} cores x {} blocks = {} rows/lap) @ {} MHz"
             .format(a.config, C, B, lanes, clk))
    log.line("  host    : " + host)
    log.line("  xclbin  : " + xclbin)
    log.line("  emu     : " + emu)
    log.line("  output  : " + out)
    log.line("  steps   : {}   reps {}   soak {:.0f} s".format(",".join(steps), a.reps, a.soak))
    for w, p in missing:
        log.line("  MISSING {}: {}".format(w, p))

    py = sys.executable
    shape = ["--cores", str(C), "--blocks", str(B)]
    common = ["--host", host, "--xclbin", xclbin, "--emu", emu]
    tag = "{}_{}MHz".format(a.config, clk)

    if "avg3" in steps:
        step(log, "THROUGHPUT -- run_avg3, 4 sparsities + MIXED",
             [py, os.path.join(HERE, "run_avg3.py"), "--design", "sparse"] + common +
             ["--clock", str(clk), "--reps", str(a.reps)] + shape +
             ["--csv", os.path.join(out, "avg3_{}.csv".format(tag))] +
             (["--dry-run"] if a.dry_run else []), a.dry_run)

    if "power" in steps:
        pcsv = os.path.join(out, "power_{}.csv".format(tag))
        for label, gargs, laps, beats in POWER_PLAN:
            step(log, "POWER -- {} {} ({:.0f} s soak)".format(a.config, label, a.soak),
                 [py, os.path.join(HERE, "measure_power.py")] + common +
                 ["--clock-mhz", str(clk), "--label", "{} {}".format(a.config, label),
                  "--soak", str(a.soak), "--csv", pcsv,
                  "--gen-args", " ".join(shape + gargs + ["--nwin", "32"]),
                  "--expect-rows", str(laps * lanes),
                  "--expect-beats", str(beats)], a.dry_run)

    if "shapes" in steps:
        for sp, lab in SHAPE_SPARSITIES:
            step(log, "SHAPES -- {} {}".format(a.config, lab),
                 [py, os.path.join(HERE, "run_shapes.py"), "--design", "sparse",
                  "--sparsity", sp] + common +
                 ["--clock", str(clk), "--reps", str(a.reps), "--overhead", "auto"] +
                 shape + ["--csv", os.path.join(out, "shapes_{}_{}.csv".format(tag, lab))] +
                 (["--dry-run"] if a.dry_run else []), a.dry_run)

    log.line("")
    log.line("done: {} -> {}".format(a.config, out))
    if not a.dry_run:
        for f in sorted(os.listdir(out)):
            if f.endswith(".csv"):
                log.line("  " + f)


if __name__ == "__main__":
    main()

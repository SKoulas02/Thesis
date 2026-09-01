"""Measure accelerator power under sustained load, and derive energy efficiency.

Python 3.6, stdlib only (plus power_scraper.py beside it). Run on the server.

METHOD -- and why it is not simply "run the host and read the wattmeter"
A single calculation takes 10-80 ms. `xbutil examine` takes about a SECOND to
execute. You cannot sample a 10 ms window with a 1 Hz instrument. And a plain
run is ~85% H2D transfer anyway (74.7 ms of DMA against a 10.2 ms kernel at the
largest size), so most samples would land on the DMA engine rather than on
compute.

So the host is asked to SOAK: re-enqueue all 17 CUs back-to-back for N seconds
with the buffers already resident on the card -- no transfers, no setup, just
compute and HBM traffic. It prints the window as epoch seconds:

    SOAK_START_EPOCH 1756...
    SOAK_END_EPOCH   1756...

and this script clips its samples to exactly that window. Everything outside it
(process startup, xclbin load, H2D, read-back) is discarded rather than averaged
in.

IDLE BASELINE comes free: after the host exits, the bitstream stays programmed,
so sampling for a further --idle-after seconds gives idle-power FOR THE SAME
DESIGN. That is the right baseline -- it holds leakage, clock trees and the
platform constant, so the difference isolates the power of doing the work.

WHAT IS REPORTED
  board  W  the two 12 V input rails: FPGA + HBM + transceivers + regulator
            losses + fans. What the machine actually draws.
  VCCINT W  the FPGA core rail, downstream of those regulators -- a SUBSET of
            board power, never additive. HBM and transceiver draw are nearly
            identical between dense and sparse, so a design difference shows up
            far more clearly here.
Both are given absolute and as a delta over that design's own idle.

ENERGY EFFICIENCY is the point of the exercise. Sustained throughput comes from
the soak itself (the host prints Mrow/s measured over the same window as the
power), so watts and rows are measured on the same work at the same time.

CAVEATS THAT MUST TRAVEL WITH THE NUMBERS
  * ~1 Hz sampling measures STEADY STATE only; nothing transient is visible.
  * THE CARD'S TELEMETRY LAGS THE LOAD. Measured on a 5 s trial: samples taken
    just after the soak began still read below the idle mean, because the
    satellite controller polls its sensors slowly. --warmup discards the first
    few seconds of the load window for this reason. Do not set it to 0.
  * The dynamic delta (~3 W board) is SMALLER than the sample-to-sample spread
    (~4 W). The mean is still sound -- standard error falls as 1/sqrt(n), so 60
    samples brings it to a few hundred mW -- but the standard error is reported
    alongside every figure and should be quoted with it.
  * The card is shared. Another user's kernel only ever ADDS watts, and unlike
    throughput you cannot take best-of-N to filter it out. Check the CUs are
    idle first.
  * Board power includes fans, which are thermally controlled and drift.

USAGE
    python3 measure_power.py --host ./host_sparse --xclbin ./gemv_sparse.xclbin \\
        --emu ~/GEMV_Sparse/GEMV_4.0_Source/Emulation --clock-mhz 225 \\
        --label "sparse 2:4" --soak 60 --csv power_results.csv

Rows append, so run it once per configuration into the same CSV.
"""

import argparse
import csv
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from power_scraper import Sampler, summarise, read_once   # noqa: E402

RE_START = re.compile(r"SOAK_START_EPOCH\s+([0-9.]+)")
RE_END = re.compile(r"SOAK_END_EPOCH\s+([0-9.]+)")
RE_SUSTAINED = re.compile(r"([0-9.]+)\s+Mrow/s sustained")
RE_ITERS = re.compile(r"soak:\s+(\d+)\s+consecutive")

CSV_COLS = ["label", "clock_mhz", "V", "soak_s", "iterations", "Mrow_s_sustained",
            "board_load_w", "board_idle_w", "board_delta_w",
            "vccint_load_w", "vccint_idle_w", "vccint_delta_w", "vccint_load_a",
            "board_load_min", "board_load_max", "board_load_sd", "board_delta_se",
            "vccint_delta_se", "n_load", "n_idle",
            "energy_per_row_nJ", "energy_per_MAC_pJ", "GMAC_per_W",
            "energy_per_row_nJ_vccint", "sample_errors"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--xclbin", required=True)
    ap.add_argument("--emu", required=True)
    ap.add_argument("--clock-mhz", type=float, required=True,
                    help="what the xclbin was LINKED at -- sparse 225, dense 300")
    ap.add_argument("--label", required=True, help='e.g. "sparse 2:4" or "dense"')
    ap.add_argument("--soak", type=float, default=60.0,
                    help="seconds of sustained load (professor's spec: 60)")
    ap.add_argument("--idle-after", type=float, default=20.0,
                    help="seconds of idle sampling after the host exits, for the baseline")
    ap.add_argument("--warmup", type=float, default=6.0,
                    help="seconds to discard at the START of the load window. The card's "
                         "power telemetry lags the actual load, so early samples still "
                         "read idle. Do not set to 0.")
    ap.add_argument("--settle", type=float, default=8.0,
                    help="seconds to discard after the host exits before idle sampling "
                         "-- the card does not drop to idle instantly")
    ap.add_argument("--interval", type=float, default=1.0, help="sampling period (s)")
    ap.add_argument("--bdf", default="0000:af:00.1")
    ap.add_argument("--V", type=int, default=1024, help="vector length, for MAC accounting")
    ap.add_argument("--csv", default="power_results.csv")
    a = ap.parse_args()

    print("")
    print("power measurement: {}".format(a.label))
    print("  xclbin   : {}".format(a.xclbin))
    print("  clock    : {:.0f} MHz".format(a.clock_mhz))
    print("  soak     : {:.0f} s load + {:.0f} s idle baseline".format(a.soak, a.idle_after))
    print("  device   : {}".format(a.bdf))

    # A reading before we touch anything -- proves telemetry works and shows what
    # the card was doing beforehand (someone else's kernel would show up here).
    try:
        pre = read_once(a.bdf)
    except Exception as e:
        raise SystemExit("cannot read telemetry: {}".format(e))
    print("  pre-run  : board {:.2f} W, VCCINT {:.2f} W".format(
        pre["board_w"], pre["vccint_w"] or 0.0))
    if not pre["board_consistent"]:
        print("  WARNING: the 12 V rails do not reconstruct the reported board power; "
              "the report format may have changed -- check power_scraper.py")

    sampler = Sampler(a.bdf, interval=a.interval)
    sampler.start()

    cmd = [a.host, a.xclbin, a.emu, str(a.clock_mhz), str(a.soak)]
    print("  running  : {}".format(" ".join(cmd)))
    t_launch = time.time()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         universal_newlines=True)
    out, _ = p.communicate()
    t_exit = time.time()
    if p.returncode != 0:
        sampler.stop()
        sys.stderr.write(out)
        raise SystemExit("host failed (code {})".format(p.returncode))

    m0, m1 = RE_START.search(out), RE_END.search(out)
    if not (m0 and m1):
        sampler.stop()
        sys.stderr.write(out)
        raise SystemExit("host printed no SOAK window -- was it built with the "
                         "[soak_seconds] argument, and was --soak > 0?")
    t0, t1 = float(m0.group(1)), float(m1.group(1))

    ms = RE_SUSTAINED.search(out)
    sustained = float(ms.group(1)) if ms else None
    mi = RE_ITERS.search(out)
    iters = int(mi.group(1)) if mi else 0

    print("  soak     : {} iterations, {:.1f} s, {} Mrow/s sustained".format(
        iters, t1 - t0, "{:.3f}".format(sustained) if sustained else "?"))

    # Idle baseline: bitstream still programmed, nothing running.
    print("  idle     : settling {:.0f} s, then sampling {:.0f} s...".format(
        a.settle, a.idle_after))
    time.sleep(a.settle + a.idle_after)
    sampler.stop()

    # Discard the warm-up: the sensors have not caught up with the load yet.
    if t1 - t0 <= a.warmup:
        print("  WARNING: soak ({:.1f} s) is not longer than warmup ({:.1f} s); "
              "using the whole window.".format(t1 - t0, a.warmup))
        load = summarise(sampler.window(t0, t1))
        load_t0 = t0
    else:
        load_t0 = t0 + a.warmup
        load = summarise(sampler.window(load_t0, t1))
    idle = summarise(sampler.window(t_exit + a.settle, time.time()))
    if not load:
        raise SystemExit("no samples inside the load window -- is --interval shorter "
                         "than --soak? xbutil needs ~1 s per reading.")
    if not idle:
        raise SystemExit("no samples in the idle window -- raise --idle-after.")

    b_load, b_idle = load["board_mean"], idle["board_mean"]
    v_load = load.get("vccint_mean")
    v_idle = idle.get("vccint_mean")

    row = {
        "label": a.label, "clock_mhz": a.clock_mhz, "V": a.V, "soak_s": a.soak,
        "iterations": iters, "Mrow_s_sustained": round(sustained, 4) if sustained else "",
        "board_load_w": round(b_load, 3), "board_idle_w": round(b_idle, 3),
        "board_delta_w": round(b_load - b_idle, 3),
        "vccint_load_w": round(v_load, 3) if v_load else "",
        "vccint_idle_w": round(v_idle, 3) if v_idle else "",
        "vccint_delta_w": round(v_load - v_idle, 3) if (v_load and v_idle) else "",
        "vccint_load_a": round(load.get("vccint_a_mean", 0), 3),
        "board_load_min": round(load["board_min"], 3),
        "board_load_max": round(load["board_max"], 3),
        "board_load_sd": round(load.get("board_sd", 0), 3),
        "n_load": load["n"], "n_idle": idle["n"],
        "sample_errors": sampler.errors,
    }

    if sustained:
        rows_per_s = sustained * 1e6
        macs_per_s = rows_per_s * a.V          # dense-equivalent work
        row["energy_per_row_nJ"] = round(b_load / rows_per_s * 1e9, 3)
        row["energy_per_MAC_pJ"] = round(b_load / macs_per_s * 1e12, 4)
        row["GMAC_per_W"] = round(macs_per_s / 1e9 / b_load, 4)
        if v_load:
            row["energy_per_row_nJ_vccint"] = round(v_load / rows_per_s * 1e9, 3)

    # Delta uncertainty: two independent means, so errors add in quadrature.
    b_err = (load.get("board_se", 0) ** 2 + idle.get("board_se", 0) ** 2) ** 0.5
    v_err = (load.get("vccint_se", 0) ** 2 + idle.get("vccint_se", 0) ** 2) ** 0.5
    row["board_delta_se"] = round(b_err, 3)
    row["vccint_delta_se"] = round(v_err, 3)

    print("")
    print("  (load window trimmed by {:.0f} s of warm-up -- sensors lag the load)".format(
        a.warmup if t1 - t0 > a.warmup else 0.0))
    print("  {:<22} {:>10} {:>10} {:>14}".format("", "load", "idle", "delta"))
    print("  {:<22} {:>10.2f} {:>10.2f} {:>8.2f} +/-{:.2f}".format(
        "board (12 V rails) W", b_load, b_idle, b_load - b_idle, b_err))
    if v_load and v_idle:
        print("  {:<22} {:>10.2f} {:>10.2f} {:>8.2f} +/-{:.2f}".format(
            "VCCINT (FPGA core) W", v_load, v_idle, v_load - v_idle, v_err))
    print("  {:<22} {:>10} {:>10}".format("samples", load["n"], idle["n"]))
    print("  board sd under load    : {:.2f} W  (range {:.2f} - {:.2f})".format(
        load.get("board_sd", 0), load["board_min"], load["board_max"]))
    if load["n"] < 20:
        print("  WARNING: only {} load samples. The professor's 60 s soak gives ~55 "
              "after warm-up; fewer than ~20 makes the mean shaky.".format(load["n"]))
    if sustained:
        print("")
        print("  sustained throughput   : {:.3f} Mrow/s".format(sustained))
        print("  energy per row         : {:.1f} nJ   (board)".format(row["energy_per_row_nJ"]))
        if "energy_per_row_nJ_vccint" in row:
            print("                           {:.1f} nJ   (VCCINT only)".format(
                row["energy_per_row_nJ_vccint"]))
        print("  energy per MAC         : {:.3f} pJ   (dense-equivalent, V={})".format(
            row["energy_per_MAC_pJ"], a.V))
        print("  efficiency             : {:.2f} GMAC/s/W".format(row["GMAC_per_W"]))
    if sampler.errors:
        print("  NOTE: {} telemetry reads failed and were skipped".format(sampler.errors))

    exists = os.path.exists(a.csv)
    with open(a.csv, "a") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction="ignore")
        if not exists:
            w.writeheader()
        w.writerow(row)
    print("")
    print("  appended to {}".format(a.csv))


if __name__ == "__main__":
    main()

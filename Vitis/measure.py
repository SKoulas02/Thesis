"""S22 measurement driver -- sweeps problem size, runs the host, emits CSV.

Python 3.6, stdlib only. Run on the server.

WHAT PROBLEM THIS SOLVES
A single ./host run is dominated by launch overhead: ~300-600 us of XRT
scheduling around a workload that may be only microseconds. Two identical runs
measured 630 us and 348 us -- a 2x spread on identical work. No throughput
number from that is publishable.

Two fixes, both applied here:

 1. BIG PROBLEMS. Sizes large enough that compute dominates. At nlaps=4096,
    nwin=32 the ideal kernel time is ~7 ms against ~0.5 ms of overhead.

 2. THE DIFFERENTIAL METHOD. Fixed overhead appears identically in every run,
    so it cancels in a difference:

        marginal throughput = (t_big - t_small) / (beats_big - beats_small)

    That number is free of launch cost by construction, which matters more than
    any amount of averaging -- averaging reduces noise but not bias.

Both are reported: the raw per-size figures (which still include overhead, and
are what a naive measurement would give) and the differential (which does not).
The gap between them is itself worth showing in the thesis.

Each size is run --reps times and the BEST kernel span is kept. Best, not mean:
the machine is shared, so slow samples are contention, not the design.

USAGE
    python3 measure.py --host ~/GEMV_Dense/Vitis/host \
                       --xclbin ~/GEMV_Dense/Vitis/gemv_dense.xclbin \
                       --emu ~/GEMV_Dense/GEMV_Dense_Source/Emulation \
                       --nwin 32 --laps 512 1024 2048 4096 --reps 5 \
                       --csv dense_300MHz.csv

OUTPUT: a table on screen and a CSV ready to open in Excel.

NOTE: this uses gen_timing_stimulus.py, which writes NO golden.txt. Correctness
is established separately by gemv_dense_gen.py + compare_dense_py36.py on the
small cases. Never quote a number from here without that having passed.
"""

import argparse
import csv
import os
import re
import subprocess
import sys

BEATS_PER_WINDOW = 16
PC_BYTES = 32
W_PCS, A_PCS, C_PCS = 8, 2, 4
LANES = 64

RE_SPAN = re.compile(r"kernel span\s*:\s*([0-9.]+)\s*us")
RE_H2D = re.compile(r"H2D transfer\s*:\s*([0-9.]+)\s*us")
RE_D2H = re.compile(r"D2H transfer\s*:\s*([0-9.]+)\s*us")


def run(cmd):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out, _ = p.communicate()
    txt = out.decode("utf-8", "replace")
    if p.returncode != 0:
        sys.stderr.write(txt)
        raise SystemExit("command failed: {}".format(" ".join(cmd)))
    return txt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--xclbin", required=True)
    ap.add_argument("--emu", required=True)
    ap.add_argument("--nwin", type=int, default=32)
    ap.add_argument("--laps", type=int, nargs="+", required=True)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--csv", default="measurements.csv")
    ap.add_argument("--freq-mhz", type=float, default=300.0,
                    help="the clock the xclbin was LINKED at. Used for this script's "
                         "ideal-time maths AND passed to the host as argv[3], so the "
                         "host's own beats/cycle is right too. Dense closes 300, 325 and "
                         "350; pass whichever bitstream you are measuring.")
    a = ap.parse_args()

    gen = os.path.join(a.emu, "gen_timing_stimulus.py")
    beats_per_lap = a.nwin * BEATS_PER_WINDOW
    V = a.nwin * 32

    rows = []
    print("")
    print("V = {} elements, {} beats/lap, {} reps per size, best-of kept".format(
        V, beats_per_lap, a.reps))
    print("")
    print("{:>8} {:>12} {:>12} {:>10} {:>10} {:>10} {:>9}".format(
        "laps", "weight beats", "best us", "spread%", "Mrow/s", "GB/s", "beats/cyc"))
    print("-" * 78)

    for laps in a.laps:
        nwb = laps * beats_per_lap
        run([sys.executable, gen, "--nwin", str(a.nwin), "--nlaps", str(laps)])

        spans = []
        h2d = d2h = 0.0
        for r in range(a.reps):
            txt = run([a.host, a.xclbin, a.emu, str(a.freq_mhz)])
            m = RE_SPAN.search(txt)
            if not m:
                raise SystemExit("could not parse kernel span from host output")
            spans.append(float(m.group(1)))
            if r == 0:
                mh, md = RE_H2D.search(txt), RE_D2H.search(txt)
                h2d = float(mh.group(1)) if mh else 0.0
                d2h = float(md.group(1)) if md else 0.0

        best = min(spans)
        spread = (max(spans) - best) / best * 100.0
        in_bytes = (nwb * W_PCS + a.nwin * A_PCS) * PC_BYTES
        out_bytes = laps * C_PCS * PC_BYTES
        rows_out = laps * LANES
        ideal_us = nwb * (1000.0 / a.freq_mhz) / 1000.0

        mrow = rows_out / best          # rows/us = Mrow/s
        gbs = (in_bytes + out_bytes) / best / 1000.0
        bpc = ideal_us / best

        print("{:>8} {:>12} {:>12.1f} {:>9.1f}% {:>10.3f} {:>10.3f} {:>9.3f}".format(
            laps, nwb, best, spread, mrow, gbs, bpc))

        rows.append(dict(clock_mhz=a.freq_mhz, V=V, laps=laps, weight_beats=nwb, rows=rows_out,
                         best_us=round(best, 3), mean_us=round(sum(spans) / len(spans), 3),
                         worst_us=round(max(spans), 3), spread_pct=round(spread, 1),
                         reps=a.reps, ideal_us=round(ideal_us, 3),
                         bytes_in=in_bytes, bytes_out=out_bytes,
                         Mrow_s=round(mrow, 4), GB_s=round(gbs, 4),
                         beats_per_cycle=round(bpc, 4),
                         h2d_us=round(h2d, 3), d2h_us=round(d2h, 3)))

    # ---- differential: overhead cancels between adjacent sizes ----
    print("")
    print("DIFFERENTIAL (launch overhead cancels -- these are the defensible numbers)")
    print("")
    print("{:>18} {:>13} {:>12} {:>10} {:>10} {:>9}".format(
        "pair", "delta beats", "delta us", "Mrow/s", "GB/s", "beats/cyc"))
    print("-" * 76)

    for i in range(1, len(rows)):
        lo, hi = rows[i - 1], rows[i]
        db = hi["weight_beats"] - lo["weight_beats"]
        dt = hi["best_us"] - lo["best_us"]
        if db <= 0 or dt <= 0:
            print("{:>18} {:>13} {:>12}   -- non-monotonic, ignore".format(
                "{}->{}".format(lo["laps"], hi["laps"]), db, round(dt, 1)))
            continue
        drows = hi["rows"] - lo["rows"]
        dbytes = (hi["bytes_in"] + hi["bytes_out"]) - (lo["bytes_in"] + lo["bytes_out"])
        d_ideal = db * (1000.0 / a.freq_mhz) / 1000.0
        print("{:>18} {:>13} {:>12.1f} {:>10.3f} {:>10.3f} {:>9.3f}".format(
            "{}->{}".format(lo["laps"], hi["laps"]), db, dt,
            drows / dt, dbytes / dt / 1000.0, d_ideal / dt))
        rows.append(dict(clock_mhz=a.freq_mhz, V=V, laps="{}->{}".format(lo["laps"], hi["laps"]),
                         weight_beats=db, rows=drows, best_us=round(dt, 3),
                         ideal_us=round(d_ideal, 3),
                         bytes_in=hi["bytes_in"] - lo["bytes_in"],
                         bytes_out=hi["bytes_out"] - lo["bytes_out"],
                         Mrow_s=round(drows / dt, 4),
                         GB_s=round(dbytes / dt / 1000.0, 4),
                         beats_per_cycle=round(d_ideal / dt, 4)))

    # ---- least-squares fit: uses ALL sizes, not just adjacent pairs ----------
    # Model:  t(beats) = overhead + beats / rate
    # The SLOPE is the marginal cost per beat -- same quantity the differential
    # rows estimate, but from every point instead of two, so a single unlucky
    # sample moves it much less.
    # The INTERCEPT is the fixed launch overhead: the time a zero-beat run would
    # take. That is the quantity that made the early small-case measurements
    # meaningless, so it is worth reporting rather than merely subtracting.
    raw = [r for r in rows if isinstance(r["laps"], int)]
    if len(raw) >= 2:
        n = len(raw)
        xs = [float(r["weight_beats"]) for r in raw]
        ys = [float(r["best_us"]) for r in raw]
        mx = sum(xs) / n
        my = sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        slope = sxy / sxx                 # us per beat
        intercept = my - slope * mx       # us at zero beats = fixed overhead

        # R^2, so a bad fit is visible rather than silently quoted
        ss_tot = sum((y - my) ** 2 for y in ys)
        ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
        r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else float("nan")

        cyc_us = 1000.0 / a.freq_mhz      # ns->us per cycle at the kernel clock
        bpc_fit = (cyc_us / 1000.0) / slope if slope > 0 else float("nan")
        rows_per_lap = LANES
        beats_per_row = beats_per_lap / float(rows_per_lap)
        mrow_fit = 1.0 / (slope * beats_per_row) if slope > 0 else float("nan")
        bytes_per_beat = W_PCS * PC_BYTES + (C_PCS * PC_BYTES) / float(beats_per_lap)
        gbs_fit = bytes_per_beat / slope / 1000.0 if slope > 0 else float("nan")

        print("")
        print("LEAST-SQUARES FIT over all {} sizes:  t(us) = overhead + beats * slope".format(n))
        print("")
        print("  fixed launch overhead : {:>10.1f} us     <- intercept; the reason small".format(intercept))
        print("                                             cases measure nothing useful")
        print("  marginal cost per beat: {:>10.6f} us".format(slope))
        print("  marginal throughput   : {:>10.3f} Mrow/s".format(mrow_fit))
        print("  marginal bandwidth    : {:>10.3f} GB/s".format(gbs_fit))
        print("  beats/cycle           : {:>10.3f}      (1.000 = roofline at {:.0f} MHz)".format(
            bpc_fit, a.freq_mhz))
        print("  R^2                   : {:>10.5f}      (<0.999 means the fit is not"
              " trustworthy)".format(r2))

        rows.append(dict(clock_mhz=a.freq_mhz, V=V, laps="FIT", weight_beats="", rows="",
                         best_us=round(intercept, 3),      # overhead in the time column
                         ideal_us="", bytes_in="", bytes_out="",
                         Mrow_s=round(mrow_fit, 4), GB_s=round(gbs_fit, 4),
                         beats_per_cycle=round(bpc_fit, 4),
                         mean_us=round(slope, 8),          # slope, us/beat
                         worst_us=round(r2, 5),            # R^2
                         spread_pct="", reps=n, h2d_us="", d2h_us=""))

    cols = ["clock_mhz", "V", "laps", "weight_beats", "rows", "best_us", "mean_us", "worst_us",
            "spread_pct", "reps", "ideal_us", "bytes_in", "bytes_out",
            "Mrow_s", "GB_s", "beats_per_cycle", "h2d_us", "d2h_us"]
    with open(a.csv, "w") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("")
    print("CSV written: {}".format(a.csv))
    print("  laps=512        -> raw per-size measurement (still contains launch overhead)")
    print("  laps=512->1024  -> differential between adjacent sizes (overhead cancels)")
    print("  laps=FIT        -> least-squares over all sizes;")
    print("                     best_us column = FIXED LAUNCH OVERHEAD (intercept)")
    print("                     mean_us column = slope in us/beat")
    print("                     worst_us column = R^2")


if __name__ == "__main__":
    main()

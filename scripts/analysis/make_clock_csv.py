"""Build results/GEMV_Clock_300_vs_325.csv -- the two-clock comparison, chart-ready.

WHY THIS SHEET EXISTS. The shape sweep and the energy-per-element set were only
ever measured at 300 MHz, so every shape-based chart is a 300 MHz chart. But the
run_avg3 comparison and the power soaks were BOTH run at 300 and 325, which is
enough to say everything worth saying about clock. This sheet puts those side by
side, one row per configuration, so a grouped bar chart is a two-column
selection rather than a pivot.

WIDE FORMAT ON PURPOSE. One row per configuration, with _300 and _325 as
separate columns. Long format (a clock column) charts better in a pivot but
worse by hand, and everything downstream here is built by selecting columns.

  THE TWO ESTIMATORS ARE KEPT IN SEPARATE COLUMNS AND NAMED.

    *_avg3  latency / throughput / GFLOPS / DSP occupancy, from
            results/GEMV_avg3_results.csv -- mean of N single runs, launch
            overhead INCLUDED.
    *_soak  power / energy, from the 60 s soaks -- sustained, overhead
            amortised over ~8,000 iterations.

  They differ 5-6% for identical hardware. Charting an avg3 column against a
  soak column invents a difference that is not there. Within one estimator, and
  within one metric, the 300-vs-325 pair is exact.

WHICH POWER FILES. results/power_results.csv (300 MHz, 2026-08-31) and
results/power_results_325.csv (325 MHz, 2026-09-02) -- the matched pair,
measured two days apart by the same method. NOT power_results_0903.csv, which is
a later 300 MHz re-measure; mixing it in would pair a 2026-09-03 300 MHz number
against a 2026-09-02 325 MHz one for no reason.

  Neither of those files has a MIXED row -- that configuration did not exist when
  they were taken. power_mixed_both.csv (2026-09-05) supplies it at BOTH clocks,
  measured back to back on a settled card, so it is also the cleanest 300-vs-325
  pair in the corpus. It is listed LAST for each clock, so it only ever fills a
  gap and never displaces an established row.

  ⚠️ power_mixed_325.csv is NOT used here. Same configuration, valid soak, but
  taken while the card was still warm from a five-hour build: idle 30.88 W
  against 30.26 W settled, inflating its energy by 3.9% at statistically
  identical throughput (158.311 vs 158.930 Mrow/s). Kept as the evidence that
  ENERGY needs a settled card and THROUGHPUT does not.

The 300 MHz file carries TWO `sparse 2:4` rows -- replicate soaks. They are
AVERAGED, which is where the 35.44 W in the existing tables comes from.

Run:  python make_clock_csv.py
"""

import csv
import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "results")

IDEAL = 325.0 / 300.0          # 1.08333 -- the scaling a clock-limited design must show


def _data(name):
    p = os.path.join(DATA, name)
    return p if os.path.exists(p) else os.path.join(HERE, name)


AVG3 = "GEMV_avg3_results.csv"
# MIXED is not in either main soak file -- it was measured later, at BOTH clocks
# back to back on a settled card (2026-09-05), which makes power_mixed_both.csv
# the cleanest 300-vs-325 pair in the whole power corpus. It is listed second so
# the established rows win for the five configurations they cover.
PW300 = ["power_results.csv", "power_mixed_both.csv"]
PW325 = ["power_results_325.csv", "power_mixed_both.csv"]
OUT = os.path.join(DATA, "GEMV_Clock_300_vs_325.csv")

ORDER = ["dense", "2:4", "2:8", "2:16", "2:32", "MIXED"]
# soak label -> avg3 sparsity label
PWMAP = {"dense": "dense", "sparse 2:4": "2:4", "sparse 2:8": "2:8",
         "sparse 2:16": "2:16", "sparse 2:32": "2:32", "sparse MIXED": "MIXED"}


def read_power(paths, clock):
    """label -> averaged soak row, for one clock. Replicates AVERAGED, not last-wins.

    Files are tried in order and the FIRST file to supply a configuration wins,
    so a supplementary file can fill a gap without perturbing rows that are
    already established. Rows are filtered by clock because power_mixed_both.csv
    carries 300 and 325 in the same file.
    """
    acc = {}
    for path in paths:
        p = _data(path)
        if not os.path.exists(p):
            print("MISSING, skipped:", path)
            continue
        for r in csv.DictReader(io.open(p, newline="", encoding="utf-8")):
            cfg = PWMAP.get(r["label"])
            if cfg is None or abs(float(r["clock_mhz"]) - clock) > 0.5:
                continue
            if cfg in acc and acc[cfg][0]["_src"] != path:
                continue           # already supplied by an earlier file
            r = dict(r, _src=path)
            acc.setdefault(cfg, []).append(r)
    out = {}
    for cfg, rows in acc.items():
        n = float(len(rows))
        out[cfg] = {k: sum(float(x[k]) for x in rows) / n for k in
                    ("Mrow_s_sustained", "board_load_w", "board_idle_w",
                     "energy_per_row_nJ", "energy_per_row_nJ_vccint")}
        out[cfg]["n_soaks"] = int(n)
    return out


def main():
    a = {}
    for r in csv.DictReader(io.open(_data(AVG3), newline="", encoding="utf-8")):
        a[(r["sparsity"], int(float(r["clock_mhz"])))] = r

    p3, p5 = read_power(PW300, 300), read_power(PW325, 325)

    rows = []
    for i, cfg in enumerate(ORDER, start=1):
        r3, r5 = a.get((cfg, 300)), a.get((cfg, 325))
        if not r3 or not r5:
            print("SKIP (missing avg3 row):", cfg)
            continue
        q3, q5 = p3.get(cfg), p5.get(cfg)

        def f(row, key):
            v = (row or {}).get(key, "")
            return float(v) if v not in ("", None) else None

        mr3, mr5 = float(r3["Mrow_s"]), float(r5["Mrow_s"])
        d = dict(
            sparsity=cfg, sparsity_order=i,
            design=r3["design"],
            # ---- run_avg3 estimator ----
            latency_us_300_avg3=round(float(r3["latency_avg_us"]), 3),
            latency_us_325_avg3=round(float(r5["latency_avg_us"]), 3),
            Mrow_s_300_avg3=round(mr3, 3),
            Mrow_s_325_avg3=round(mr5, 3),
            GFLOPS_eff_300_avg3=float(r3["GFLOPS_effective"]),
            GFLOPS_eff_325_avg3=float(r5["GFLOPS_effective"]),
            GFLOPS_act_300_avg3=float(r3["GFLOPS_actual"]),
            GFLOPS_act_325_avg3=float(r5["GFLOPS_actual"]),
            speedup_vs_dense_300_avg3=float(r3["speedup_vs_dense"]),
            speedup_vs_dense_325_avg3=float(r5["speedup_vs_dense"]),
            dsp_occupancy_300_avg3=float(r3["dsp_occupancy_compute"]),
            dsp_occupancy_325_avg3=float(r5["dsp_occupancy_compute"]),
            # ---- clock scaling, avg3 vs avg3 ----
            # DILUTED BY LAUNCH OVERHEAD. avg3 latency includes a fixed
            # ~460-690 us launch cost that does NOT shrink when the clock rises,
            # so this ratio sits below ideal by roughly the overhead fraction --
            # worst at 2:32, which has both the largest overhead (685 us) and the
            # shortest compute. Use the _soak pair below for the scaling CLAIM.
            clock_scaling_avg3=round(mr5 / mr3, 4),
            clock_scaling_ideal=round(IDEAL, 4),
            pct_of_ideal_avg3=round(100.0 * (mr5 / mr3) / IDEAL, 2),
        )
        # ---- power_soak estimator; BLANK for MIXED at 325, never zero ----
        d["board_load_W_300_soak"] = round(q3["board_load_w"], 3) if q3 else ""
        d["board_load_W_325_soak"] = round(q5["board_load_w"], 3) if q5 else ""
        d["board_idle_W_300_soak"] = round(q3["board_idle_w"], 3) if q3 else ""
        d["board_idle_W_325_soak"] = round(q5["board_idle_w"], 3) if q5 else ""
        d["energy_nJ_per_row_300_soak"] = round(q3["energy_per_row_nJ"], 3) if q3 else ""
        d["energy_nJ_per_row_325_soak"] = round(q5["energy_per_row_nJ"], 3) if q5 else ""
        d["energy_nJ_per_row_300_vccint"] = round(q3["energy_per_row_nJ_vccint"], 3) if q3 else ""
        d["energy_nJ_per_row_325_vccint"] = round(q5["energy_per_row_nJ_vccint"], 3) if q5 else ""
        d["n_soaks_300"] = q3["n_soaks"] if q3 else ""
        d["n_soaks_325"] = q5["n_soaks"] if q5 else ""
        # ---- clock scaling, SOAK vs SOAK: the honest estimator for this claim.
        # A soak amortises the launch overhead over ~8,000 iterations, so what is
        # left is very nearly pure compute and the ratio is a clean test of
        # "throughput scales with clock". This is the pair that gives the 0.47%.
        d["Mrow_s_300_soak"] = round(q3["Mrow_s_sustained"], 3) if q3 else ""
        d["Mrow_s_325_soak"] = round(q5["Mrow_s_sustained"], 3) if q5 else ""
        if q3 and q5:
            s = q5["Mrow_s_sustained"] / q3["Mrow_s_sustained"]
            d["clock_scaling_soak"] = round(s, 4)
            d["pct_of_ideal_soak"] = round(100.0 * s / IDEAL, 2)
        else:
            d["clock_scaling_soak"] = ""
            d["pct_of_ideal_soak"] = ""
        rows.append(d)

    # energy ratio vs dense, computed per clock AFTER dense is known
    den = {r["sparsity"]: r for r in rows}["dense"]
    for r in rows:
        for tag in ("300", "325"):
            k = "energy_nJ_per_row_%s_soak" % tag
            r["energy_vs_dense_%s_soak" % tag] = (
                round(float(den[k]) / float(r[k]), 4)
                if (r[k] != "" and den[k] != "") else "")

    cols = ["sparsity", "sparsity_order", "design",
            "latency_us_300_avg3", "latency_us_325_avg3",
            "Mrow_s_300_avg3", "Mrow_s_325_avg3",
            "GFLOPS_eff_300_avg3", "GFLOPS_eff_325_avg3",
            "GFLOPS_act_300_avg3", "GFLOPS_act_325_avg3",
            "speedup_vs_dense_300_avg3", "speedup_vs_dense_325_avg3",
            "dsp_occupancy_300_avg3", "dsp_occupancy_325_avg3",
            "clock_scaling_avg3", "clock_scaling_ideal", "pct_of_ideal_avg3",
            "Mrow_s_300_soak", "Mrow_s_325_soak",
            "clock_scaling_soak", "pct_of_ideal_soak",
            "board_load_W_300_soak", "board_load_W_325_soak",
            "board_idle_W_300_soak", "board_idle_W_325_soak",
            "energy_nJ_per_row_300_soak", "energy_nJ_per_row_325_soak",
            "energy_vs_dense_300_soak", "energy_vs_dense_325_soak",
            "energy_nJ_per_row_300_vccint", "energy_nJ_per_row_325_vccint",
            "n_soaks_300", "n_soaks_325"]

    if not os.path.isdir(DATA):
        os.makedirs(DATA)
    with io.open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\r\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("wrote %s : %d rows x %d cols" % (OUT, len(rows), len(cols)))

    print("\n  ideal clock scaling = %.4f\n" % IDEAL)
    print("  %-7s %9s %9s | %8s %7s | %8s %7s | %9s %9s" %
          ("config", "GFeff300", "GFeff325", "scal_a3", "%ideal",
           "scal_sk", "%ideal", "nJ/row300", "nJ/row325"))
    for r in rows:
        e3, e5 = r["energy_nJ_per_row_300_soak"], r["energy_nJ_per_row_325_soak"]
        ss, ps = r["clock_scaling_soak"], r["pct_of_ideal_soak"]
        print("  %-7s %9.2f %9.2f | %8.4f %6.2f%% | %8s %7s | %9s %9s" %
              (r["sparsity"], r["GFLOPS_eff_300_avg3"], r["GFLOPS_eff_325_avg3"],
               r["clock_scaling_avg3"], r["pct_of_ideal_avg3"],
               ("%.4f" % ss) if ss != "" else "--",
               ("%.2f%%" % ps) if ps != "" else "--",
               ("%.1f" % e3) if e3 != "" else "--",
               ("%.1f" % e5) if e5 != "" else "--"))


if __name__ == "__main__":
    main()

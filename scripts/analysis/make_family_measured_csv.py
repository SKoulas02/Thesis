"""Merge the six family configurations' card measurements into chart-ready CSVs.

    python scripts/analysis/make_family_measured_csv.py

Inputs : results/family_measurements/<tag>/  (run_family_measure.py output, copied down)
         results/GEMV_Family_Hardware.csv     (build data: closed clock, OOC Fmax, area)
Outputs: results/GEMV_Family_Measured.csv       WIDE, 6 rows (one per family) -- the chart sheet
         results/GEMV_Family_Measured_long.csv  LONG, 30 rows (family x sparsity) -- the record

THE METHOD IS IMPORTED, NOT RE-IMPLEMENTED. Steady-state numbers come from
check_family_measure.steady() -- inverse-variance pooled overhead per configuration -- the
exact code that validated all six (throughput = cores x blocks x clock x a sparsity-only
efficiency, chi^2 5.48 on 5 dof). A second implementation here could drift from it silently.

WIDE LAYOUT, BECAUSE THAT IS WHAT A GROUPED BAR CHART WANTS. One row per family, one
contiguous block of five columns per metric (2:4, 2:8, 2:16, 2:32, MIXED). Selecting the
family labels plus one block gives "each family = a group, each sparsity = a bar" directly.

ESTIMATORS ARE NEVER MIXED, AND THE COLUMN PREFIX SAYS WHICH ONE A NUMBER CAME FROM:
  ss_    steady state: 11-shape sweeps, pooled overhead removed, geomean over shapes.
         Use for GFLOPS and efficiency, and for ANY cross-family comparison.
  soak_  60 s power soaks: board / VCCINT watts, and energy per row (board W / soak Mrow/s).
  avg3_  run_avg3 at N = 1024, mean of 3, launch overhead INCLUDED -- kept for continuity with
         the original 8x8 comparison table; biased across families (overhead scales with CUs).
  freq_  per-family build data: OOC Fmax (bare engine, 1.5 ns sweep) and the in-system clock
         the bitstream closed at. Frequency does NOT vary with sparsity.

⚠️ POWER CAVEAT that must travel with the soak_ columns: total power is ~independent of
sparsity (board <= 3.4%, VCCINT <= 4.5% spread in every family), but the small DYNAMIC part
(soak_core_dyn_W) often reads lowest at 2:4 -- and 2:4 is ALWAYS the first soak. That is
equally explained by warm-up; the soak order is fixed. Do not chart dynamic power vs sparsity
as a design effect without a reversed-order repeat.
"""

import csv
import io
import math
import os
import statistics

import check_family_measure as cfm

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # repository root; this file is in scripts/analysis/
RES = os.path.join(ROOT, "results")
OUT_WIDE = os.path.join(RES, "GEMV_Family_Measured.csv")
OUT_LONG = os.path.join(RES, "GEMV_Family_Measured_long.csv")

ORDER = ["4x4", "8x4", "16x3", "8x8", "4x24", "4x32"]          # size order, T = 16 .. 128
SP = [("2:4", "2to4"), ("2:8", "2to8"), ("2:16", "2to16"), ("2:32", "2to32"),
      ("MIXED", "MIXED")]


def gm(xs):
    return math.exp(sum(math.log(x) for x in xs) / len(xs))


def iv_pool(o):
    """Inverse-variance pooled overhead and its standard error (same weights as steady())."""
    S = o["shapes"]
    ov = [float(S[l][0]["launch_overhead_us"]) for _, l, _ in cfm.LABS]
    se = [float(S[l][0]["overhead_fit_se_us"]) for _, l, _ in cfm.LABS]
    w = [1.0 / s ** 2 for s in se]
    return sum(v * x for v, x in zip(ov, w)) / sum(w), (1.0 / sum(w)) ** 0.5


def host_cus(L):
    return (-(-L * 32 // 256)) + (-(-(10 * L + 2) // 256)) + 2 + (-(-L * 16 // 256))


def main():
    hw = {r["config"]: r for r in cfm.rd(os.path.join(RES, "GEMV_Family_Hardware.csv"))
          if r["role"] == "headline"}

    wide, long_rows = [], []
    for tag in ORDER:
        o = cfm.load(tag)
        if o is None:
            raise SystemExit("missing measurements for %s" % tag)
        L, clk = o["L"], o["clk"]
        h = hw[tag]
        ceil = 2.0 * 2 * L * clk / 1000.0                # GFLOPS_actual ceiling
        pool, pool_se = iv_pool(o)
        a = {r["sparsity"]: r for r in o["avg3"]}
        p = {r["label"].split()[-1]: r for r in o["power"]}
        if int(h["dsps_theory"]) != 8 * L or int(h["target_mhz"]) != clk:
            raise SystemExit("%s: hardware CSV disagrees with the measurement config" % tag)

        row = dict(family="%s (T=%d)" % (tag, L), config=tag, cores=o["C"], blocks=o["B"],
                   blocks_total=L, dsps=8 * L, hbm_pcs=int(h["hbm_pcs"]),
                   host_cus=host_cus(L), macs_per_beat=2 * L,
                   freq_ooc_fmax_mhz=float(h["ooc_fmax_mhz"]),
                   freq_closed_mhz=clk,
                   freq_achieved_mhz=float(h["achieved_mhz"]),
                   freq_closed_over_ooc=round(clk / float(h["ooc_fmax_mhz"]), 3),
                   ss_overhead_us=round(pool, 1), ss_overhead_se_us=round(pool_se, 1),
                   ss_gflops_act_ceiling=round(ceil, 2))

        per = {}
        for lab, suf in SP:
            _, m = cfm.steady(o, lab)
            gfe = gm([x["gfe"] for x in m])
            gfa = gm([x["gfa"] for x in m])
            eff = 100.0 * gfa / ceil
            pw = p[lab]
            bw = gm([x["bw"] for x in m])
            per[suf] = dict(
                power_W=float(pw["board_load_w"]),
                vccint_W=float(pw["vccint_load_w"]),
                core_dyn_W=float(pw["vccint_delta_w"]),
                energy_nJ=float(pw["energy_per_row_nJ"]),
                soak_mrow=float(pw["Mrow_s_sustained"]),
                gfe=gfe, gfa=gfa, eff=eff,
                eff_mhz=clk * eff / 100.0,
                avg3_mrow=float(a[lab]["Mrow_s"]),
                avg3_gfe=float(a[lab]["GFLOPS_effective"]),
                gflops_per_W=gfe / float(pw["board_load_w"]),
                # ---- appended 2026-09-14 ----
                # actual MACs per second per HBM pseudo-channel: GFLOPS_actual counts 2 flops
                # per MAC, so GMAC/s = GFLOPS_actual / 2. The professor's DSP-vs-channel metric,
                # MEASURED (Family Hardware's gmac_per_hbm_pc is the 2T x clock theory).
                gmac_act_per_pc=(gfa / 2.0) / int(h["hbm_pcs"]),
                gflops_act_per_W=gfa / float(pw["board_load_w"]),
                bandwidth=bw)
            long_rows.append(dict(
                family=row["family"], config=tag, blocks_total=L, dsps=8 * L,
                hbm_pcs=row["hbm_pcs"], clock_mhz=clk, sparsity=lab,
                ss_gflops_effective=round(gfe, 2), ss_gflops_actual=round(gfa, 2),
                ss_efficiency_pct=round(eff, 2), ss_effective_mhz=round(clk * eff / 100.0, 2),
                soak_board_W=pw["board_load_w"], soak_vccint_W=pw["vccint_load_w"],
                soak_core_dyn_W=pw["vccint_delta_w"], soak_energy_nJ_per_row=pw["energy_per_row_nJ"],
                soak_mrow_s=pw["Mrow_s_sustained"],
                ss_gflops_per_board_W=round(gfe / float(pw["board_load_w"]), 3),
                ss_gflops_act_per_board_W=round(gfa / float(pw["board_load_w"]), 3),
                ss_gmac_act_per_hbm_pc=round((gfa / 2.0) / int(h["hbm_pcs"]), 3),
                ss_bandwidth_GBs=round(bw, 2),
                avg3_mrow_s=a[lab]["Mrow_s"], avg3_gflops_effective=a[lab]["GFLOPS_effective"],
                ss_overhead_us=round(pool, 1)))

        # contiguous five-column blocks, in chart order
        blocks = [("soak_board_W_", "power_W", 3), ("ss_gflops_eff_", "gfe", 2),
                  ("ss_gflops_act_", "gfa", 2), ("ss_efficiency_pct_", "eff", 2),
                  ("ss_effective_mhz_", "eff_mhz", 1), ("soak_energy_nJ_row_", "energy_nJ", 2),
                  ("ss_gflops_eff_per_W_", "gflops_per_W", 2), ("soak_vccint_W_", "vccint_W", 3),
                  ("soak_core_dyn_W_", "core_dyn_W", 3), ("avg3_mrow_s_", "avg3_mrow", 3),
                  ("avg3_gflops_eff_", "avg3_gfe", 2),
                  # ---- APPENDED 2026-09-14, after every existing block, so that no column
                  # letter used by an earlier chart moves (never insert into a sheet that
                  # charts point at). New blocks start at column BT.
                  ("ss_gmac_act_per_pc_", "gmac_act_per_pc", 3),
                  ("ss_gflops_act_per_W_", "gflops_act_per_W", 3),
                  ("ss_bandwidth_GBs_", "bandwidth", 2)]
        for prefix, key, nd in blocks:
            for _, suf in SP:
                row[prefix + suf] = round(per[suf][key], nd)
        # scalar reference columns LAST, for the same reason
        in_pcs = (-(-L * 32 // 256)) + (-(-(10 * L + 2) // 256))
        row["hbm_input_pcs"] = in_pcs
        row["bandwidth_ceiling_GBs"] = round(clk * 1e6 * in_pcs * 32 / 1e9, 2)
        row["gmac_per_pc_theory"] = round((2 * L * clk / 1000.0) / int(h["hbm_pcs"]), 3)
        wide.append(row)

    for path, rows in ((OUT_WIDE, wide), (OUT_LONG, long_rows)):
        with io.open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), lineterminator="\r\n")
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print("wrote %s : %d rows x %d cols" % (path, len(rows), len(rows[0])))

    print("\n%-12s %5s %5s | %-34s | %-40s" % ("family", "OOC", "clock", "board W  2:4 2:8 2:16 2:32 MIX",
                                                "ss GFLOPS eff  2:4 2:8 2:16 2:32 MIX"))
    for r in wide:
        print("%-12s %5.0f %5d | %s | %s" % (
            r["family"], r["freq_ooc_fmax_mhz"], r["freq_closed_mhz"],
            " ".join("%5.1f" % r["soak_board_W_" + s] for _, s in SP),
            " ".join("%7.1f" % r["ss_gflops_eff_" + s] for _, s in SP)))


if __name__ == "__main__":
    main()

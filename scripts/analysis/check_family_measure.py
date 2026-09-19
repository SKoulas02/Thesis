"""Check one family configuration's card measurements, and compare it with every
configuration already measured.

    python scripts/analysis/check_family_measure.py 8x4          # results/family_measurements/8x4/

Reads what run_family_measure.py produced (copied into results/family_measurements/<tag>/)
and runs the same checks on every configuration, so a problem in config five is caught the
same way as in config one:

  FILES      7 CSVs with full row counts; every real-run log free of FAILED/WARNING
  THROUGHPUT Mrow/s doubles per sparsity step; GFLOPS_actual flat across sparsity (the
             fixed-MAC-rate claim); MIXED additive; spreads small
  POWER      board load flat across sparsity; nJ/row halves; enough samples; pre-run idle
  SHAPES     overhead fits consistent across sweeps (sd vs reported se); no spread > 3%;
             occupancy sane; padding as predicted; GFLOPS_actual vs its ceiling
  CROSS      steady-state throughput vs every other measured config AND vs the 8x8 @ 325
             reference, against theory = (cores x blocks x clock) ratio

WHY STEADY-STATE FOR CROSS-CONFIG RATIOS. run_avg3 includes launch overhead, and overhead
scales with CU count, so avg3 ratios between configurations are biased (4x4/8x8 read 0.323
against a theory of 0.308). The shape sweeps subtract a per-config fitted overhead, and
their geomean ratio landed within ~2% of theory. Only those are used for the comparison.
"""

import csv
import glob
import io
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # repository root; this file is in scripts/analysis/
BASE = os.path.join(ROOT, "results", "family_measurements")
REF = os.path.join(ROOT, "results")

CONFIGS = {"4x4": (4, 4, 400), "8x4": (8, 4, 375), "16x3": (16, 3, 350),
           "8x8": (8, 8, 325), "4x24": (4, 24, 300), "4x32": (4, 32, 250)}
ORDER = ["4x4", "8x4", "16x3", "8x8", "4x24", "4x32"]
LABS = [("2to4", "2:4", "00"), ("2to8", "2:8", "01"), ("2to16", "2:16", "10"),
        ("2to32", "2:32", "11"), ("MIXED", "MIXED", "mix")]

flags = []      # needs action
notes = []      # worth knowing, within error


def flag(msg):
    flags.append(msg)


def rd(p):
    with io.open(p, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def gm(xs):
    return math.exp(sum(math.log(x) for x in xs) / len(xs))


def steady(o, lab):
    """Per-shape steady-state metrics with ONE POOLED overhead for the whole config.

    WHY POOLED (decided 2026-09-13 on 4x4 + 8x4 + 16x3). Each sweep fits its own overhead
    with a ~±30-60 us standard error, and that error shifts the WHOLE sweep ~±1%. In all
    three configs the scatter of the five fits across sparsity matched their reported se
    -- no evidence overhead depends on sparsity. Using the mean of the five:
      * cross-config ratio scatter fell 4-5x (sd ~1% -> 0.2-0.3%)
      * GFLOPS_actual stopped exceeding its physical ceiling (16x3 2:4: 100.35% -> 99.17%)
      * occupancies > 1.0 fell from 10 to 4 (each a single fast run, within ~1%)
    For the old 8x8@325 dataset the "pool" is the mean of its table values -- not a fit,
    which is exactly why it carries a systematic offset.

    INVERSE-VARIANCE WEIGHTED, NOT A PLAIN MEAN (changed 2026-09-14, on 4x24). 4x24's 2:4
    fit came out 1365 +/- 172 us (R^2 0.9894) against 1078-1174 for the other four. A plain
    mean let that one noisy fit drag the pool to 1162 us: every 4x24 ratio then read +0.9 to
    +1.2% high and GFLOPS_actual crossed its physical ceiling. Weighting each fit by 1/se^2
    -- the standard way to combine measurements with different, KNOWN uncertainties, and the
    reason run_shapes records se -- gives 1104 us. The overhead model predicted 1101 us
    BEFORE the run; the median (a different, assumption-free method) gives 1107. Across all
    six config pairs the worst deviation from theory fell from 1.16% (mean) to 0.52%.
    Adopted after seeing 4x24 -- said openly -- but two independent methods agree.
    """
    S = o["shapes"]
    ovs = [float(S[l][0]["launch_overhead_us"]) for _, l, _ in LABS]
    ses = [S[l][0].get("overhead_fit_se_us") for _, l, _ in LABS]
    if all(ses) and all(float(s) > 0 for s in ses):
        w = [1.0 / float(s) ** 2 for s in ses]
        pool = sum(v * x for v, x in zip(ovs, w)) / sum(w)
    else:
        pool = statistics.mean(ovs)          # no se recorded (the old 8x8 table)
    out = []
    for r in S[lab]:
        runs = [float(x) for x in r["runs"].split()]
        per = (statistics.mean(runs) - pool) / int(r["batch_R"])
        M, N = int(r["M_rows"]), int(r["N_cols"])
        laps = int(r["laps_per_matrix"]) if r.get("laps_per_matrix") else M // o["L"]
        out.append(dict(shape=r["shape"], occ=float(r["ideal_per_matrix_us"]) / per,
                        gfe=2.0 * M * N / (per * 1000),
                        gfa=2.0 * int(r["beats_per_matrix"]) * 2 * o["L"] / (per * 1000),
                        # true rows / rows actually computed: padding costs beats, earns no credit
                        padf=float(M) / (laps * o["L"]),
                        # measured HBM INPUT bandwidth, GB/s (1e9 bytes): the per-beat input PCs'
                        # bytes for one matrix over its steady-state latency. Added 2026-09-14 for
                        # the family charts; changes no other field.
                        bw=(float(r["bytes_in_per_matrix"]) / (per * 1e-6) / 1e9
                            if r.get("bytes_in_per_matrix") else float("nan"))))
    return pool, out


def load(tag):
    C, B, clk = CONFIGS[tag]
    d = os.path.join(BASE, tag)
    t = "{}_{}MHz".format(tag, clk)
    out = dict(tag=tag, C=C, B=B, L=C * B, clk=clk, dir=d)
    try:
        out["avg3"] = rd(os.path.join(d, "avg3_%s.csv" % t))
        out["power"] = rd(os.path.join(d, "power_%s.csv" % t))
        out["shapes"] = {lab: rd(os.path.join(d, "shapes_%s_%s.csv" % (t, f)))
                         for f, lab, _ in LABS}
    except IOError:
        return None
    return out


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in CONFIGS:
        raise SystemExit("usage: python scripts/analysis/check_family_measure.py <%s>" % "|".join(ORDER))
    tag = sys.argv[1]
    X = load(tag)
    if X is None:
        raise SystemExit("incomplete or missing: %s" % os.path.join(BASE, tag))
    L, clk = X["L"], X["clk"]
    print("=" * 78)
    print("%s  (%d cores x %d blocks = %d rows/lap, %d MACs/beat)  @ %d MHz"
          % (tag, X["C"], X["B"], L, 2 * L, clk))
    print("=" * 78)

    # ------------------------------------------------------------------ FILES
    logs = sorted(glob.glob(os.path.join(X["dir"], "run_*.log")))
    for lg in logs:
        txt = io.open(lg, encoding="utf-8", errors="replace").read()
        kind = "dry-run" if "(dry run" in txt else "real"
        bad = [l for l in txt.splitlines()
               if any(k in l for k in ("FAILED", "not positive", "Traceback"))]
        # an R^2 < 0.99 fit is NOT an action item any more: its large se down-weights it in the
        # inverse-variance pool automatically. Report it; do not demand a re-run for it.
        warn = [l for l in txt.splitlines() if "WARNING" in l]
        print("log %-28s %-8s %s" % (os.path.basename(lg), kind,
                                     "clean" if not (bad or warn) else
                                     "%d failure(s), %d warning(s)" % (len(bad), len(warn))))
        for b in bad[:5]:
            flag("log %s: %s" % (os.path.basename(lg), b.strip()))
        for w_ in warn[:5]:
            notes.append("log %s: %s (weighted down by its se in the pool)"
                         % (os.path.basename(lg), w_.strip()))
    n_sh = [len(X["shapes"][l]) for _, l, _ in LABS]
    print("rows: avg3 %d, power %d, shapes %s" % (len(X["avg3"]), len(X["power"]), n_sh))
    if len(X["avg3"]) != 5 or len(X["power"]) != 5 or any(n != 11 for n in n_sh):
        flag("row counts incomplete")
    if any("overhead_fit_se_us" not in X["shapes"][l][0] for _, l, _ in LABS):
        flag("shape CSVs lack overhead_fit_se_us -- re-run shapes with the current run_shapes.py")

    # ------------------------------------------------------------- THROUGHPUT
    print("\nTHROUGHPUT (run_avg3, mean of 3; includes launch overhead)")
    a = {r["sparsity"]: r for r in X["avg3"]}
    ceil_act = 2.0 * 2 * L * clk / 1000.0
    print("  %-6s %9s %6s %10s %9s %8s %9s" % ("", "span us", "spr%", "Mrow/s", "theory", "% ideal",
                                              "GF_act"))
    for s in ("2:4", "2:8", "2:16", "2:32", "MIXED"):
        r = a[s]
        beats, rows, span = int(r["weight_beats"]), int(r["M_rows"]), float(r["latency_avg_us"])
        if rows != int({"2:4": 8192, "2:8": 16384, "2:16": 32768, "2:32": 65536,
                        "MIXED": 16384}[s]) * L:
            flag("avg3 %s: M_rows %d is not laps x %d" % (s, rows, L))
        print("  %-6s %9.1f %6s %10.3f %9.3f %7.1f%% %9s" % (
            s, span, r["spread_pct"], float(r["Mrow_s"]), rows / float(beats) * clk,
            100.0 * (beats / float(clk)) / span, r["GFLOPS_actual"]))
        # by EFFECT, as for shapes: 4x24's 3.3% and 5.0% spreads moved their means 0.51%/0.86%
        runs = [float(x) for x in r["runs"].split()]
        eff = 100 * abs(statistics.mean(runs) - statistics.median(runs)) / statistics.median(runs)
        if eff > 1.5:
            flag("avg3 %s: one run moves the mean %.1f%% (spread %s%%) -- re-run avg3" % (
                s, eff, r["spread_pct"]))
        elif float(r["spread_pct"]) > 3:
            notes.append("avg3 %s spread %s%% moves the mean only %.2f%% -- keep" % (
                s, r["spread_pct"], eff))
    m = [float(a[s]["Mrow_s"]) for s in ("2:4", "2:8", "2:16", "2:32")]
    ratios = [m[i + 1] / m[i] for i in range(3)]
    acts = [float(a[s]["GFLOPS_actual"]) for s in ("2:4", "2:8", "2:16", "2:32")]
    act_spread = 100 * (max(acts) - min(acts)) / min(acts)
    print("  Mrow/s step ratios %s (ideal 2.000)" % " ".join("%.3f" % x for x in ratios))
    print("  GFLOPS_actual %.2f-%.2f, spread %.1f%%, ceiling %.1f" % (min(acts), max(acts),
                                                                   act_spread, ceil_act))
    if any(abs(x - 2) > 0.1 for x in ratios):
        flag("Mrow/s step ratio off 2.0: %s" % ratios)
    if act_spread > 5:
        flag("GFLOPS_actual not flat across sparsity (%.1f%%)" % act_spread)
    q = int(a["MIXED"]["M_rows"]) / 4.0
    pred = sum(q / float(a[s]["Mrow_s"]) for s in ("2:4", "2:8", "2:16", "2:32"))
    add = 100 * (float(a["MIXED"]["latency_avg_us"]) - pred) / pred
    print("  MIXED additivity %+.2f%%" % add)
    if abs(add) > 2:
        flag("MIXED not additive (%+.2f%%)" % add)

    # ------------------------------------------------------------------ POWER
    print("\nPOWER (60 s soak)")
    print("  %-8s %7s %7s %13s %7s %8s %10s %8s %6s" % ("", "load W", "idle W", "delta W",
                                                      "VCCINT", "nJ/row", "soak Mrow", "/avg3",
                                                      "n"))
    P = {}
    for r in X["power"]:
        s = r["label"].split()[-1]
        P[s] = r
        print("  %-8s %7s %7s %6s+/-%-5s %7s %8s %10s %8.3f %3s/%s" % (
            s, r["board_load_w"], r["board_idle_w"], r["board_delta_w"], r["board_delta_se"],
            r["vccint_load_w"], r["energy_per_row_nJ"], r["Mrow_s_sustained"],
            float(r["Mrow_s_sustained"]) / float(a[s]["Mrow_s"]), r["n_load"], r["n_idle"]))
        if int(r["n_load"]) < 40:
            flag("power %s: only %s load samples" % (s, r["n_load"]))
    lo = [float(P[s]["board_load_w"]) for s in ("2:4", "2:8", "2:16", "2:32")]
    ld_spread = 100 * (max(lo) - min(lo)) / min(lo)
    vc = [float(P[s]["vccint_load_w"]) for s in ("2:4", "2:8", "2:16", "2:32")]
    vc_spread = 100 * (max(vc) - min(vc)) / min(vc)
    nj = [float(P[s]["energy_per_row_nJ"]) for s in ("2:4", "2:8", "2:16", "2:32")]
    print("  board load %.2f-%.2f W (spread %.1f%%); nJ/row ratios %s; static share idle/load %.0f%%"
          % (min(lo), max(lo), ld_spread, " ".join("%.3f" % (nj[i] / nj[i + 1]) for i in range(3)),
             100 * statistics.mean(float(P[s]["board_idle_w"]) for s in P) / statistics.mean(lo)))
    vd = [float(P[s]["vccint_delta_w"]) for s in ("2:4", "2:8", "2:16", "2:32")]
    print("  VCCINT (FPGA core) load %.2f-%.2f W (spread %.1f%%);  dynamic core power (VCCINT load "
          "- own idle) %s W" % (min(vc), max(vc), vc_spread, " / ".join("%.3f" % x for x in vd)))
    # POWER-vs-SPARSITY IS A FINDING, NOT A DATA-INTEGRITY CHECK -- so it never flags.
    # Tried two pass/fail rules on it (2026-09-14) and each failed on real data:
    #   * board load  > 3%: 8x8 flagged at 3.4% -- but board idle was flat to 0.7% and VCCINT
    #     to 0.9%; board load alternated high/low/high/low = FAN cycling.
    #   * VCCINT load > 3%: 8x4 flagged at 4.5% -- its FIRST soak (2:4) read low; VCCINT
    #     leakage rises with die temperature (same bitstream read +4% VCCINT 12 days apart).
    # And a real 0.3 W design effect would be ~4% of VCCINT but <1% of board, so no
    # both-rails rule is principled either. THE CONFOUND: run_family_measure always soaks
    # 2:4 -> 2:8 -> 2:16 -> 2:32 -> MIXED, so any sparsity trend in the small dynamic
    # component (0.3-1.6 W) is inseparable from warm-up. Only a reversed-order repeat can
    # separate them. Total power is what the thesis claims flat; report it with this caveat.
    for label, val in (("board load", ld_spread), ("VCCINT load", vc_spread)):
        if val > 3:
            notes.append("%s spread %.1f%% across sparsity (finding, not a fault: fans / die "
                         "temperature; soak order is fixed)" % (label, val))
    if vd.index(min(vd)) == 0:
        notes.append("dynamic core power is lowest at 2:4, the FIRST soak -- consistent with less "
                      "window-reload switching at 2:4, but equally with warm-up; order is fixed, "
                      "so not separable from this data")

    # ----------------------------------------------------------------- SHAPES
    print("\nSHAPES (steady state, per-config fitted overhead)")
    S = X["shapes"]
    ov = [float(S[l][0]["launch_overhead_us"]) for _, l, _ in LABS]
    se = [float(S[l][0]["overhead_fit_se_us"]) for _, l, _ in LABS
          if S[l][0].get("overhead_fit_se_us")]
    print("  overhead: " + "  ".join("%s %.0f" % (l, o) for (_, l, _), o in zip(LABS, ov)))
    if se:
        sd = statistics.stdev(ov)
        print("  mean %.1f us; sd across sweeps %.1f vs mean se %.1f -> %s" % (
            statistics.mean(ov), sd, statistics.mean(se),
            "consistent with ONE overhead (pooling justified)" if sd < 2 * statistics.mean(se)
            else "overhead VARIES with sparsity -- do not pool"))
        w = [1.0 / s ** 2 for s in se]
        ivp = sum(v * x for v, x in zip(ov, w)) / sum(w)
        print("  pooled overhead (inverse-variance) %.1f +/- %.1f us   [plain mean %.1f, median %.1f]"
              % (ivp, (1.0 / sum(w)) ** 0.5, statistics.mean(ov), statistics.median(ov)))
        cus = (-(-L * 32 // 256)) + (-(-(10 * L + 2) // 256)) + 2 + (-(-L * 16 // 256))
        pred = 71 + 42.9 * cus
        print("  overhead model 71 + 42.9 x %d CUs = %.0f us -> measured %+.1f%%" % (
            cus, pred, 100 * (ivp / pred - 1)))
    print("  %-4s %11s %5s " % ("", "MxN", "pad") + " ".join("%7s" % l for _, l, _ in LABS))
    for i, r0 in enumerate(S["2:4"]):
        print("  %-4s %11s %5s " % (r0["shape"], r0["M_rows"] + "x" + r0["N_cols"],
                                    r0["padding_rows"]) +
              " ".join("%7s" % S[l][i]["dsp_occupancy"] for _, l, _ in LABS))
        M = int(r0["M_rows"])
        if int(r0["padding_rows"]) != -(-M // L) * L - M:
            flag("padding wrong for %s" % r0["shape"])
        for _, l, _ in LABS:
            r = S[l][i]
            # FLAG BY EFFECT, NOT BY SPREAD. A 3-5% spread on 8x4 moved its points only
            # 0.87-1.13% -- the same size as the overhead-fit error bar, so a re-run just
            # trades one +/-1% for another. What matters is how far the odd run drags the
            # mean away from the median. The 4x4 G8 outlier that genuinely needed a re-run
            # moved its point 3.7%. Threshold 1.5%: above both noise sources combined.
            runs = [float(x) for x in r["runs"].split()]
            ovh_r = float(r["launch_overhead_us"])
            ideal, R = float(r["ideal_per_matrix_us"]), int(r["batch_R"])
            o_mean = ideal / ((statistics.mean(runs) - ovh_r) / R)
            o_med = ideal / ((statistics.median(runs) - ovh_r) / R)
            eff = 100 * abs(o_mean - o_med) / o_med
            if eff > 1.5:
                flag("shapes %s %s: one run moves the point %.1f%% (spread %s%%, runs %s) -- "
                     "re-run this sweep" % (l, r["shape"], eff, r["spread_pct"], r["runs"]))
            elif float(r["spread_pct"]) > 3:
                notes.append("shapes %s %s spread %s%% moves the point only %.2f%% -- within "
                             "error, keep" % (l, r["shape"], r["spread_pct"], eff))
            # Occupancy cannot exceed 1.0 physically. The CSV value uses this sweep's OWN overhead
            # fit, which is not the analysis method -- 4x24 2:4 read 1.02-1.04 on every shape
            # from one noisy fit. HIGH per-sweep readings are therefore notes; the POOLED check
            # below is the one that flags. A LOW reading is flagged either way, because
            # over-subtraction can only push occupancy UP.
            if float(r["dsp_occupancy"]) < 0.85:
                flag("shapes %s %s occupancy %s -- too low" % (l, r["shape"], r["dsp_occupancy"]))
            elif float(r["dsp_occupancy"]) > 1.0:
                notes.append("shapes %s %s per-sweep occupancy %s > 1.0 (own fit) -- see pooled"
                             % (l, r["shape"], r["dsp_occupancy"]))
    print("  %-22s" % "max spread %" + " ".join("%7.2f" % max(float(r["spread_pct"]) for r in S[l])
                                              for _, l, _ in LABS))
    print("  %-22s" % "GF_act geomean" + " ".join("%7.2f" % gm([float(r["GFLOPS_actual"])
                                                                for r in S[l]]) for _, l, _ in LABS)
          + "   (ceiling %.1f, per-sweep overhead)" % ceil_act)

    # ---- the analysis method: POOLED overhead ----
    print("\n  WITH POOLED OVERHEAD (the analysis method):")
    eff_row, over_row = [], []
    for _, l, _ in LABS:
        pool, m = steady(X, l)
        pct = 100 * gm([x["gfa"] for x in m]) / ceil_act
        n_over = sum(x["occ"] > 1.0 for x in m)
        eff_row.append(pct)
        over_row.append(n_over)
        if pct > 100.5:
            flag("GFLOPS_actual %s = %.2f%% of its physical ceiling even with a pooled overhead"
                 % (l, pct))
        for x in m:
            if x["occ"] > 1.015:
                flag("pooled occupancy %s %s = %.4f -- beyond the error bar" % (l, x["shape"], x["occ"]))
    print("  %-22s" % "GF_act % of ceiling" + " ".join("%6.2f%%" % p for p in eff_row)
          + "   (pooled overhead %.1f us)" % pool)
    print("  %-22s" % "occupancy > 1.0" + " ".join("%7d" % n for n in over_row))

    # ------------------------------------------------------------------ CROSS
    print("\nCROSS-CONFIG, steady state, POOLED overhead: GFLOPS_effective geomean ratio")
    print("  theory = (cores x blocks x clock) ratio, then x the PADDING factor (padded rows cost")
    print("  beats but earn no GFLOPS credit). Deviation per sparsity 2:4 2:8 2:16 2:32 MIXED:")
    others = [(t, load(t)) for t in ORDER if t != tag]
    others = [(t, o) for t, o in others if o]
    try:
        ref = {l: rd(os.path.join(REF, "shapes_%s_325MHz.csv" % c)) for _, l, c in LABS}
        others.append(("8x8@325 ref", dict(tag="8x8@325 ref", L=64, clk=325,
                                           shapes=ref, power=rd(os.path.join(REF, "power_results_325.csv")))))
    except IOError:
        pass
    mine = {l: steady(X, l)[1] for _, l, _ in LABS}
    for t, o in others:
        th = (L * clk) / float(o["L"] * o["clk"])
        dev = []
        for _, l, _ in LABS:
            theirs = steady(o, l)[1]
            ratio = gm([x["gfe"] for x in mine[l]]) / gm([x["gfe"] for x in theirs])
            padadj = gm([x["padf"] for x in mine[l]]) / gm([x["padf"] for x in theirs])
            dev.append(100 * (ratio / (th * padadj) - 1))
        mu, sd = statistics.mean(dev), statistics.stdev(dev)
        print("  %s / %-12s theory %.3f  dev %s   mean %+.2f%% sd %.2f" % (
            tag, t, th, " ".join("%+5.1f" % d for d in dev), mu, sd))
        # new-method pairs agreed to within 0.3% (sd <= 0.32) on the first three configs;
        # the old 8x8@325 reference is exempt -- its offset is known and explained
        if not t.startswith("8x8@325"):
            if abs(mu) > 1.5 or sd > 1.0:
                flag("cross ratio vs %s: mean %+.2f%% sd %.2f -- the first three configs agreed "
                     "within 0.3%% / sd 0.32; investigate" % (t, mu, sd))

    print("\nCROSS-CONFIG power @ 2:4 (board load / idle / VCCINT load / nJ per row)")
    rows = [(tag, X["clk"], P["2:4"])]
    for t, o in others:
        for r in o["power"]:
            if r["label"].split()[-1] == "2:4":
                rows.append((t, o["clk"], r))
    for t, c, r in rows:
        print("  %-12s @%3d  %7s W  %7s W  %6s W  %8s nJ" % (t, c, r["board_load_w"], r["board_idle_w"],
                                                           r["vccint_load_w"], r["energy_per_row_nJ"]))

    if any(t.startswith("8x8@325") for t, _ in others):
        print("\n  NB ratios vs '8x8@325 ref' use that campaign's OLD overhead table, not a fitted"
              "\n  overhead -- expect a systematic offset until 8x8 is re-run through"
              "\n  run_family_measure.py. Ratios between configs measured the new way are the"
              "\n  trustworthy ones.")

    if notes:
        print("\nNOTES (within error, no action):")
        for n in notes:
            print("  - " + n)
    print("\n" + ("ALL CHECKS PASSED" if not flags else "%d FLAG(S) -- ACTION NEEDED:" % len(flags)))
    for f in flags:
        print("  - " + f)


if __name__ == "__main__":
    main()

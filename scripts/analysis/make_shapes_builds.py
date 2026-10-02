"""The 8x8 shape study's eleven matrices, MIXED, on EVERY configuration: the six single engines
(family campaign) beside the sixteen multi-engine builds (the shape sweep of 2026-10-02, each
matrix split over all the engines in lockstep). Compute-only latency per matrix, the rate, the
efficiency, energy per element, and the fixed cost of one calculation.

    python scripts/analysis/make_shapes_builds.py           # the two CSVs (always rewritten)
    python scripts/analysis/make_shapes_builds.py --xlsx    # + the chart workbook, ONCE

Inputs : results/family_measurements/<tag>/     the singles (make_shapes_singles.collect():
                                                pooled-overhead latency, the MIXED soak)
         results/window_shapes_<stamp>/shapes/  shapes_xrt_<arch>_<MHz>MHz_MIXED.csv + power_xrt_*
                                                (run_window_shapes.py; the latest per build)
         results/GEMV_Qwen_XRT.csv              the singles' fixed cost per calculation (below)
         results/GEMV_Configurations.csv        row order; workload/plan_shapes.py, plan_qwen.py
Outputs: results/GEMV_Shapes_Builds.csv          LONG, 22 builds x 11 shapes
         results/GEMV_Shapes_Builds_Summary.csv  one row per build, sorted by HBM channels like the
                                                 Qwen table; chart columns in _single / _multi /
                                                 _shared / _bcast quads (one filled per row)
         results/GEMV_Charts_Shapes_Builds.xlsx  chart-ready sheets; built ONCE (it will hold charts)

LATENCY per matrix = compute only, launch costs removed, padding included (row grain 4 x lanes
of one engine): singles -- the family estimator (batched runs minus the pooled launch overhead,
OpenCL host); multi-engine builds -- the two-batch slope of run_shapes_xrt.py (XRT host): the
busiest engine's time, (t(4R) - t(R)) / 3R, median of 10 passes. The host does not enter either.
The slope can read HIGH by up to ~2 % where the engines' shares differ slightly (staggered
lockstep starts): occupancy > 1 is flagged per build (occupancy_over_1_shapes).
RATES: delivered GFLOPS = 2 x M x N / latency (dense-equivalent work); silicon GFLOPS = the work
actually computed; efficiency = ideal / latency, the MINIMUM over the 11 shapes (the slope can
only read high); weight rate = the busiest engine's weight beats / latency (median).
ENERGY = soak power x latency (as the 8x8 charts): static = idle x latency, dynamic = (load -
idle) x latency, per element = / true M x N; geomean over the 11 shapes (the static share is
constant within a build, so the two geomeans add up exactly). Soaks: singles -- the family MIXED
soak; multi-engine -- the sweep's own 60 s soak, the same V = 1024 MIXED stimulus. soak_rate_pct
= the soak's rows/s against 100 % streaming: the singles' OpenCL soaks ran at a lower duty cycle,
so their load power, and dynamic energy, read slightly low.
FIXED COST of one calculation (what a lone request pays on top of its compute): multi-engine --
the sweep's intercept t(R) - R x slope (median of 11 shapes); singles -- the Qwen test's overhead
per layer, (token - engine) / 40 (GEMV_Qwen_XRT.csv, the mean of two runs). Both are XRT,
ert=false, one lockstep calculation; on the 16 multi-engine builds the two methods are printed
side by side. request_G9_us = compute + fixed for one 4096 x 4096 MIXED matrix.
"""

import argparse
import csv
import glob
import io
import math
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # repository root; this file is in scripts/analysis/
RES = os.path.join(ROOT, "results")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "workload"))
import make_shapes_singles as mss              # noqa: E402  the singles' rows (family data)
import plan_shapes as ps                       # noqa: E402
import plan_qwen as pq                         # noqa: E402
import plan_workload as pw                     # noqa: E402

OUT_LONG = os.path.join(RES, "GEMV_Shapes_Builds.csv")
OUT_SUM = os.path.join(RES, "GEMV_Shapes_Builds_Summary.csv")
OUT_XLSX = os.path.join(RES, "GEMV_Charts_Shapes_Builds.xlsx")
QWEN = os.path.join(RES, "GEMV_Qwen_XRT.csv")
CONFIGS = os.path.join(RES, "GEMV_Configurations.csv")
KINDS = ("single", "multi", "shared", "bcast")
KIND_OF = {"single": "single", "multi": "multi", "shared": "shared", "broadcast": "bcast"}
KIND_TEXT = {"single": "single engine", "multi": "multi-tenant, own vector channels",
             "shared": "multi-tenant, shared vector channels",
             "bcast": "multi-tenant, broadcast vector (one mover pair)"}
SUFFIX = {"shared": " shared", "bcast": " bcast"}
G9 = "G9"                                       # 4096 x 4096: the single-request comparison
LAP_CYCLES_MIXED_V1024 = 32 * (8 + 4 + 2 + 1) / 4.0 + pw.LAP_BUBBLE    # soak: one lap, average
SCALING = ["4x4", "2x4x4", "3x4x4", "4x4x4", "5x4x4", "6x4x4_bcast", "7x4x4_bcast"]
TWINS = [("3 x 4x4", "3x4x4"), ("3 x 8x4", "3x8x4"), ("2 x 16x3", "2x16x3")]
FEEDS = [("own vector", ""), ("shared vector", "_shared"), ("broadcast vector", "_bcast")]
TWIN_METRICS = [("Delivered GFLOPS (geomean of 11 MIXED shapes)", "gflops_delivered_geomean", "0.0"),
                ("Efficiency (% of ideal, worst shape)", "efficiency_pct", "0.0"),
                ("Energy per element (pJ, geomean)", "total_pJ_per_element", "0.0"),
                ("One 4096 x 4096 request (us: compute + fixed cost)", "request_G9_us", "0.0"),
                ("Weight rate of the busiest engine (M beats/s)", "weight_rate_Mbeats_s", "0.0")]


def fail(msgs):
    print("CHECKS FAILED -- nothing written:")
    for m in (msgs if isinstance(msgs, list) else [msgs]):
        print("   ", m)
    raise SystemExit(1)


def read_csv(path):
    with io.open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields=None):
    fields = fields or list(rows[0].keys())
    with io.open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, restval="", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print("wrote %s (%d rows, %d columns)" % (os.path.relpath(path, ROOT), len(rows), len(fields)))


def gm(xs):
    return math.exp(sum(math.log(x) for x in xs) / len(xs))


def builds_meta():
    """[(name, shape, engines, clock, kind, label, channels, movers, sort key)] for every build
    with a clock, in the Qwen table's order (HBM channels, then Configurations)."""
    by_key = dict(((a[1], a[2]), a[0]) for a in pw.ARCHS)
    order = {}
    for i, c in enumerate(read_csv(CONFIGS)):
        order[by_key[(c["engine_shape"], int(c["tenants"]))]] = i
    out = []
    for i, (name, shape, n, clock, _f, _x) in enumerate(pq.BUILDS):
        if clock is None:
            continue
        k = KIND_OF[pq.kind(name)]
        w, ind, a, c = pw.channels_of(shape)
        channels = n * (w + ind + c) + (a if k in ("shared", "bcast") else n * a)
        movers = n * (w + ind + c) + (a if k == "bcast" else n * a)
        label = ((shape if n == 1 else "%d x %s%s" % (n, shape, SUFFIX.get(k, "")))
                 + " (%d ch)" % channels)
        out.append((name, shape, n, clock, k, label, channels, movers,
                    (channels, order.get(name, 100 + i))))
    return sorted(out, key=lambda x: x[-1])


def singles(problems):
    """{tag: [11 MIXED shape rows]} from the family data, plus the soak's rate."""
    rows, _ref, _worst = mss.collect()
    out = {}
    for r in rows:
        if r["sparsity"] == "MIXED":
            out.setdefault(r["build"], []).append(r)
    soak = {}
    for tag in out:
        o = mss.cfm.load(tag)
        pw_ = [x for x in o["power"] if x["label"].split()[-1] == "MIXED"]
        if len(pw_) != 1 or int(pw_[0]["V"]) != 1024:
            problems.append("%s: not one V=1024 MIXED soak" % tag)
            continue
        soak[tag] = pw_[0]
    return out, soak


def shape_sweeps(problems):
    """{arch: (rows, power row, folder)} from the latest window_shapes_* per build."""
    out = {}
    for d in sorted(glob.glob(os.path.join(RES, "window_shapes_*"))):
        if not os.path.isfile(os.path.join(d, "window_summary.csv")):
            continue
        for s in read_csv(os.path.join(d, "window_summary.csv")):
            if s["part"] != "shapes" or s["status"] != "OK":
                continue
            stem = os.path.join(d, "shapes", "shapes_xrt_%s_%sMHz_MIXED.csv" % (s["arch"],
                                                                               s["clock_mhz"]))
            pwf = os.path.join(d, "shapes", "power_xrt_%s_%sMHz_MIXED.csv" % (s["arch"],
                                                                             s["clock_mhz"]))
            if not (os.path.exists(stem) and os.path.exists(pwf)):
                problems.append("%s: %s has no shapes / power CSV" % (os.path.basename(d),
                                                                      s["arch"]))
                continue
            out[s["arch"]] = (read_csv(stem), read_csv(pwf), os.path.basename(d))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--xlsx", action="store_true", help="also build the chart workbook (once)")
    a = ap.parse_args()
    problems = []
    meta = builds_meta()
    fam, fam_soak = singles(problems)
    sweep = shape_sweeps(problems)
    qwen = {}
    for r in read_csv(QWEN):
        if r["status"] == "measured":
            m = re.search(r"workload_xrt_qwen_(.+)_\d+MHz\.csv$", r["source"].split(" + ")[-1])
            qwen[m.group(1)] = r
    ref = [(g, M, N) for g, M, N in ps.SHAPES]
    long_rows, summary, xcheck = [], [], []
    for name, shape, n, clock, k, label, channels, movers, _key in meta:
        cores, blocks = pq.SHAPE_OF[shape]
        lanes = cores * blocks
        per = []                              # (shape, M, N, latency, ideal, beats, pad, fixed)
        if n == 1:
            if name not in fam or name not in fam_soak:
                problems.append("%s: no family MIXED data" % name)
                continue
            rows = fam[name]
            if int(rows[0]["clock_mhz"]) != clock:
                problems.append("%s: family clock %s, plan %d" % (name, rows[0]["clock_mhz"],
                                                                  clock))
            p = fam_soak[name]
            load_w, idle_w = float(p["board_load_w"]), float(p["board_idle_w"])
            rate = float(p["Mrow_s_sustained"])
            for r in rows:
                M, N = int(r["M_rows"]), int(r["N_cols"])
                lpc = int(math.ceil(float(M) / (4 * lanes)))
                beats = lpc * (N // 32) * (8 + 4 + 2 + 1)
                lat = float(r["latency_us"])
                per.append((r["shape"], M, N, lat, lat * float(r["occupancy"]), beats,
                            int(r["padding_rows"]), None))
            lat_src = "family_measurements/%s (OpenCL batched runs, pooled launch overhead)" % name
            pwr_src = "family_measurements/%s/power_%s_%dMHz.csv (MIXED soak)" % (name, name,
                                                                                 clock)
            q = qwen.get(name)
            fixed = (float(q["overhead_us"]) / float(q["layers"])) if q else None
            fixed_src = "Qwen test: (token - engine) / 40, GEMV_Qwen_XRT.csv"
        else:
            if name not in sweep:
                problems.append("%s: no shape sweep" % name)
                continue
            rows, pwr, d = sweep[name]
            plan = ps.plan(name)
            if len(rows) != 11 or len(pwr) != 1:
                problems.append("%s: %d shape rows, %d power rows" % (name, len(rows), len(pwr)))
                continue
            for r, s in zip(rows, plan["shapes"]):
                if (r["shape"], int(r["M_rows"]), int(r["N_cols"])) != (s["shape"], s["M"], s["N"]) \
                        or int(r["batch_R_big"]) != s["r_big"] \
                        or float(r["busiest_cycles"]) != float(s["busiest_cycles"]) \
                        or int(r["clock_mhz"]) != clock or int(r["padding_rows"]) != s["padding_rows"]:
                    problems.append("%s %s does not match its plan" % (name, r["shape"]))
                per.append((r["shape"], s["M"], s["N"], float(r["latency_per_matrix_us"]),
                            s["busiest_cycles"] / float(clock), max(s["engine_beats"]),
                            s["padding_rows"], float(r["fixed_cost_us"])))
            p = pwr[0]
            load_w, idle_w = float(p["board_load_w"]), float(p["board_idle_w"])
            if not (idle_w < load_w and abs(float(p["soak_s"]) - 60.0) < 1.0
                    and int(p["iterations"]) >= 1000 and int(p["V"]) == 1024):
                problems.append("%s: soak row %s" % (name, p))
            rate = float(p["Mrow_s_sustained"])
            lat_src = "%s/shapes (XRT, two-batch slope, median of 10 passes)" % d
            pwr_src = "%s/shapes/power_xrt_%s_%dMHz_MIXED.csv (60 s soak)" % (d, name, clock)
            fixed = statistics.median(x[7] for x in per)
            fixed_src = "shape sweep: intercept t(R) - R x slope, median of 11 shapes"
            q = qwen.get(name)
            if q:
                xcheck.append((name, fixed, float(q["overhead_us"]) / float(q["layers"])))
        if [(x[0], x[1], x[2]) for x in per] != ref:
            problems.append("%s: not the 11 shapes of plan_shapes.SHAPES" % name)
            continue
        occ = [x[4] / x[3] for x in per]
        dyn_w = load_w - idle_w
        for (g, M, N, lat, ideal, beats, pad, fx), o in zip(per, occ):
            el = float(M * N)
            long_rows.append(dict(
                build=name, config_label=label, kind=KIND_TEXT[k], engine_shape=shape,
                engines=n, lanes_total=lanes * n, hbm_channels=channels, movers=movers,
                clock_mhz=clock, shape=g, dimensions="%dx%d" % (M, N), M_rows=M, N_cols=N,
                matrix_elements=M * N, padding_rows=pad, latency_us=round(lat, 4),
                ideal_us=round(ideal, 4), occupancy=round(o, 4),
                gflops_delivered=round(2.0 * el / lat / 1e3, 3),
                gflops_silicon=round(4.0 * lanes * (beats if n == 1 else beats) / lat / 1e3, 3)
                if n == 1 else "",
                weight_rate_Mbeats_s=round(beats / lat, 2),
                board_load_W=round(load_w, 3), board_idle_W=round(idle_w, 3),
                energy_static_uJ=round(idle_w * lat, 4), energy_dynamic_uJ=round(dyn_w * lat, 4),
                static_pJ_per_element=round(idle_w * lat / el * 1e6, 3),
                dynamic_pJ_per_element=round(dyn_w * lat / el * 1e6, 3),
                total_pJ_per_element=round(load_w * lat / el * 1e6, 3),
                fixed_cost_us=round(fx, 2) if fx is not None else "",
                latency_source=lat_src, power_source=pwr_src))
        # silicon GFLOPS of a multi-engine build: every engine's computed work over the latency
        if n > 1:
            sx = sweep[name][0]
            for lr, r in zip(long_rows[-11:], sx):
                lr["gflops_silicon"] = round(float(r["GFLOPS_actual"]), 3)
        g9 = [x for x in per if x[0] == G9][0]
        st_pj = gm([idle_w * x[3] / (x[1] * x[2]) * 1e6 for x in per])
        dy_pj = gm([dyn_w * x[3] / (x[1] * x[2]) * 1e6 for x in per])
        gfl = gm([2.0 * x[1] * x[2] / x[3] / 1e3 for x in per])
        eff = min(occ)
        steady = n * lanes * clock / LAP_CYCLES_MIXED_V1024          # Mrow/s at 100 % streaming

        def quad(v, nd):
            return [round(v, nd) if kk == k else "" for kk in KINDS]

        row = dict(config_label=label)
        for kk, x in zip(KINDS, quad(gfl, 2)):
            row["gflops_delivered_" + kk] = x
        for kk, x in zip(KINDS, quad(100.0 * eff, 2)):
            row["efficiency_pct_" + kk] = x
        for kk, s1, d1 in zip(KINDS, quad(st_pj, 2), quad(dy_pj, 2)):
            row["static_pJ_" + kk], row["dynamic_pJ_" + kk] = s1, d1
        for kk, c1, f1 in zip(KINDS, quad(g9[3], 3), quad(fixed if fixed is not None else 0.0, 1)):
            row["compute_G9_us_" + kk], row["fixed_us_" + kk] = c1, (f1 if fixed is not None
                                                                     else "")
        row.update(
            build=name, kind=KIND_TEXT[k], engine_shape=shape, engines=n, lanes_total=lanes * n,
            hbm_channels=channels, movers=movers, clock_mhz=clock,
            gflops_delivered_geomean=round(gfl, 2),
            gflops_silicon_geomean=round(gm([float(x["gflops_silicon"])
                                             for x in long_rows[-11:]]), 2),
            efficiency_pct=round(100.0 * eff, 2),
            occupancy_median=round(statistics.median(occ), 4),
            occupancy_max=round(max(occ), 4),
            occupancy_over_1_shapes=sum(1 for o in occ if o > 1.0),
            weight_rate_Mbeats_s=round(statistics.median(x[5] / x[3] for x in per), 1),
            latency_G9_us=round(g9[3], 3),
            fixed_cost_us=round(fixed, 1) if fixed is not None else "",
            request_G9_us=round(g9[3] + fixed, 1) if fixed is not None else "",
            static_pJ_per_element=round(st_pj, 2), dynamic_pJ_per_element=round(dy_pj, 2),
            total_pJ_per_element=round(st_pj + dy_pj, 2),
            energy_G9_uJ=round(load_w * g9[3], 2),
            soak_load_W=round(load_w, 3), soak_idle_W=round(idle_w, 3),
            soak_dynamic_W=round(dyn_w, 3), soak_rate_pct=round(100.0 * rate / steady, 1),
            latency_source=lat_src, power_source=pwr_src, fixed_cost_source=fixed_src)
        summary.append(row)
    if len(summary) != len(meta):
        problems.append("%d of %d builds complete" % (len(summary), len(meta)))
    if problems:
        fail(problems)

    write_csv(OUT_LONG, long_rows)
    write_csv(OUT_SUM, summary)
    print("\n%-24s %4s %8s %7s %8s %9s %8s %8s %8s %6s %s" % (
        "build", "MHz", "GFLOPS", "eff %", "Mbeat/s", "G9 us", "fixed", "pJ/el", "soak W",
        "soak%", "occ>1"))
    for r in summary:
        print("%-24s %4d %8.1f %7.2f %8.1f %9.3f %8s %8.1f %8.2f %6.1f %s" % (
            r["config_label"], r["clock_mhz"], r["gflops_delivered_geomean"], r["efficiency_pct"],
            r["weight_rate_Mbeats_s"], r["latency_G9_us"], r["fixed_cost_us"],
            r["total_pJ_per_element"], r["soak_load_W"], r["soak_rate_pct"],
            r["occupancy_over_1_shapes"] or ""))
    print("\nfixed cost per calculation, the two methods on the multi-engine builds:")
    for name, fs, fq in xcheck:
        print("   %-14s shape sweep %6.1f us   Qwen (token - engine) / 40 %6.1f us   %+5.1f %%"
              % (name, fs, fq, 100.0 * (fq / fs - 1.0)))
    if a.xlsx:
        write_xlsx(summary, long_rows, meta)


def write_xlsx(summary, long_rows, meta):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    import make_chart_xlsx as mcx

    if os.path.exists(OUT_XLSX):
        raise SystemExit("%s exists -- it may hold charts and a rebuild deletes them; nothing "
                         "written" % os.path.relpath(OUT_XLSX, ROOT))
    if os.path.exists(os.path.join(RES, "~$" + os.path.basename(OUT_XLSX))):
        raise SystemExit("Excel has %s open -- nothing written" % os.path.basename(OUT_XLSX))
    bold = Font(bold=True, size=12)
    by = dict(((r["build"], r["shape"]), r) for r in long_rows)
    srow = dict((r["build"], r) for r in summary)
    label = dict((m[0], "%s @%d" % (m[5].split(" (")[0], m[3])) for m in meta)
    wb = Workbook()
    wb.remove(wb.active)
    ws = wb.create_sheet("Charts")
    ws.cell(row=1, column=1, value="The 8x8 shape study's 11 MIXED matrices on every "
                                   "configuration -- compute-only latency, rate, efficiency, "
                                   "energy, fixed cost").font = bold

    def cell(ws, r, c, v, head=False, fmt=None):
        x = ws.cell(row=r, column=c, value=v)
        x.border = mcx.BOX
        if head:
            x.fill, x.font = mcx.H_FILL, mcx.H_FONT
            x.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        elif fmt and isinstance(v, float):
            x.number_format = fmt
        return x

    mcx.write_sheet(wb, os.path.basename(OUT_SUM), "Per Build")
    for title, builds in (("Latency 4x4", SCALING),
                          ("Latency All", [m[0] for m in meta])):
        ws = wb.create_sheet(title)
        ws.cell(row=1, column=1, value="Latency per matrix (us), MIXED, compute only -- "
                                       "%s" % ("4x4 engines, 1 to 7" if title == "Latency 4x4"
                                               else "every configuration")).font = bold
        for j, h in enumerate(["dimensions"] + [label[b] for b in builds]):
            cell(ws, 2, 1 + j, h, head=True)
        ws.row_dimensions[2].height = 30
        for i, (g, M, N) in enumerate(ps.SHAPES):
            cell(ws, 3 + i, 1, "%dx%d" % (M, N))
            for j, b in enumerate(builds):
                cell(ws, 3 + i, 2 + j, by[(b, g)]["latency_us"], fmt="0.000")
        ws.column_dimensions["A"].width = 14
        for j in range(len(builds)):
            ws.column_dimensions[chr(66 + j) if j < 25 else "A" + chr(65 + j - 25)].width = 14
    # the fastest build of each kind (delivered GFLOPS, geomean): one latency series per kind
    best = []
    for kk in KINDS:
        cand = [r for r in summary if r["kind"] == KIND_TEXT[kk]]
        if cand:
            best.append(max(cand, key=lambda r: r["gflops_delivered_geomean"])["build"])
    ws = wb.create_sheet("Latency Best")
    ws.cell(row=1, column=1, value="Latency per matrix (us), MIXED, compute only -- the fastest "
                                   "configuration of each kind (single engine, multi-tenant own "
                                   "vector, shared vector, broadcast vector)").font = bold
    for j, h in enumerate(["dimensions"] + [label[b] for b in best]):
        cell(ws, 2, 1 + j, h, head=True)
    ws.row_dimensions[2].height = 30
    for i, (g, M, N) in enumerate(ps.SHAPES):
        cell(ws, 3 + i, 1, "%dx%d" % (M, N))
        for j, b in enumerate(best):
            cell(ws, 3 + i, 2 + j, by[(b, g)]["latency_us"], fmt="0.000")
    for c, w in zip("ABCDE", (14, 22, 22, 22, 22)):
        ws.column_dimensions[c].width = w
    # 4x4 engines, 1 to 7: the measured rate against n x the single engine x the clock ratio
    ws = wb.create_sheet("Scaling 4x4")
    ws.cell(row=1, column=1, value="4x4 engines, 1 to 7: delivered GFLOPS (geomean of the 11 MIXED "
                                   "shapes) against the ideal = n x the single 4x4 x clock / its "
                                   "clock").font = bold
    for j, h in enumerate(["engines", "build", "clock MHz", "measured GFLOPS", "ideal GFLOPS",
                           "% of ideal"]):
        cell(ws, 2, 1 + j, h, head=True)
    one = srow[SCALING[0]]
    for i, b in enumerate(SCALING):
        r = srow[b]
        ideal = one["gflops_delivered_geomean"] * r["engines"] * r["clock_mhz"] / float(
            one["clock_mhz"])
        cell(ws, 3 + i, 1, r["engines"])
        cell(ws, 3 + i, 2, label[b])
        cell(ws, 3 + i, 3, r["clock_mhz"])
        cell(ws, 3 + i, 4, r["gflops_delivered_geomean"], fmt="0.0")
        cell(ws, 3 + i, 5, round(ideal, 2), fmt="0.0")
        cell(ws, 3 + i, 6, round(100.0 * r["gflops_delivered_geomean"] / ideal, 2), fmt="0.0")
    for c, w in zip("ABCDEF", (10, 22, 11, 17, 15, 12)):
        ws.column_dimensions[c].width = w
    ws = wb.create_sheet("Twins")
    for t, (title, col, fmt) in enumerate(TWIN_METRICS):
        top = 6 * t + 1
        ws.cell(row=top, column=1, value=title).font = bold
        for j, h in enumerate(["twin set"] + [f for f, _ in FEEDS]):
            cell(ws, top + 1, 1 + j, h, head=True)
        for i, (lab, base) in enumerate(TWINS):
            cell(ws, top + 2 + i, 1, lab)
            for j, (_f, suf) in enumerate(FEEDS):
                v = srow[base + suf][col]
                cell(ws, top + 2 + i, 2 + j, round(float(v), 3), fmt=fmt)
    for c, w in zip("ABCD", (22, 18, 18, 18)):
        ws.column_dimensions[c].width = w
    mcx.write_sheet(wb, os.path.basename(OUT_LONG), "Data")
    ws = wb.create_sheet("Notes")
    lines = [
        "GEMV_Charts_Shapes_Builds.xlsx -- the 8x8 shape study's 11 MIXED matrices on all %d "
        "configurations" % len(summary),
        "Latency per matrix = compute only (launch costs removed), padding included. Single "
        "engines: the family campaign (OpenCL batched runs minus the pooled launch overhead). "
        "Multi-engine builds: the 2026-10-02 shape sweep (XRT), ONE matrix split over all the "
        "engines in lockstep, the busiest engine's time from two batch sizes, median of 10 passes",
        "Efficiency = ideal / latency, the worst of the 11 shapes; the two-batch slope can read "
        "up to ~2 % HIGH where the engines' shares differ slightly (staggered starts) -- "
        "occupancy_over_1_shapes counts the shapes above 1",
        "Energy = soak power x latency (static = idle x latency, dynamic = (load - idle) x "
        "latency); per element = / true M x N; geomean of the 11 shapes. Soaks: V = 1024 MIXED "
        "(singles: family OpenCL soak, lower duty cycle -> their dynamic energy reads a little "
        "low; see soak_rate_pct)",
        "Fixed cost = what one calculation (one lone request) costs on top of its compute, XRT, "
        "ert=false: multi-engine -- the sweep's intercept; singles -- the Qwen test's overhead "
        "per layer. request_G9_us = compute + fixed for one 4096 x 4096 matrix",
        "3 x 4x4 shared and 3 x 8x4 shared stream weights at 225 M beats/s (half the 450 MHz HBM "
        "port clock) on every shape: efficiency 57 % / 70 %, a link-level cap, not the engines",
        "Never rebuild this file once it holds charts (the builder refuses). Sources: "
        "results/GEMV_Shapes_Builds.csv, GEMV_Shapes_Builds_Summary.csv "
        "(scripts/analysis/make_shapes_builds.py)"]
    for i, t in enumerate(lines, start=1):
        c = ws.cell(row=i, column=1, value=t)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        if i == 1:
            c.font = bold
    ws.column_dimensions["A"].width = 140
    wb.save(OUT_XLSX)
    print("wrote %s: %s" % (os.path.relpath(OUT_XLSX, ROOT), ", ".join(wb.sheetnames)))


if __name__ == "__main__":
    main()

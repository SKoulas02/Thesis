"""Build the multi-tenant chart sheets and the cross-configuration comparison.

    python scripts/analysis/make_multi_tenant_csv.py

Inputs : results/multi_tenant/multi_[<shape>_]x<N>_<MHz>MHz*.csv   run_multi_measure.py
                                     (no shape in the name = 4x4, the original study)
         multi_tenant/prep_tenants.py                     the stimulus plan (imported)
         results/family_measurements/<tag>/power_*.csv    family power, 5 soaks each
         results/GEMV_Family_Measured.csv                 the six single-engine builds
Outputs: results/GEMV_MultiTenant_Interference.csv   slowdown of each tenant vs running alone
         results/GEMV_MultiTenant_Throughput.csv     aggregate GMAC/s vs tenants running
         results/GEMV_MultiTenant_Energy.csv         nJ per actual MAC, static + dynamic
         results/GEMV_ScaleOut_vs_ScaleUp.csv        4 x 4x4 vs one 4x24, same 24 channels
         results/GEMV_Idle_vs_Channels.csv           idle board power of every build
         results/GEMV_Configurations.csv             every build side by side: throughput,
                                                     energy per MAC, clock, GMAC/s per channel

ONE BITSTREAM PER SERIES. The tenants-running series (1 .. N) comes from ONE 4x4 bitstream --
the largest measured with --soak-each -- so the clock, the placement and the idle power are
identical at every point of the x-axis and only the number of running tenants changes. That
is x5 (split floorplan, 329 MHz) today.

EVERY BUILD, ONE ROW (Configurations, Idle vs Channels): the six single engines and every
multi-tenant build in MULTI_BUILDS, sorted by the HBM channels they use. A build that has not
been measured yet keeps its row, blank, with status "pending", so the charts already hold its
slot: when its CSV arrives, rerunning this script fills the row and the charts need only the
pasted values, no editing.

WORKLOAD FROM THE FILE NAME: *_all2to32* = every tenant at 2:32 (--all-sparsity 11), anything
else = the mixed plan (each tenant its own mode). Both come from the plan in prep_tenants.py,
imported here and CHECKED against the data: every tenant's rows per calculation must equal
laps x lanes for its planned vector at BEATS weight beats, or nothing is written.

ESTIMATORS -- never mixed within one chart:
  interference  ACTIVE-span ratio (latest input-mover start -> end), mean of 3 reps
  throughput    power-soak GMAC/s: the host's own calculation counts x MACs per
                calculation, sustained over 60 s (launch overhead included)
  energy        that soak's board load and the idle measured right after it:
                static = idle / GMAC/s, dynamic = (load - idle) / GMAC/s, nJ per MAC
  configurations, scale-out
                STEADY STATE on both sides, because the family figures are steady state.
                Multi-tenant: the sum over tenants of MACs / (active span - a) with every
                tenant running, from the LAP MODEL  active = a + (beats + b x laps) / f
                fitted to the alone runs. b (the settle bubble, ~1 cycle per pass over the
                vector) belongs to the engine, so it is pooled over every bitstream of that
                shape; a (XRT's fixed start/finish cost) differs a little between bitstreams,
                so each gets its own. Single engine: the family's ss_gflops_act_MIXED / 2.
                Energy per MAC = MIXED soak power / steady GMAC/s, split static (idle) and
                dynamic (load - idle) -- the family's ss_gflops_act_per_W method.
  idle          mean board and VCCINT idle over every soak of that bitstream

THE OUTER CATEGORY LABEL IS ON THE FIRST ROW OF EACH GROUP ONLY (column workload), as in
GEMV_Family_Mixed_Energy.csv: Excel's multi-level axis starts a new group at every non-blank
outer cell, so a "centred" label shifts every group.

TWO-COLUMN COLOURING (Configurations): every chart metric has a _single and a _multi column,
one of them blank in every row. Charted as two series with 100% overlap, each bar takes the
colour of its kind of build with no per-point formatting.
"""

import csv
import glob
import io
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # repository root; this file is in scripts/analysis/
RES = os.path.join(ROOT, "results")
MT_DIR = os.path.join(RES, "multi_tenant")
FAM_DIR = os.path.join(RES, "family_measurements")

sys.path.insert(0, os.path.join(ROOT, "multi_tenant"))
import prep_tenants as plan                     # noqa: E402  the stimulus plan, one source of truth

OUT = dict(interf="GEMV_MultiTenant_Interference.csv",
           thr="GEMV_MultiTenant_Throughput.csv",
           en="GEMV_MultiTenant_Energy.csv",
           so="GEMV_ScaleOut_vs_ScaleUp.csv",
           idle="GEMV_Idle_vs_Channels.csv",
           cfg="GEMV_Configurations.csv")

BEATS = 2097152                 # prep_tenants.py --target-beats default; CHECKED via row counts
MIN_OVERLAP = 70.0              # below this, a set's tenants were not really running together
ENERGY_TOL = 0.5                # % between the driver's pJ/MAC and load / GMAC/s
PEAK_BAND = (90.0, 100.5)       # steady-state % of peak outside this -> a suspect lap-model fit
WORKLOADS = ["Mixed", "All 2:32"]
FAMILY_ORDER = ["4x4", "8x4", "16x3", "8x8", "4x24", "4x32"]
SCALE_UP = "4x24"               # the single engine with the SAME channel count as 4 x 4x4
SERIES_SHAPE = "4x4"            # the tenants-running series is the 4x4 study

SHAPES = {"4x4": (4, 4), "8x4": (8, 4), "16x3": (16, 3), "8x8": (8, 8),
          "4x24": (4, 24), "4x32": (4, 32)}
# Every multi-tenant build made or in progress: (tenant shape, tenants, floorplan). A build
# with no CSV yet keeps a blank "pending" row, so its chart slot exists from the start.
MULTI_BUILDS = [("4x4", 2, "SLR0"), ("4x4", 3, "SLR0"), ("4x4", 4, "SLR0"), ("4x4", 5, "split"),
                ("8x4", 2, "SLR0"), ("8x4", 3, "split"), ("16x3", 2, "split")]
FAMILY_FLOORPLAN = {"4x4": "SLR0", "8x4": "SLR0", "16x3": "SLR0",
                    "8x8": "split", "4x24": "split", "4x32": "split"}

RUN_NAME = re.compile(r"multi_(?:(\d+x\d+)_)?x(\d+)_(\d+)MHz(.*)\.csv$")


def f(x):
    return float(x) if x not in (None, "") else None


def write_csv(path, rows):
    with io.open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), lineterminator="\r\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def tenants_label(s):
    return "%d tenant%s" % (s, "" if s == 1 else "s")


def lanes_of(shape):
    c, b = SHAPES[shape]
    return c * b


def pcs_of(shape):
    """HBM channels of ONE tenant, derived exactly as the family generator does."""
    T = lanes_of(shape)
    return -(-32 * T // 256) + -(-(10 * T + 2) // 256) + 2 + -(-16 * T // 256)


def plan_for(workload, k):
    """-> (sparsity code, nwin) of tenant k under that workload's plan."""
    if workload == "All 2:32":
        return "11", plan.ALL_SAME_NWIN[k]
    return plan.MEASURE_PLAN[k]


def laps_for(workload, k):
    code, nwin = plan_for(workload, k)
    return BEATS // (nwin * plan.FREEZE[code])


def full_set(n):
    return "+".join(str(k) for k in range(n))


def load_runs(problems):
    runs = []
    for path in sorted(glob.glob(os.path.join(MT_DIR, "multi_*MHz*.csv"))):
        name = os.path.basename(path)
        m = RUN_NAME.match(name)
        if not m:
            continue
        shape = m.group(1) or "4x4"
        if shape not in SHAPES:
            problems.append("%s: unknown engine shape %s" % (name, shape))
            continue
        rows = list(csv.DictReader(io.open(path, encoding="utf-8")))
        if not rows:
            problems.append("%s: empty" % name)
            continue
        n, rest = int(m.group(2)), m.group(4)
        run = dict(name=name, shape=shape, lanes=lanes_of(shape), pcs=pcs_of(shape),
                   macs_per_calc=BEATS * 2 * lanes_of(shape), n=n,
                   workload="All 2:32" if "all2to32" in rest else "Mixed",
                   note=rest.strip("_"), clock=f(rows[0]["clock_mhz"]),
                   xclbin=rows[0]["xclbin"],
                   tenant_rows=[r for r in rows if r["tenant"] != "soak"],
                   soaks=[r for r in rows if r["tenant"] == "soak"])
        for r in rows:
            if int(r["tenants_in_bitstream"]) != n:
                problems.append("%s: a row says %s tenants in the bitstream"
                                % (name, r["tenants_in_bitstream"]))
                break
            if f(r["clock_mhz"]) != run["clock"]:
                problems.append("%s: more than one clock in the file" % name)
                break
        runs.append(run)
    return runs


def soak_each(run):
    """{set size: soak row} if the run soaked EVERY occupancy level with a MAC count."""
    got = {}
    for r in run["soaks"]:
        if f(r.get("gmac_s_actual")) and f(r.get("board_load_w")):
            got[int(r["set_size"])] = r
    return got if sorted(got) == list(range(1, run["n"] + 1)) else None


def full_soak(run):
    """The soak of ALL tenants with board power, or None."""
    for r in run["soaks"]:
        if int(r["set_size"]) == run["n"] and f(r.get("board_load_w")) and f(r.get("board_idle_w")):
            return r
    return None


def check_run(run, problems):
    """Plan, completeness, overlap and energy arithmetic of one run."""
    n, wl, name = run["n"], run["workload"], run["name"]
    by = {}
    for r in run["tenant_rows"]:
        by[(r["set"], int(r["tenant"]))] = r
        code, nwin = plan_for(wl, int(r["tenant"]))
        want = laps_for(wl, int(r["tenant"])) * run["lanes"]
        if int(r["rows_computed"]) != want:
            problems.append("%s t%s: %s rows per calculation, the %s plan at %d beats on %s gives "
                            "%d (%s, V=%d) -- different stimulus?"
                            % (name, r["tenant"], r["rows_computed"], wl, BEATS, run["shape"],
                               want, plan.SP_NAME[code], 32 * nwin))
    for k in range(n):
        r = by.get((str(k), k))
        if r is None:
            problems.append("%s: no alone run for t%d" % (name, k))
        elif f(r["interference_active"]) != 1.0:
            problems.append("%s: t%d alone has interference %s, not 1" % (name, k,
                                                                          r["interference_active"]))
    for s in range(2, n + 1):
        label = full_set(s)
        for k in range(s):
            r = by.get((label, k))
            if r is None:
                problems.append("%s: set {%s} has no row for t%d" % (name, label, k))
            elif f(r["active_overlap_pct"]) is None or f(r["active_overlap_pct"]) < MIN_OVERLAP:
                problems.append("%s: set {%s} active overlap %s%% < %.0f%%"
                                % (name, label, r["active_overlap_pct"], MIN_OVERLAP))
    se = soak_each(run) or {}
    for s, r in sorted(se.items()):
        load, idle, gm = f(r["board_load_w"]), f(r["board_idle_w"]), f(r["gmac_s_actual"])
        if not 0 < idle < load:
            problems.append("%s soak %d: idle %.2f W vs load %.2f W" % (name, s, idle, load))
        pj = f(r["energy_per_mac_pJ"])
        if abs(100.0 * (pj / (load / gm * 1000.0) - 1.0)) > ENERGY_TOL:
            problems.append("%s soak %d: %.1f pJ/MAC but load/GMAC gives %.1f"
                            % (name, s, pj, load / gm * 1000.0))
    return by, se


def alone_spread(run, k):
    r = [x for x in run["tenant_rows"] if x["set"] == str(k) and int(x["tenant"]) == k][0]
    xs = [float(v) for v in r["active_runs"].split()]
    return 100.0 * (max(xs) - min(xs)) / min(xs)


def alone_points(run):
    """(laps / f, active - beats / f, active) for every tenant's ALONE run."""
    pts = []
    for k in range(run["n"]):
        r = [x for x in run["tenant_rows"] if x["set"] == str(k) and int(x["tenant"]) == k]
        if r:
            act = float(r[0]["active_us_mean"])
            pts.append((laps_for(run["workload"], k) / run["clock"],
                        act - BEATS / run["clock"], act))
    return pts


def lap_fits(runs):
    """The lap model  active = a + (beats + b x laps) / f,  b per SHAPE, a per BITSTREAM.

    Demeaning within each bitstream gives the pooled slope with a separate intercept for
    every bitstream, so a two-tenant run (two points) still gets a well-defined a.
    -> {xclbin: (a, b)}, [(shape, xclbin, a, b, points, worst residual %)]
    """
    fits, report = {}, []
    for shape in sorted(set(r["shape"] for r in runs)):
        groups = {}
        for r in runs:
            if r["shape"] == shape:
                groups.setdefault(r["xclbin"], []).extend(alone_points(r))
        sxy = sxx = 0.0
        for pts in groups.values():
            mx = statistics.mean(p[0] for p in pts)
            my = statistics.mean(p[1] for p in pts)
            sxy += sum((x - mx) * (y - my) for x, y, _ in pts)
            sxx += sum((x - mx) ** 2 for x, _, _ in pts)
        b = sxy / sxx
        for xcl, pts in sorted(groups.items()):
            a = statistics.mean(y - b * x for x, y, _ in pts)
            worst = max(abs(y - a - b * x) / act * 100.0 for x, y, act in pts)
            fits[xcl] = (a, b)
            report.append((shape, xcl, a, b, len(pts), worst))
    return fits, report


def pick_run(runs, shape, nt):
    """The Mixed run of that build with a full-set power soak, at its HIGHEST clock.

    One build can have several bitstreams (2 x 4x4: 375 closed, 400 missed -> 394). The
    fastest one that was measured is the build's operating point; its all-2:32 run and
    soaks come from the same xclbin.
    """
    cands = [r for r in runs if r["shape"] == shape and r["n"] == nt
             and r["workload"] == "Mixed" and full_soak(r)]
    return max(cands, key=lambda r: (r["clock"], r["name"])) if cands else None


def steady_gmac(run, a):
    """Every tenant running: sum of MACs / (active span - fixed cost), in GMAC/s."""
    by = {(r["set"], int(r["tenant"])): r for r in run["tenant_rows"]}
    label = full_set(run["n"])
    return sum(run["macs_per_calc"] / (float(by[(label, k)]["active_us_mean"]) - a)
               for k in range(run["n"])) / 1000.0


def config_row(single, label, **v):
    """One build. Each chart metric lands in its _single OR its _multi column."""
    g, load, idle, clk, ch = v.get("gmac"), v.get("load"), v.get("idle"), v.get("clock"), v["channels"]

    def two(value, nd):
        x = round(value, nd) if value is not None else ""
        return (x, "") if single else ("", x)

    st = idle / g if (g and idle is not None) else None
    dy = (load - idle) / g if (g and load is not None) else None
    pc = g / ch if g else None
    row = {"config_label": label}
    row["gmac_s_single"], row["gmac_s_multi"] = two(g, 3)
    row["static_nJ_per_MAC_single"], row["dynamic_nJ_per_MAC_single"] = (
        (round(st, 4), round(dy, 4)) if (single and g) else ("", ""))
    row["static_nJ_per_MAC_multi"], row["dynamic_nJ_per_MAC_multi"] = (
        (round(st, 4), round(dy, 4)) if (not single and g) else ("", ""))
    row["clock_mhz_single"], row["clock_mhz_multi"] = two(clk, 0)
    row["gmac_s_per_channel_single"], row["gmac_s_per_channel_multi"] = two(pc, 4)
    lanes = v["lanes"]
    row.update(
        kind="single engine" if single else "multi-tenant",
        configuration=v["configuration"], engine_shape=v["shape"], tenants=v["tenants"],
        hbm_channels=ch, lanes_total=lanes, macs_per_cycle=2 * lanes,
        clock_mhz=clk if clk is not None else "",
        gmac_s=round(g, 3) if g else "",
        pct_of_peak=round(100.0 * g / (2 * lanes * clk / 1000.0), 2) if (g and clk) else "",
        gmac_s_per_channel=round(pc, 4) if pc else "",
        energy_total_nJ_per_MAC=round(load / g, 4) if (g and load) else "",
        board_load_W=load if load is not None else "",
        board_idle_W=idle if idle is not None else "",
        floorplan=v["floorplan"], status=v["status"], source=v["source"],
        estimator=v.get("estimator", ""))
    return row


def main():
    problems = []
    runs = load_runs(problems)
    checked_all = {r["name"]: check_run(r, problems) for r in runs}

    # ---- the tenant series: the largest 4x4 bitstream measured with --soak-each ----------
    cands = [r for r in runs if r["shape"] == SERIES_SHAPE and r["workload"] == "Mixed"
             and soak_each(r)]
    if not cands:
        raise SystemExit("no 4x4 multi_x*.csv with --soak-each data in %s" % MT_DIR)
    n = max(r["n"] for r in cands)
    series = {}
    for r in runs:
        if r["shape"] == SERIES_SHAPE and r["n"] == n and soak_each(r):
            if r["workload"] in series:
                problems.append("two %s runs for x%d: %s and %s -- keep one"
                                % (r["workload"], n, series[r["workload"]]["name"], r["name"]))
            series[r["workload"]] = r
    wls = [w for w in WORKLOADS if w in series]
    clk = series["Mixed"]["clock"]
    for w in wls:
        if series[w]["clock"] != clk:
            problems.append("x%d: %s at %.0f MHz but Mixed at %.0f" % (n, w, series[w]["clock"], clk))
    checked = {w: checked_all[series[w]["name"]] for w in wls}

    # ---- family reference data ---------------------------------------------------------
    fam, fam_power = {}, {}
    with io.open(os.path.join(RES, "GEMV_Family_Measured.csv"), encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            fam[r["family"].split()[0]] = r
    for tag in FAMILY_ORDER:
        if tag not in fam:
            problems.append("GEMV_Family_Measured.csv has no %s row" % tag)
            continue
        if int(fam[tag]["hbm_pcs"]) != pcs_of(tag):
            problems.append("%s: %s HBM channels in the family CSV, the derivation gives %d"
                            % (tag, fam[tag]["hbm_pcs"], pcs_of(tag)))
        pw = glob.glob(os.path.join(FAM_DIR, tag, "power_*.csv"))
        if len(pw) != 1:
            problems.append("%s: expected one power CSV, found %d" % (tag, len(pw)))
            continue
        prow = list(csv.DictReader(io.open(pw[0], encoding="utf-8")))
        mixed = [r for r in prow if r["label"].split()[-1] == "MIXED"]
        if len(mixed) != 1:
            problems.append("%s: %d MIXED soak rows in %s" % (tag, len(mixed), pw[0]))
            continue
        fam_power[tag] = dict(rows=prow, pw=pw[0], load=float(mixed[0]["board_load_w"]),
                              idle=float(mixed[0]["board_idle_w"]))
        if abs(fam_power[tag]["load"] / float(fam[tag]["soak_board_W_MIXED"]) - 1.0) > 0.005:
            problems.append("%s: MIXED soak load %.3f W in the power CSV, %s W in the family CSV"
                            % (tag, fam_power[tag]["load"], fam[tag]["soak_board_W_MIXED"]))

    fits, fit_report = lap_fits(runs)

    if problems:
        print("CHECKS FAILED -- nothing written:")
        for p in dict.fromkeys(problems):       # a tenant appears in several sets: say it once
            print("   ", p)
        raise SystemExit(1)

    xclbin = series["Mixed"]["xclbin"]
    lanes_s = lanes_of(SERIES_SHAPE)
    est_int = "active-span ratio vs the same tenant alone, mean of 3 reps"
    est_soak = "60 s power soak, host calculation counts x MACs per calculation"

    # ---- 1. interference: A:B two-level category, C.. one column per tenant -------------
    int_rows = []
    for w in wls:
        by, _ = checked[w]
        noise = max(alone_spread(series[w], k) for k in range(n))
        for s in range(2, n + 1):
            label = full_set(s)
            row = dict(workload=w if s == 2 else "", tenants_running=tenants_label(s))
            for k in range(n):
                v = f(by[(label, k)]["interference_active"]) if k < s else None
                row["slowdown_pct_t%d" % k] = round((v - 1.0) * 100.0, 3) if v else ""
            for k in range(n):
                row["interference_t%d" % k] = f(by[(label, k)]["interference_active"]) if k < s else ""
            row.update(active_overlap_pct=f(by[(label, 0)]["active_overlap_pct"]),
                       alone_spread_pct_max=round(noise, 3), workload_name=w, tenants_n=s,
                       bitstream=xclbin, clock_mhz=clk, estimator=est_int)
            int_rows.append(row)

    # ---- 2. throughput: A category, B:C measured, D:E reference lines -------------------
    se = {w: checked[w][1] for w in wls}
    peak1 = 2 * lanes_s * clk / 1000.0
    one = float(se["Mixed"][1]["gmac_s_actual"])
    thr_rows = []
    for s in range(1, n + 1):
        g = {w: float(se[w][s]["gmac_s_actual"]) for w in wls}
        thr_rows.append(dict(
            tenants_running=tenants_label(s),
            gmac_s_mixed=round(g["Mixed"], 3),
            gmac_s_all2to32=round(g["All 2:32"], 3) if "All 2:32" in g else "",
            gmac_s_linear_scaling=round(s * one, 3),
            gmac_s_peak=round(s * peak1, 3),
            scaling_x_mixed=round(g["Mixed"] / one, 3),
            scaling_x_all2to32=(round(g["All 2:32"] / float(se["All 2:32"][1]["gmac_s_actual"]), 3)
                                if "All 2:32" in g else ""),
            pct_of_peak_mixed=round(100.0 * g["Mixed"] / (s * peak1), 2),
            pct_of_peak_all2to32=(round(100.0 * g["All 2:32"] / (s * peak1), 2)
                                  if "All 2:32" in g else ""),
            tenants_n=s, bitstream=xclbin, clock_mhz=clk, estimator=est_soak))

    # ---- 3. energy per MAC: A:B two-level category, C:D the two stacks ------------------
    en_rows = []
    for w in wls:
        for s in range(1, n + 1):
            r = se[w][s]
            load, idle, gm = f(r["board_load_w"]), f(r["board_idle_w"]), f(r["gmac_s_actual"])
            en_rows.append(dict(
                workload=w if s == 1 else "", tenants_running=tenants_label(s),
                static_nJ_per_MAC=round(idle / gm, 4),
                dynamic_nJ_per_MAC=round((load - idle) / gm, 4),
                total_nJ_per_MAC=round(load / gm, 4),
                gmac_s=round(gm, 3), board_load_W=load, board_idle_W=idle,
                board_dynamic_W=round(load - idle, 3),
                vccint_load_W=f(r["vccint_load_w"]), vccint_idle_W=f(r["vccint_idle_w"]),
                static_pct=round(100.0 * idle / load, 2), workload_name=w, tenants_n=s,
                bitstream=xclbin, clock_mhz=clk,
                estimator=est_soak + "; static = idle after the soak, dynamic = load - idle"))

    # ---- 4. scale-out vs scale-up at 24 channels: 4 x 4x4 (x4 bitstream) vs one 4x24 ---
    so_rows = []
    x4_mixed = [r for r in runs if r["shape"] == "4x4" and r["n"] == 4
                and r["workload"] == "Mixed" and soak_each(r)]
    up = fam[SCALE_UP]
    if x4_mixed and int(up["hbm_pcs"]) == 4 * pcs_of("4x4"):
        m = x4_mixed[0]
        a = fits[m["xclbin"]][0]
        steady = steady_gmac(m, a)
        soak4 = soak_each(m)[4]
        p_out, p_up = float(soak4["board_load_w"]), float(up["soak_board_W_MIXED"])
        g_up = float(up["ss_gflops_act_MIXED"]) / 2.0
        up_clk = float(up["freq_closed_mhz"])
        metrics = [
            ("HBM channels", 4 * pcs_of("4x4"), int(up["hbm_pcs"]), "channels",
             "the controlled variable: the same channel budget"),
            ("Kernel clock", m["clock"], up_clk, "MHz", "runtime clock of each bitstream"),
            ("MACs per cycle", 4 * 2 * lanes_of("4x4"), 2 * int(up["blocks_total"]), "MACs/cycle",
             "4 x 32 vs 96 blocks x 2 (DSPs 512 vs %s)" % up["dsps"]),
            ("Throughput", round(steady, 3), round(g_up, 3), "GMAC/s",
             "steady state: x4 = MACs / (active span - %.0f us fixed cost), all four tenants "
             "running; 4x24 = ss_gflops_act_MIXED / 2" % a),
            ("Board power", p_out, p_up, "W", "60 s MIXED soak, board load"),
            ("Energy per MAC", round(p_out / steady, 4), round(p_up / g_up, 4), "nJ/MAC",
             "soak board power / steady-state GMAC/s (the family's per-W method)"),
        ]
        for name, v_out, v_up, unit, basis in metrics:
            so_rows.append(dict(metric=name, ratio_scaleout_over_scaleup=round(v_out / v_up, 4),
                                scaleout_value=v_out, scaleup_value=v_up, unit=unit,
                                scaleout="4 x 4x4 tenants, %s" % m["xclbin"],
                                scaleup="one 4x24 engine, family build", basis=basis))
    else:
        print("NOTE: no x4 soak-each run or 4x24 is not 24 channels -- scale-out sheet skipped")

    # ---- 5. every build: throughput, energy per MAC, clock, GMAC/s per channel ----------
    cfg_rows = []
    est_fam = "steady state: ss_gflops_act_MIXED / 2; energy = MIXED soak power / that"
    est_mt = ("steady state: sum over tenants of MACs / (active span - a), all tenants running; "
              "energy = full-set MIXED soak power / that")
    for j, tag in enumerate(FAMILY_ORDER):
        r = fam[tag]
        row = config_row(True, "%s (%d ch)" % (tag, int(r["hbm_pcs"])),
                         gmac=float(r["ss_gflops_act_MIXED"]) / 2.0, load=fam_power[tag]["load"],
                         idle=fam_power[tag]["idle"], clock=float(r["freq_closed_mhz"]),
                         channels=int(r["hbm_pcs"]), lanes=int(r["blocks_total"]),
                         configuration="one %s engine" % tag, shape=tag, tenants=1,
                         floorplan=FAMILY_FLOORPLAN[tag], status="measured",
                         source="GEMV_Family_Measured.csv, %s"
                                % os.path.relpath(fam_power[tag]["pw"], RES).replace("\\", "/"),
                         estimator=est_fam)
        row["_order"] = (int(r["hbm_pcs"]), 0, int(r["blocks_total"]))
        cfg_rows.append(row)
    for shape, nt, fp in MULTI_BUILDS:
        ch, lanes = nt * pcs_of(shape), nt * lanes_of(shape)
        label = "%d x %s (%d ch)" % (nt, shape, ch)
        run = pick_run(runs, shape, nt)
        common = dict(channels=ch, lanes=lanes, configuration="%d x %s tenants" % (nt, shape),
                      shape=shape, tenants=nt, floorplan=fp)
        if run is None:
            row = config_row(False, label, status="pending: not measured yet", source="",
                             **common)
        else:
            soak = full_soak(run)
            g = steady_gmac(run, fits[run["xclbin"]][0])
            row = config_row(False, label, gmac=g, load=float(soak["board_load_w"]),
                             idle=float(soak["board_idle_w"]), clock=run["clock"],
                             status="measured", source="%s (%s)" % (run["name"], run["xclbin"]),
                             estimator=est_mt, **common)
        row["_order"] = (ch, 1, lanes)
        cfg_rows.append(row)
    cfg_rows.sort(key=lambda r: r["_order"])
    for r in cfg_rows:
        del r["_order"]
        if r["pct_of_peak"] != "" and not PEAK_BAND[0] <= r["pct_of_peak"] <= PEAK_BAND[1]:
            problems.append("%s: steady state is %.1f%% of peak -- outside %s, the fit is suspect"
                            % (r["config_label"], r["pct_of_peak"], PEAK_BAND))

    # ---- 6. idle power vs HBM channels -------------------------------------------------
    idle_rows = []
    for j, tag in enumerate(FAMILY_ORDER):
        rows = fam_power[tag]["rows"]
        b = statistics.mean(float(r["board_idle_w"]) for r in rows)
        v = statistics.mean(float(r["vccint_idle_w"]) for r in rows)
        ch = int(fam[tag]["hbm_pcs"])
        idle_rows.append(dict(build_label="%s (%d ch)" % (tag, ch), idle_vccint_W=round(v, 3),
                              idle_rest_W=round(b - v, 3), idle_board_W=round(b, 3),
                              hbm_channels=ch, build=tag, kind="single engine",
                              clock_mhz=float(fam[tag]["freq_closed_mhz"]),
                              uses_channel_16_up="yes" if ch > 16 else "no", n_soaks=len(rows),
                              source=os.path.relpath(fam_power[tag]["pw"], ROOT).replace("\\", "/"),
                              _order=(ch, 0, int(fam[tag]["blocks_total"]))))
    for shape, nt, fp in MULTI_BUILDS:
        chosen = pick_run(runs, shape, nt)          # same bitstream as the Configurations row
        mine = ([r for r in runs if r["xclbin"] == chosen["xclbin"]] if chosen
                else [r for r in runs if r["shape"] == shape and r["n"] == nt])
        soaks = [s for r in mine for s in r["soaks"]
                 if f(s.get("board_idle_w")) and f(s.get("vccint_idle_w"))]
        ch = nt * pcs_of(shape)
        row = dict(build_label="%d x %s (%d ch)" % (nt, shape, ch), idle_vccint_W="",
                   idle_rest_W="", idle_board_W="", hbm_channels=ch,
                   build="%s_x%d" % (shape, nt), kind="multi-tenant", clock_mhz="",
                   uses_channel_16_up="yes" if ch > 16 else "no", n_soaks=len(soaks),
                   source="pending: not measured yet", _order=(ch, 1, nt * lanes_of(shape)))
        if soaks:
            b = statistics.mean(float(s["board_idle_w"]) for s in soaks)
            v = statistics.mean(float(s["vccint_idle_w"]) for s in soaks)
            row.update(idle_vccint_W=round(v, 3), idle_rest_W=round(b - v, 3),
                       idle_board_W=round(b, 3),
                       clock_mhz=sorted(set(r["clock"] for r in mine))[0],
                       source=", ".join(r["name"] for r in mine))
        idle_rows.append(row)
    idle_rows.sort(key=lambda r: r["_order"])
    for r in idle_rows:
        del r["_order"]

    if problems:
        print("CHECKS FAILED -- nothing written:")
        for p in dict.fromkeys(problems):
            print("   ", p)
        raise SystemExit(1)

    # ---- write ---------------------------------------------------------------------------
    outs = [("interf", int_rows), ("thr", thr_rows), ("en", en_rows), ("so", so_rows),
            ("idle", idle_rows), ("cfg", cfg_rows)]
    for key, rows in outs:
        if rows:
            write_csv(os.path.join(RES, OUT[key]), rows)
            print("wrote results/%s : %d rows x %d cols" % (OUT[key], len(rows), len(rows[0])))

    # ---- console summary -----------------------------------------------------------------
    print("\nseries bitstream: x%d, %s @ %.0f MHz, workloads: %s" % (n, xclbin, clk, ", ".join(wls)))
    print("\nslowdown vs alone (%)   " + "".join("%8s" % ("t%d" % k) for k in range(n))
          + "   overlap")
    for r in int_rows:
        print("  %-9s %-10s" % (r["workload_name"], r["tenants_running"]) + "".join(
            "%8s" % ("%+.2f" % r["slowdown_pct_t%d" % k] if r["slowdown_pct_t%d" % k] != "" else "")
            for k in range(n)) + "   %5.1f%%" % r["active_overlap_pct"])
    print("\nlap model  active = a + (beats + b x laps) / f   (b per shape, a per bitstream)")
    for shape, xcl, a, b, npts, worst in fit_report:
        print("  %-5s %-34s a %6.1f us  b %.3f  %2d alone runs, worst %.2f%%"
              % (shape, xcl, a, b, npts, worst))
    if so_rows:
        print("\nscale-out (4 x 4x4) / scale-up (4x24):")
        for r in so_rows:
            print("  %-15s %10s vs %-10s %-10s ratio %.3f" % (r["metric"], r["scaleout_value"],
                  r["scaleup_value"], r["unit"], r["ratio_scaleout_over_scaleup"]))
    print("\nevery build (steady state)      MHz  GMAC/s  %peak  per ch  nJ/MAC  status")
    for r in cfg_rows:
        print("  %-22s %8s %7s %6s %7s %7s  %s"
              % (r["config_label"], r["clock_mhz"], r["gmac_s"], r["pct_of_peak"],
                 r["gmac_s_per_channel"], r["energy_total_nJ_per_MAC"], r["status"]))
    print("\nidle power            board   VCCINT   rest")
    for r in idle_rows:
        print("  %-20s %6s  %6s  %6s" % (r["build_label"], r["idle_board_W"],
              r["idle_vccint_W"], r["idle_rest_W"]))
    print("\nnext: python scripts/analysis/make_chart_xlsx.py")


if __name__ == "__main__":
    main()

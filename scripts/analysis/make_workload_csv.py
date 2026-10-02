"""Build the workload-study chart table: the same 132 matrices on all 13 architectures.

    python scripts/analysis/make_workload_csv.py

Inputs : results/workload/workload_<arch>_<MHz>MHz.csv        workload/run_workload.py, one
         results/workload/workload_<arch>_<MHz>MHz_calcs.csv  summary + every calculation
         workload/plan_workload.py                  the plan (imported, and checked)
         results/GEMV_Configurations.csv             each build's measured steady-state
                                                     efficiency, full-rate power and label
Output : results/GEMV_Workload.csv                   one row per build, sorted like
                                                     Configurations (by HBM channels)

TWO RUNS, TWO TABLES, the same columns and checks (a run folder that is absent is skipped):
  shared      results/workload/workload_<arch>_...            -> GEMV_Workload.csv
              matrices of one width share a calculation and its input vector (2026-09-25)
  per-matrix  results/workload_per_matrix/workload_permatrix_<arch>_...
                                                              -> GEMV_Workload_PerMatrix.csv
              every matrix its own calculation and vector (the multi-user inference case)
Each is checked against plan_workload.plan(arch, <its mode>).

WHAT IS MEASURED AND WHAT IS DERIVED -- the two are never mixed in one column:

  wall time per pass      MEASURED: a 60 s soak of whole workload passes, seconds / passes.
                          Every launch, every host step and the tenants' lockstep included.
  energy per pass         MEASURED: board power during that soak x wall time per pass;
                          static = idle power (sampled right after, bitstream loaded) x wall
                          time, dynamic = (load - idle) x wall time.
  active time             MEASURED: the slowest tenant's sum of per-calculation active times.
                          An upper bound on engine time (each calculation carries a fixed
                          completion cost), used here only as a consistency check.
  finish balance          MEASURED: earliest tenant's finish / latest's, per pass.
  engine time             DERIVED: the plan's cycles (beats + 1 per lap, the slowest tenant,
                          padding included) / clock / the build's steady-state efficiency
                          from GEMV_Configurations.csv (pct_of_peak, measured on the same
                          2:4..2:32 mix in the earlier campaigns). The time the workload
                          would take if launching were free.
  overhead                wall - engine: launches, completion, host thread steps.
  launch-free energy      DERIVED: that engine time x the build's full-rate soak power
                          (Configurations board_load_W).

WHY ENGINE TIME IS NOT FITTED FROM THIS RUN: the per-calculation active times carry a fixed
cost of 60-370 us that varies by +-17-70 us from calculation to calculation, and there are
only seven calculation sizes per tenant; a fit of intercept and slope gives slopes below 1
(faster than one beat per cycle -- impossible) on most builds. See workload/README.md.

CHECKS (nothing is written if any fails): the 13 architectures of plan_workload.ARCHS, each
exactly once; the plan's useful/padded MACs and predicted time equal the run's; every timed
calculation's beats and laps equal the plan's; the clock equals the plan's and
Configurations'; energy = load power x wall time; active time >= engine time.

Chart columns come in _single / _multi pairs (one blank per row), so two series at 100%
overlap colour each bar by its kind, as in the Configurations sheet.
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
RESULTS = os.path.join(ROOT, "results")
sys.path.insert(0, os.path.join(ROOT, "workload"))
import plan_workload as pw                      # noqa: E402

CONFIGS = os.path.join(RESULTS, "GEMV_Configurations.csv")
# vectors mode, run folder under results/, file prefix, output table
MODES = [
    ("shared", "workload", "workload_", "GEMV_Workload.csv"),
    ("per-matrix", "workload_per_matrix", "workload_permatrix_", "GEMV_Workload_PerMatrix.csv"),
    ("moe", "workload_moe", "workload_moe_", "GEMV_Workload_MoE.csv"),
]
TWINS_OUT = os.path.join(RESULTS, "GEMV_Shared_Twins.csv")
# MoE on a shared-vector build: its engines' steady-state efficiency is taken from its twin
# (same engines, same count) or, with no twin, from the single engine of that shape
EFFICIENCY_FROM = {"3x4x4_shared": "3x4x4", "3x8x4_shared": "3x8x4",
                   "2x16x3_shared": "2x16x3"}
TWIN_OF = {"3x4x4_shared": "3x4x4", "3x8x4_shared": "3x8x4", "2x16x3_shared": "2x16x3"}


def fail(msg):
    raise SystemExit("make_workload_csv: " + msg + " -- nothing written")


def read_csv(path):
    with io.open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_runs(run_dir, prefix, vectors):
    """-> {arch: (summary row, calculation rows)}, or None when the folder holds no run."""
    pat = re.compile(re.escape(prefix) + ("(?!permatrix_)" if vectors == "shared" else "")
                     + r"(.+)_(\d+)MHz\.csv$")
    runs = {}
    for path in sorted(glob.glob(os.path.join(run_dir, prefix + "*MHz.csv"))):
        m = pat.match(os.path.basename(path))
        if not m:
            continue
        rows = read_csv(path)
        if len(rows) != 1:
            fail("%s has %d rows, expected 1" % (path, len(rows)))
        s = rows[0]
        if s["arch"] != m.group(1) or int(s["clock_mhz"]) != int(m.group(2)):
            fail("%s: file name and contents disagree" % path)
        if s.get("vectors", "shared") != vectors:
            fail("%s was run with vectors %s, this table is %s"
                 % (path, s.get("vectors", "shared"), vectors))
        if s["arch"] in runs:
            fail("two runs of %s" % s["arch"])
        calcs = path[:-4] + "_calcs.csv"
        if not os.path.exists(calcs):
            fail("no %s" % calcs)
        runs[s["arch"]] = (s, read_csv(calcs))
    if not runs:
        return None
    want = [a[0] for a in pw.archs_for(vectors)]
    if sorted(runs) != sorted(want):
        fail("architectures found %s, expected %s" % (sorted(runs), sorted(want)))
    return runs


def check_run(arch, s, calcs, plan):
    clk = int(s["clock_mhz"])
    if clk != plan["clock_mhz"]:
        fail("%s ran at %d MHz, the plan says %d" % (arch, clk, plan["clock_mhz"]))
    for k in ("useful_macs", "padded_macs"):
        if int(s[k]) != plan[k]:
            fail("%s: %s %s, the plan now says %d -- the plan changed" % (arch, k, s[k], plan[k]))
    if abs(float(s["predicted_us"]) - plan["predicted_cycles"] / clk) > 0.06:
        fail("%s: predicted time differs from the plan" % arch)
    npass = len(set(r["pass_"] for r in calcs))
    ncalc = sum(len(t["calcs"]) for t in plan["tenant_plans"])
    if len(calcs) != npass * ncalc:
        fail("%s: %d calculation rows, expected %d passes x %d" % (arch, len(calcs), npass, ncalc))
    for r in calcs:
        c = plan["tenant_plans"][int(r["tenant"])]["calcs"][int(r["calc"])]
        if int(r["beats"]) != c["beats"] or int(r["laps"]) != c["laps"]:
            fail("%s tenant %s calc %s: %s beats / %s laps, the plan says %d / %d"
                 % (arch, r["tenant"], r["calc"], r["beats"], r["laps"], c["beats"], c["laps"]))
    e = float(s["board_load_W"]) * float(s["wall_pass_us"])
    if abs(e - float(s["energy_wall_uJ"])) > 1e-3 * e:
        fail("%s: energy per pass is not load power x wall time" % arch)
    return npass


def main():
    configs = read_csv(CONFIGS)
    for vectors, folder, prefix, table in MODES:
        run_dir = os.path.join(RESULTS, folder)
        print("== vectors %s: %s" % (vectors, os.path.relpath(run_dir, ROOT)))
        runs = load_runs(run_dir, prefix, vectors)
        if runs is None:
            print("no runs there -- %s not written" % table)
            continue
        if vectors == "moe":
            build_moe(folder, prefix, runs, configs, os.path.join(RESULTS, table))
        else:
            build(vectors, folder, prefix, runs, configs, os.path.join(RESULTS, table))


def build(vectors, folder, prefix, runs, configs, out_path):
    by_key = dict(((a[1], a[2]), a[0]) for a in pw.ARCHS)       # (shape, tenants) -> arch
    out = []
    print("%-17s %9s %9s %9s %7s %8s %8s %7s  %s" % ("build", "engine us", "active us",
          "wall us", "ovh %", "GMAC/s", "wall", "nJ/MAC", "idle now/before W"))
    for c in configs:
        arch = by_key.get((c["engine_shape"], int(c["tenants"])))
        if arch is None:
            fail("no workload architecture for Configurations row %s" % c["config_label"])
        s, calcs = runs[arch]
        plan = pw.plan(arch, vectors)
        npass = check_run(arch, s, calcs, plan)
        clk = int(s["clock_mhz"])
        if clk != int(float(c["clock_mhz"])):
            fail("%s: clock %d here, %s in Configurations" % (arch, clk, c["clock_mhz"]))

        eff = float(c["pct_of_peak"]) / 100.0
        engine_us = plan["predicted_cycles"] / clk / eff
        active_us = float(s["active_sum_us"])
        if active_us < engine_us:
            fail("%s: active time %.1f us < engine time %.1f us -- the efficiency does not "
                 "fit this run" % (arch, active_us, engine_us))
        wall_us = float(s["wall_pass_us"])
        if wall_us < engine_us:
            fail("%s: a pass took %.1f us in the soak, less than the %.1f us the engines need "
                 "-- impossible" % (arch, wall_us, engine_us))
        load_w, idle_w = float(s["board_load_W"]), float(s["board_idle_W"])
        macs = int(s["useful_macs"])
        static_mj = idle_w * wall_us / 1e3
        dynamic_mj = (load_w - idle_w) * wall_us / 1e3
        full_w = float(c["board_load_W"])
        single = c["kind"] == "single engine"

        def pair(v, nd):
            v = round(v, nd)
            return (v, "") if single else ("", v)

        # stacked pairs in chart-series order: single (bottom, top), then multi (bottom, top)
        eng, ovh = pair(engine_us / 1e3, 3), pair((wall_us - engine_us) / 1e3, 3)
        sta, dyn = pair(static_mj, 2), pair(dynamic_mj, 2)
        row = dict(config_label=c["config_label"])
        row["engine_ms_single"], row["overhead_ms_single"] = eng[0], ovh[0]
        row["engine_ms_multi"], row["overhead_ms_multi"] = eng[1], ovh[1]
        row["static_mJ_single"], row["dynamic_mJ_single"] = sta[0], dyn[0]
        row["static_mJ_multi"], row["dynamic_mJ_multi"] = sta[1], dyn[1]
        row["wall_gmac_s_single"], row["wall_gmac_s_multi"] = pair(macs / wall_us / 1e3, 3)
        row["engine_gmac_s_single"], row["engine_gmac_s_multi"] = pair(macs / engine_us / 1e3, 3)
        row["nj_per_mac_single"], row["nj_per_mac_multi"] = pair(float(s["nj_per_mac_wall"]), 4)
        row.update(
            kind=c["kind"], configuration=c["configuration"], engine_shape=c["engine_shape"],
            tenants=int(c["tenants"]), hbm_channels=int(c["hbm_channels"]),
            lanes_total=int(c["lanes_total"]), clock_mhz=clk, matrices=int(s["matrices"]),
            useful_macs=macs, padded_macs=int(s["padded_macs"]),
            padding_pct=float(s["padding_pct"]), calcs_per_tenant=s["calcs_per_tenant"],
            efficiency_pct=float(c["pct_of_peak"]),
            predicted_us=round(plan["predicted_cycles"] / clk, 1),
            engine_us=round(engine_us, 1), active_sum_us=active_us, wall_pass_us=wall_us,
            overhead_us=round(wall_us - engine_us, 1),
            overhead_pct=round(100.0 * (wall_us - engine_us) / wall_us, 2),
            wall_gmac_s=round(macs / wall_us / 1e3, 3),
            engine_gmac_s=round(macs / engine_us / 1e3, 3),
            board_load_W=load_w, board_idle_W=idle_w, dynamic_W=round(load_w - idle_w, 3),
            energy_pass_mJ=round(static_mj + dynamic_mj, 2), static_mJ=round(static_mj, 2),
            dynamic_mJ=round(dynamic_mj, 2), nj_per_mac_wall=float(s["nj_per_mac_wall"]),
            fullrate_load_W=full_w,
            nj_per_mac_launch_free=round(full_w * engine_us / macs * 1e3, 4),
            finish_balance=float(s["finish_balance"]),
            balance_planned=float(s["balance_planned"]),
            timed_passes=npass, soak_passes=int(s["soak_passes"]), soak_s=float(s["soak_s"]),
            source=folder + "/" + prefix + "%s_%dMHz.csv" % (arch, clk),
            estimator="wall time, energy: measured (60 s soak of whole passes, board power); "
                      "engine time: plan cycles / clock / measured steady-state efficiency "
                      "(Configurations pct_of_peak); overhead = wall - engine"
                      + ("" if vectors == "shared" else
                         "; every matrix its own calculation and input vector"))
        out.append(row)
        print("%-17s %9.1f %9.1f %9.1f %7.2f %8.3f %8.3f %7.4f  %.2f / %.2f" % (
            c["config_label"], engine_us, active_us, wall_us, row["overhead_pct"],
            row["engine_gmac_s"], row["wall_gmac_s"], row["nj_per_mac_wall"], idle_w,
            float(c["board_idle_W"])))

    with io.open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()), lineterminator="\n")
        w.writeheader()
        w.writerows(out)
    print("wrote %s (%d builds, %d columns)" % (os.path.relpath(out_path, ROOT), len(out),
                                                len(out[0])))


def build_moe(folder, prefix, runs, configs, out_path):
    """MoE: one row per build -- the 13 and every shared-vector build with a recorded clock --
    sorted by HBM channels. One TOKEN = one pass = 7 layers (one per width), 132 MIXED
    experts in all, each layer's experts split over the engines and stacked per engine.
    Chart columns in _single / _multi / _shared triples (one filled per row). Then the twin
    table: every shared-vector build beside its own-vector twin."""
    by_key = dict(((a[1], a[2]), a[0]) for a in pw.ARCHS)
    cfg, order = {}, {}
    for i, c in enumerate(configs):
        arch = by_key.get((c["engine_shape"], int(c["tenants"])))
        if arch is None:
            fail("no workload architecture for Configurations row %s" % c["config_label"])
        cfg[arch], order[arch] = c, i
    kinds = ("single", "multi", "shared")
    rows, got = [], {}
    print("%-26s %9s %9s %9s %7s %8s %7s %7s" % ("build (MoE)", "engine us", "active us",
          "token us", "ovh %", "GMAC/s", "nJ/MAC", "idle W"))
    for i, (name, shape, n, clock, _fold, _x) in enumerate(pw.archs_for("moe")):
        s, calcs = runs[name]
        plan = pw.plan(name, "moe")
        npass = check_run(name, s, calcs, plan)
        clk = int(s["clock_mhz"])
        shared = pw.is_shared(name)
        src = EFFICIENCY_FROM[name] if shared else name
        if not shared and clk != int(float(cfg[name]["clock_mhz"])):
            fail("%s: clock %d here, %s in Configurations" % (name, clk, cfg[name]["clock_mhz"]))
        eff = float(cfg[src]["pct_of_peak"]) / 100.0
        w, ind, a, cc = pw.channels_of(shape)
        channels = n * (w + ind + cc) + (a if shared else n * a)
        kind = "single" if n == 1 else ("shared" if shared else "multi")
        label = (shape if n == 1 else "%d x %s%s" % (n, shape, " shared" if shared else "")) \
            + " (%d ch)" % channels
        engine_us = plan["predicted_cycles"] / clk / eff       # sum over layers of the slowest
        active_us = float(s["layer_active_sum_us"])             # the same, measured
        if active_us < engine_us:
            fail("%s: active time %.1f us < engine time %.1f us -- the efficiency does not fit "
                 "this run" % (name, active_us, engine_us))
        wall_us = float(s["wall_pass_us"])
        if wall_us < engine_us:
            fail("%s: a token took %.1f us in the soak, less than the %.1f us the engines need "
                 "-- impossible" % (name, wall_us, engine_us))
        load_w, idle_w = float(s["board_load_W"]), float(s["board_idle_W"])
        macs = int(s["useful_macs"])
        static_mj, dynamic_mj = idle_w * wall_us / 1e3, (load_w - idle_w) * wall_us / 1e3

        def trio(v, nd):
            return [round(v, nd) if k == kind else "" for k in kinds]

        row = dict(config_label=label)
        for k, e, o in zip(kinds, trio(engine_us / 1e3, 3), trio((wall_us - engine_us) / 1e3, 3)):
            row["engine_ms_" + k], row["overhead_ms_" + k] = e, o
        for k, st, dy in zip(kinds, trio(static_mj, 3), trio(dynamic_mj, 3)):
            row["static_mJ_" + k], row["dynamic_mJ_" + k] = st, dy
        for col, v, nd in (("gmac_s", macs / wall_us / 1e3, 3),
                           ("nj_per_mac", float(s["nj_per_mac_wall"]), 4),
                           ("idle_W", idle_w, 3)):
            for k, x in zip(kinds, trio(v, nd)):
                row["%s_%s" % (col, k)] = x
        row.update(
            kind={"single": "single engine", "multi": "multi-tenant, own vector channels",
                  "shared": "multi-tenant, shared vector channels"}[kind],
            engine_shape=shape, engines=n, layers=plan["layers"],
            experts_per_token=plan["experts_per_token"],
            experts_per_layer="/".join(str(x) for x in plan["experts_per_layer"]),
            hbm_channels=channels, clock_mhz=clk, lanes_total=plan["lanes"] * n,
            useful_macs_per_token=macs, padding_pct=float(s["padding_pct"]),
            efficiency_pct=float(cfg[src]["pct_of_peak"]),
            efficiency_from=cfg[src]["config_label"],
            predicted_us=round(plan["predicted_cycles"] / clk, 1),
            engine_us=round(engine_us, 1), layer_active_sum_us=active_us, token_us=wall_us,
            layer_us=round(wall_us / plan["layers"], 1),
            overhead_us=round(wall_us - engine_us, 1),
            overhead_pct=round(100.0 * (wall_us - engine_us) / wall_us, 2),
            gmac_s=round(macs / wall_us / 1e3, 3),
            tokens_per_s=round(1e6 / wall_us, 1),
            board_load_W=load_w, board_idle_W=idle_w, dynamic_W=round(load_w - idle_w, 3),
            energy_token_mJ=round(static_mj + dynamic_mj, 3), static_mJ=round(static_mj, 3),
            dynamic_mJ=round(dynamic_mj, 3), nj_per_mac=float(s["nj_per_mac_wall"]),
            finish_balance=float(s["finish_balance"]),
            timed_passes=npass, soak_passes=int(s["soak_passes"]), soak_s=float(s["soak_s"]),
            source=folder + "/" + prefix + "%s_%dMHz.csv" % (name, clk),
            estimator="per token = one pass of 7 layers (one per width), 132 MIXED experts, "
                      "each layer's experts split over the engines and stacked per engine, "
                      "engines in lockstep; time, energy, idle: measured (60 s soak, board "
                      "power); engine time: sum over layers of the busiest engine's plan "
                      "cycles / clock / steady-state efficiency of efficiency_from; overhead = "
                      "token time - engine time")
        rows.append(((channels, order.get(name, 100 + i)), row))
        got[name] = row
        print("%-26s %9.1f %9.1f %9.1f %7.2f %8.3f %7.4f %7.2f" % (
            label, engine_us, active_us, wall_us, row["overhead_pct"], row["gmac_s"],
            row["nj_per_mac"], idle_w))
    rows = [r for _, r in sorted(rows, key=lambda x: x[0])]
    with io.open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print("wrote %s (%d builds, %d columns)" % (os.path.relpath(out_path, ROOT), len(rows),
                                                len(rows[0])))

    # ---- the energy saved by the unused channels: every shared build beside its twin ------
    twins = []
    for sh, own in sorted(TWIN_OF.items()):
        if sh not in got or own not in got:
            continue
        a, b = got[own], got[sh]
        twins.append(dict(
            pair="%s: %d -> %d ch" % (a["config_label"].split(" (")[0], a["hbm_channels"],
                                     b["hbm_channels"]),
            channels_own=a["hbm_channels"], channels_shared=b["hbm_channels"],
            channels_freed=a["hbm_channels"] - b["hbm_channels"],
            clock_own=a["clock_mhz"], clock_shared=b["clock_mhz"],
            idle_W_own=a["board_idle_W"], idle_W_shared=b["board_idle_W"],
            idle_drop_W=round(a["board_idle_W"] - b["board_idle_W"], 3),
            idle_drop_pct=round(100.0 * (a["board_idle_W"] - b["board_idle_W"])
                                / a["board_idle_W"], 2),
            load_W_own=a["board_load_W"], load_W_shared=b["board_load_W"],
            token_us_own=a["token_us"], token_us_shared=b["token_us"],
            energy_token_mJ_own=a["energy_token_mJ"], energy_token_mJ_shared=b["energy_token_mJ"],
            energy_change_pct=round(100.0 * (b["energy_token_mJ"] - a["energy_token_mJ"])
                                    / a["energy_token_mJ"], 2),
            nj_per_mac_own=a["nj_per_mac"], nj_per_mac_shared=b["nj_per_mac"]))
    if twins:
        with io.open(TWINS_OUT, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(twins[0].keys()), lineterminator="\n")
            w.writeheader()
            w.writerows(twins)
        print("wrote %s (%d twin pairs)" % (os.path.relpath(TWINS_OUT, ROOT), len(twins)))
        for t in twins:
            print("  %-28s idle %.2f -> %.2f W (%+.2f%%)   energy per token %+.2f%%"
                  % (t["pair"], t["idle_W_own"], t["idle_W_shared"], -t["idle_drop_pct"],
                     t["energy_change_pct"]))
    else:
        print("no shared build with its twin measured yet -- %s not written"
              % os.path.relpath(TWINS_OUT, ROOT))


if __name__ == "__main__":
    main()

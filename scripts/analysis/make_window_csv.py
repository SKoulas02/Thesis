"""Build the chart tables of the measurement windows: the professor's two use cases on the
native XRT host with host-side scheduling (ert=false), measured on a quiet server.

    python scripts/analysis/make_window_csv.py                  # every window; runs AVERAGED
    python scripts/analysis/make_window_csv.py --runs latest    # the latest run of each build
    python scripts/analysis/make_window_csv.py --window results/window_20261002_1644

Inputs : results/window_<stamp>/                     workload/run_window.py, one run each:
           window_summary.csv, settings.txt, window.log    status, settings, per-build times
           qwen/workload_xrt_qwen_<arch>_<MHz>MHz.csv (+ _calcs.csv)      UC2, one per build
           uc1/workload_xrt_permatrix_<arch>_<MHz>MHz.csv (+ _calcs.csv)  UC1, the 13 builds
         results/window_load_<YYYYMMDD>.txt            that day's 30 s load log (optional)
         results/window_shapes_<stamp>/shapes/shapes_xrt_<arch>_<MHz>MHz_MIXED.csv
                                                       the MIXED shape sweep (run_window_shapes.py):
                                                       the measured efficiency of every build
                                                       without a Configurations row
         results/qwen_accuracy/qwen_accuracy_<arch>_<MHz>MHz_wsigma1[_rows].csv
                                                       the gates' realistic layer (N(0,1) data)
         results/GEMV_Configurations.csv                steady-state efficiency of the 13 builds
         workload/plan_workload.py, plan_qwen.py        the plans (imported, and checked)
Output : results/GEMV_UC1_XRT.csv        UC1, multi-user per-matrix: the SAME builder, checks and
                                         columns as GEMV_Workload_PerMatrix.csv (the OpenCL run of
                                         2026-09-28, make_workload_csv.py), so the two compare
                                         column for column; the window's columns are APPENDED
         results/GEMV_Qwen_XRT.csv       UC2, Qwen3.5-35B-A3B step 1: one row per build, sorted by
                                         HBM channels like Configurations; chart columns in
                                         _single/_multi/_shared/_bcast quads (one filled per row);
                                         a build not measured yet keeps a blank pending row
         results/GEMV_Qwen_Accuracy.csv  the realistic layer's error by sparsity, each distinct
                                         data set pooled once (twins -- own vector, shared,
                                         broadcast -- were gated on the same data and returned
                                         the same bits; acc_same_data_as in the Qwen table)
Columns are only ever APPENDED: GEMV_Charts_XRT.xlsx holds charts that address these tables'
columns by letter.

THE ESTIMATOR is the workload tables' (make_workload_csv.py): wall time per pass / token and
energy are MEASURED (60 s soak, board power; static = idle power x time); engine time is
DERIVED (the plan's cycles / clock / the measured steady-state efficiency); overhead = wall -
engine. The three timed passes are reported beside the soak (timed_pass_us); the first pass
after a load runs a few % slow, which is why the soak is the measurement.

RUNS (2026-10-02). Every build was measured twice on a quiet server: 1 Oct (alone on the server
after a reboot, window_20261001_1438) and 2 Oct (the professor's isolated window,
window_20261002_1644; 2x8x8_bcast twice that day, window_20261002_1609 and _1644). Every run is
checked on its own; the table reports the MEAN of the runs' measured primaries -- time per pass /
token, board load and idle power, active and timed times -- and computes energy, throughput,
nJ/MAC and the engine / overhead split from those means (so energy = load x time still holds
exactly); each quantity's min, max and half-range over the runs are APPENDED (runs_* columns, the
error bars). --runs latest reports the latest run alone. WHY: times repeat within ~1.5 %, but the
Qwen test's dynamic power (random data, a new seed every run) moved by up to 4.6 W between runs,
so a single run is a single sample of it.

EFFICIENCY (engine time = plan cycles / clock / efficiency). A build with a Configurations row
takes its pct_of_peak (measured in the earlier steady-state campaigns). A build without one --
the shared and broadcast builds -- takes ITS OWN measured MIXED occupancy from the shape sweep:
the MINIMUM over the 11 shapes, because that two-size slope can only read high (staggered
lockstep starts), never low. Until 2026-10-02 these builds borrowed their twin's value, which hid
that 3x4x4_shared and 3x8x4_shared stream weights at only 225 M beats/s (0.58 and 0.71 of their
clocks; every other build ~0.99). Where both values exist (the 7 multi-engine builds with a
Configurations row) they are printed side by side.

ACCURACY: |card - exact| / rms(exact) over the gate's realistic layer, in % -- median, p95 and
max over its rows (the gate's own order statistics) -- and the mean signed offset (card - exact)
/ rms, the bias. The card matches "each summand toward zero to 2^-7, fixed-point sum, bf16
truncation" on 100 % of rows; that is checked, so these numbers describe the arithmetic.

SERVER LOAD per build (appended columns): the 30 s log's 1-minute load average over the build's
interval in window.log (mean and max) and the most Vivado / v++ / HLS processes seen, over all its
runs. The run's own processes hold the load near 3; server_busy_max > 0 means the build shared
the server.

CHECKS (nothing is written if any fails): each window's settings say ert=false, weight sigma 1.0
and a 60 s soak; every listed build measured OK; every run against its plan -- clock, MACs,
predicted time, the beats and laps of every timed calculation, energy = load power x wall time;
engine time <= active time and <= wall time; a shape-sweep efficiency exists, at the build's
clock, and is <= 1; one accuracy file per measured Qwen build at its clock and sigma 1.0 with a
100 % model match. A tool whose md5 in settings.txt differs from the local copy is reported, not
fatal (the plan checks catch any change that matters). Window times are seconds of the day: no
window crosses midnight.
"""

import argparse
import csv
import glob
import hashlib
import io
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # repository root; this file is in scripts/analysis/
RESULTS = os.path.join(ROOT, "results")
WORKLOAD = os.path.join(ROOT, "workload")
sys.path.insert(0, HERE)
sys.path.insert(0, WORKLOAD)
import make_workload_csv as mw                  # noqa: E402  the per-matrix table, reused as is
import plan_workload as pw                      # noqa: E402
import plan_qwen as pq                          # noqa: E402

CONFIGS = os.path.join(RESULTS, "GEMV_Configurations.csv")
ACCURACY = os.path.join(RESULTS, "qwen_accuracy")
UC1_OUT = os.path.join(RESULTS, "GEMV_UC1_XRT.csv")
QWEN_OUT = os.path.join(RESULTS, "GEMV_Qwen_XRT.csv")
ACC_OUT = os.path.join(RESULTS, "GEMV_Qwen_Accuracy.csv")
UC1_PREFIX, QWEN_PREFIX = "workload_xrt_permatrix_", "workload_xrt_qwen_"
HOST = "native XRT (host_workload_xrt.cpp), ert=false: host-side scheduling"
KINDS = ("single", "multi", "shared", "bcast")
KIND_OF = {"single": "single", "multi": "multi", "shared": "shared", "broadcast": "bcast"}
KIND_TEXT = {"single": "single engine", "multi": "multi-tenant, own vector channels",
             "shared": "multi-tenant, shared vector channels",
             "bcast": "multi-tenant, broadcast vector (one mover pair)"}
SUFFIX = {"shared": " shared", "bcast": " bcast"}
SPARSITY_ORDER = ["2:4", "2:8", "2:16", "2:32"]
# the measured primaries a table row is built from: the mean over the runs replaces each
MEAN_FIELDS = ("wall_pass_us", "board_load_W", "board_idle_W", "vccint_load_W", "vccint_idle_W",
               "active_sum_us", "layer_active_sum_us", "makespan_us", "host_wall_us",
               "finish_balance", "host_load_1min")


def fail(msg):
    raise SystemExit("make_window_csv: " + msg + " -- nothing written")


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


def md5(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def seconds(hms):
    h, m, s = (int(x) for x in hms.split(":"))
    return 3600 * h + 60 * m + s


class Window(object):
    """One results/window_<stamp>/: its settings, its OK builds, their intervals, its load log."""

    def __init__(self, path):
        self.path, self.name = path, os.path.basename(path)
        settings = io.open(os.path.join(path, "settings.txt"), encoding="utf-8").read()
        for need in ("ert=false", "--weight-sigma 1.0", "soak 60 s"):
            if need not in settings:
                fail("%s/settings.txt does not say %r" % (self.name, need))
        m = re.search(r"1-minute load at the start: ([\d.]+); busy processes: (.*)", settings)
        self.start_load, self.start_busy = (m.group(1), m.group(2).strip()) if m else ("?", "?")
        self.md5s = re.findall(r"^  ([0-9a-f]{32})  (\S+)$", settings, re.M)
        self.ok = {}
        for r in read_csv(os.path.join(path, "window_summary.csv")):
            if r["status"] == "OK":
                self.ok[(r["part"], r["arch"])] = r
            else:
                print("  %s: %s %s was %s (%s) -- not used" % (self.name, r["part"], r["arch"],
                                                               r["status"], r["note"]))
        self.span, start = {}, {}
        pat = re.compile(r"^(\d\d:\d\d:\d\d) \[\d+/\d+\] (qwen|uc1) (\S+?)(:| \.\.\.)")
        for ln in io.open(os.path.join(path, "window.log"), encoding="utf-8"):
            m = pat.match(ln)
            if not m:
                continue
            key, t = (m.group(2), m.group(3)), seconds(m.group(1))
            if m.group(4) == " ...":
                start[key] = t
            elif key in start:
                self.span[key] = (start[key], t)
        self.load, self.load_log = [], ""
        m = re.match(r"window_(\d{8})_", self.name)
        log = os.path.join(RESULTS, "window_load_%s.txt" % m.group(1)) if m else ""
        if log and os.path.exists(log):
            pat = re.compile(r"^(\d\d:\d\d:\d\d)\s+load ([\d.]+) \S+ \S+\s+logins \d+\s+busy (\d+)")
            for ln in io.open(log, encoding="utf-8"):
                m = pat.match(ln)
                if m:
                    self.load.append((seconds(m.group(1)), float(m.group(2)), int(m.group(3))))
            self.load_log = os.path.basename(log)

    def server(self, key):
        """(mean load, max load, max busy processes) over one build's interval, or Nones."""
        if key not in self.span or not self.load:
            return None, None, None
        a, b = self.span[key]
        got = [(load, busy) for t, load, busy in self.load if a <= t <= b]
        if not got:
            return None, None, None
        return (statistics.mean(x for x, _ in got), max(x for x, _ in got),
                max(n for _, n in got))


def load_run(w, part, prefix, arch, clock, vectors=None):
    path = os.path.join(w.path, part, "%s%s_%dMHz.csv" % (prefix, arch, clock))
    if not os.path.exists(path):
        fail("%s lists %s %s OK but has no %s" % (w.name, part, arch, os.path.basename(path)))
    rows = read_csv(path)
    if len(rows) != 1:
        fail("%s has %d rows, expected 1" % (path, len(rows)))
    s = rows[0]
    if s["arch"] != arch or int(s["clock_mhz"]) != clock:
        fail("%s: file name and contents disagree" % path)
    if vectors and s.get("vectors") != vectors:
        fail("%s was run with vectors %s, this table is %s" % (path, s.get("vectors"), vectors))
    calcs = path[:-4] + "_calcs.csv"
    if not os.path.exists(calcs):
        fail("no %s" % calcs)
    return path, s, read_csv(calcs)


def timed_passes(calcs):
    """Each timed pass's span, first start to last end (every pass restarts its clock)."""
    out = []
    for p in sorted(set(int(r["pass_"]) for r in calcs)):
        rs = [r for r in calcs if int(r["pass_"]) == p]
        out.append(max(float(r["end_us"]) for r in rs) - min(float(r["start_us"]) for r in rs))
    return out


def combine(runs):
    """runs: [(window, path, s, calcs)], each already checked against its plan -> the row the
    table is built from: the latest run's, with every measured primary replaced by its mean over
    the runs and every energy field recomputed from those means (energy = load x time exactly)."""
    s = dict(runs[-1][2])
    for f in MEAN_FIELDS:
        vals = [r[2].get(f, "") for r in runs]
        if all(v not in ("", None) for v in vals):
            s[f] = "%.6f" % statistics.mean(float(v) for v in vals)
    wall, load, idle = float(s["wall_pass_us"]), float(s["board_load_W"]), float(s["board_idle_W"])
    macs = int(s["useful_macs"])
    s["energy_wall_uJ"] = "%.6f" % (load * wall)
    s["energy_wall_static_uJ"] = "%.6f" % (idle * wall)
    s["energy_dynamic_uJ"] = "%.6f" % ((load - idle) * wall)
    s["nj_per_mac_wall"] = "%.4f" % (load * wall / macs * 1e3)
    s["wall_gmac_s"] = "%.3f" % (macs / wall / 1e3)
    s["soak_passes"] = str(sum(int(r[2]["soak_passes"]) for r in runs))
    s["soak_s"] = "%.2f" % sum(float(r[2]["soak_s"]) for r in runs)
    return s


def spread(prefix, vals, nd):
    """runs_<prefix>_min / _max / _halfrange over the runs."""
    return {"runs_%s_min" % prefix: round(min(vals), nd),
            "runs_%s_max" % prefix: round(max(vals), nd),
            "runs_%s_halfrange" % prefix: round((max(vals) - min(vals)) / 2.0, nd)}


def run_spreads(runs, unit_prefix, energy_prefix):
    """The appended error-bar columns: per run time (ms), rate, energy, nJ/MAC, dynamic and idle
    power -- min, max and half-range over the runs."""
    t = [float(r[2]["wall_pass_us"]) for r in runs]
    ld = [float(r[2]["board_load_W"]) for r in runs]
    idl = [float(r[2]["board_idle_W"]) for r in runs]
    macs = int(runs[-1][2]["useful_macs"])
    out = {"runs": len(runs)}
    out.update(spread(unit_prefix, [x / 1e3 for x in t], 3))
    if unit_prefix == "token_ms":
        out.update(spread("tokens_s", [1e6 / x for x in t], 2))
    else:
        out.update(spread("gmac_s", [macs / x / 1e3 for x in t], 3))
    out.update(spread(energy_prefix, [l * x / 1e3 for l, x in zip(ld, t)], 3))
    out.update(spread("nj_per_mac", [l * x / macs * 1e3 for l, x in zip(ld, t)], 4))
    out.update(dict(("runs_dynamic_W_" + k, round(f(l - i for l, i in zip(ld, idl)), 3))
                    for k, f in (("min", min), ("max", max))))
    out.update(dict(("runs_idle_W_" + k, round(f(idl), 3)) for k, f in (("min", min), ("max", max))))
    return out


def window_cols(runs, key):
    """The window columns, over all runs: timed passes (means), the windows, host and server
    load (mean of the means, max of the maxima)."""
    spans = [timed_passes(r[3]) for r in runs]
    srv = [r[0].server(key) for r in runs]
    means = [x[0] for x in srv if x[0] is not None]
    maxes = [x[1] for x in srv if x[1] is not None]
    busy = [x[2] for x in srv if x[2] is not None]
    return dict(timed_pass_us=round(statistics.mean(statistics.mean(s) for s in spans), 1),
                timed_first_pass_us=round(statistics.mean(s[0] for s in spans), 1),
                timed_spread_pct=round(max(100.0 * (max(s) - min(s)) / min(s) for s in spans), 2),
                host=HOST, window=" + ".join(r[0].name for r in runs),
                host_load_1min=round(statistics.mean(float(r[2]["host_load_1min"])
                                                     for r in runs), 2),
                server_load_mean=round(statistics.mean(means), 2) if means else "",
                server_load_max=max(maxes) if maxes else "",
                server_busy_max=max(busy) if busy else "")


def sources(runs):
    return " + ".join(os.path.relpath(r[1], RESULTS).replace(os.sep, "/") for r in runs)


def shape_efficiencies():
    """{arch: (efficiency, clock, where)}: each multi-engine build's measured MIXED occupancy from
    its latest shape sweep, the minimum over the 11 shapes."""
    out = {}
    for d in sorted(glob.glob(os.path.join(RESULTS, "window_shapes_*"))):
        if not os.path.isfile(os.path.join(d, "window_summary.csv")):
            continue
        for r in read_csv(os.path.join(d, "window_summary.csv")):
            if r["part"] != "shapes" or r["status"] != "OK":
                continue
            f = os.path.join(d, "shapes", "shapes_xrt_%s_%sMHz_MIXED.csv" % (r["arch"],
                                                                            r["clock_mhz"]))
            if not os.path.exists(f):
                fail("%s lists %s OK but has no %s" % (os.path.basename(d), r["arch"],
                                                       os.path.basename(f)))
            rows = read_csv(f)
            if len(rows) != 11 or any(x["config"] != r["arch"] for x in rows):
                fail("%s: not the 11 shapes of %s" % (f, r["arch"]))
            out[r["arch"]] = (min(float(x["occupancy"]) for x in rows), int(r["clock_mhz"]),
                              os.path.basename(d))
    return out


def build_uc1(runs_of, configs):
    """The OpenCL per-matrix table's builder and checks (make_workload_csv.build), fed the mean
    of each build's runs; every run is checked on its own first."""
    runs, keep = {}, {}
    for name, shape, n, clock, _f, _x in pw.archs_for("per-matrix"):
        ws = runs_of.get(("uc1", name))
        if not ws:
            fail("UC1 %s: no window measured it" % name)
        plan = pw.plan(name, "per-matrix")
        rr = []
        for w in ws:
            path, s, calcs = load_run(w, "uc1", UC1_PREFIX, name, clock, "per-matrix")
            mw.check_run(name, s, calcs, plan)
            rr.append((w, path, s, calcs))
        runs[name], keep[name] = (combine(rr), rr[-1][3]), rr
    stray = sorted(k for p, k in runs_of if p == "uc1" and k not in runs)
    if stray:
        fail("UC1 builds the plan does not list: %s" % " ".join(stray))
    print("\n== UC1 (multi-user, per-matrix), the OpenCL table's builder and checks; the mean of "
          "each build's runs")
    tmp = UC1_OUT + ".tmp"
    mw.build("per-matrix", "-", UC1_PREFIX, runs, configs, tmp)
    rows = read_csv(tmp)
    os.remove(tmp)
    print("%-17s %4s %10s %8s %8s %9s %8s %9s %6s" % (
        "build", "runs", "pass us", "+-time %", "mJ/pass", "+-energy%", "dyn W", "server ld",
        "busy"))
    for r in rows:
        arch = re.match(r"-/" + re.escape(UC1_PREFIX) + r"(.+)_\d+MHz\.csv$", r["source"]).group(1)
        rr = keep[arch]
        r["source"] = sources(rr)
        r["estimator"] += ("; host: " + HOST + "; the mean of %d run(s) (runs_* = min / max / "
                           "half-range over them)" % len(rr))
        r.update(window_cols(rr, ("uc1", arch)))
        r.update(run_spreads(rr, "pass_ms", "energy_pass_mJ"))
        print("%-17s %4d %10.1f %8.2f %8.1f %9.2f %4.2f-%4.2f %9s %6s" % (
            r["config_label"], r["runs"], float(r["wall_pass_us"]),
            100.0 * r["runs_pass_ms_halfrange"] * 1e3 / float(r["wall_pass_us"]),
            float(r["energy_pass_mJ"]),
            100.0 * r["runs_energy_pass_mJ_halfrange"] / float(r["energy_pass_mJ"]),
            r["runs_dynamic_W_min"], r["runs_dynamic_W_max"],
            "%s/%s" % (r["server_load_mean"], r["server_load_max"]), r["server_busy_max"]))
    return rows


def check_qwen(name, s, calcs, plan):
    clk = int(s["clock_mhz"])
    if clk != plan["clock_mhz"]:
        fail("%s ran at %d MHz, the plan says %d" % (name, clk, plan["clock_mhz"]))
    for k in ("layers", "layer_rows", "width", "useful_macs"):
        if int(s[k]) != plan[k]:
            fail("%s: %s %s, the plan says %d -- the plan changed" % (name, k, s[k], plan[k]))
    if float(s["weight_sigma"]) != 1.0:
        fail("%s ran with weight sigma %s, not 1.0" % (name, s["weight_sigma"]))
    if abs(float(s["predicted_us"]) - plan["predicted_cycles"] / clk) > 0.06:
        fail("%s: predicted time differs from the plan" % name)
    npass = len(set(r["pass_"] for r in calcs))
    ncalc = sum(len(t["calcs"]) for t in plan["tenant_plans"])
    if len(calcs) != npass * ncalc:
        fail("%s: %d calculation rows, expected %d passes x %d" % (name, len(calcs), npass, ncalc))
    for r in calcs:
        c = plan["tenant_plans"][int(r["tenant"])]["calcs"][int(r["calc"])]
        if int(r["beats"]) != c["beats"] or int(r["laps"]) != c["laps"]:
            fail("%s engine %s layer %s: %s beats / %s laps, the plan says %d / %d"
                 % (name, r["tenant"], r["calc"], r["beats"], r["laps"], c["beats"], c["laps"]))
    e = float(s["board_load_W"]) * float(s["wall_pass_us"])
    if abs(e - float(s["energy_wall_uJ"])) > 1e-3 * e:
        fail("%s: energy per token is not load power x wall time" % name)
    return npass


def dataset(name, clk):
    """A fingerprint of the realistic layer a build was gated on: twins (same shape, same
    engine count -- own vector, shared, broadcast) get the same data, and the card returns the
    same bits on every one of them."""
    path = os.path.join(ACCURACY, "qwen_accuracy_%s_%dMHz_wsigma1_rows.csv" % (name, clk))
    if not os.path.exists(path):
        fail("no %s -- copy the gates' accuracy files" % os.path.relpath(path, ROOT))
    rows = read_csv(path)
    key = "|".join("%s,%s,%s" % (r["sparsity"], r["exact"], r["card_hex"]) for r in rows)
    return hashlib.md5(key.encode("utf-8")).hexdigest(), rows


def accuracy(name, clk):
    path = os.path.join(ACCURACY, "qwen_accuracy_%s_%dMHz_wsigma1.csv" % (name, clk))
    if not os.path.exists(path) or not os.path.exists(path[:-4] + "_rows.csv"):
        fail("no %s (+ _rows) -- copy the gates' accuracy files" % os.path.relpath(path, ROOT))
    rows = read_csv(path)
    if len(rows) != 1:
        fail("%s has %d rows, expected 1" % (path, len(rows)))
    r = rows[0]
    if r["arch"] != name or int(r["clock_mhz"]) != clk or float(r["weight_sigma"]) != 1.0:
        fail("%s: arch / clock / sigma disagree with the build" % path)
    if float(r["match_fix7_toward_0_out_trunc"]) != 100.0:
        fail("%s: the card matches its arithmetic model on %s %% of rows, not 100"
             % (path, r["match_fix7_toward_0_out_trunc"]))
    return dict(acc_rows=int(r["rows"]),
                acc_median_pct=round(100 * float(r["median_abs_offset_rel_rms"]), 3),
                acc_p95_pct=round(100 * float(r["p95_abs_offset_rel_rms"]), 3),
                acc_max_pct=round(100 * float(r["max_abs_offset_rel_rms"]), 3),
                acc_mean_offset_pct=round(100 * float(r["mean_offset_rel_rms"]), 4),
                acc_model_match_pct=float(r["match_fix7_toward_0_out_trunc"]))


def build_qwen(runs_of, configs):
    by_key = dict(((a[1], a[2]), a[0]) for a in pw.ARCHS)
    cfg, order = {}, {}
    for i, c in enumerate(configs):
        arch = by_key.get((c["engine_shape"], int(c["tenants"])))
        if arch is None:
            fail("no architecture for Configurations row %s" % c["config_label"])
        cfg[arch], order[arch] = c, i
    known = set(a[0] for a in pq.BUILDS)
    stray = sorted(k for p, k in runs_of if p == "qwen" and k not in known)
    if stray:
        fail("Qwen builds plan_qwen does not list: %s" % " ".join(stray))
    sweep = shape_efficiencies()
    print("\n== efficiency: Configurations (13 builds) vs the MIXED shape sweep (min of 11 shapes)")
    for name, (eff, sclk, d) in sorted(sweep.items()):
        if name in cfg:
            print("   %-15s Configurations %6.2f %%   shape sweep %6.2f %%   (%+.2f points; the "
                  "table uses Configurations)" % (name, float(cfg[name]["pct_of_peak"]), 100 * eff,
                                                  100 * eff - float(cfg[name]["pct_of_peak"])))
    first_with = {}                                 # data fingerprint -> first build gated on it
    for name, _s, _n, clock, _f, _x in pq.BUILDS:
        if clock is not None and runs_of.get(("qwen", name)):
            first_with.setdefault(dataset(name, clock)[0], name)
    print("\n== UC2 Qwen step 1 (per token = 40 layers of 9216 x 2048 MIXED); the mean of each "
          "build's runs")
    print("%-24s %4s %4s %6s %9s %9s %9s %6s %7s %8s %8s %6s %6s %13s %5s %22s" % (
        "build", "runs", "MHz", "eff %", "engine us", "active", "token us", "+-t %", "ovh %",
        "tok/s", "mJ/tok", "+-E %", "dyn W", "server ld", "busy", "acc med/p95/max/bias %"))
    rows, measured = [], []
    for i, (name, shape, n, clock, _f, _x) in enumerate(pq.BUILDS):
        k = KIND_OF[pq.kind(name)]
        w_pcs, ind_pcs, a_pcs, c_pcs = pw.channels_of(shape)
        channels = n * (w_pcs + ind_pcs + c_pcs) + (a_pcs if k in ("shared", "bcast") else n * a_pcs)
        label = ((shape if n == 1 else "%d x %s%s" % (n, shape, SUFFIX.get(k, "")))
                 + " (%d ch)" % channels)
        key = (channels, order.get(name, 100 + i))
        ws = runs_of.get(("qwen", name))
        if not ws or clock is None:
            if ws:
                fail("%s was measured but has no clock in the plan" % name)
            rows.append((key, dict(config_label=label, status="pending: not measured yet",
                                   kind=KIND_TEXT[k], engine_shape=shape, engines=n,
                                   hbm_channels=channels)))
            print("%-24s pending: not measured yet" % label)
            continue
        plan = pq.plan(name)
        rr = []
        for w in ws:
            path, s1, calcs1 = load_run(w, "qwen", QWEN_PREFIX, name, clock)
            check_qwen(name, s1, calcs1, plan)
            rr.append((w, path, s1, calcs1))
        s, calcs = combine(rr), rr[-1][3]
        npass = check_qwen(name, s, calcs, plan)
        clk = int(s["clock_mhz"])
        if name in cfg:
            if clk != int(float(cfg[name]["clock_mhz"])):
                fail("%s: clock %d here, %s in Configurations" % (name, clk,
                                                                   cfg[name]["clock_mhz"]))
            eff = float(cfg[name]["pct_of_peak"]) / 100.0
            eff_from = cfg[name]["config_label"]
        else:
            if name not in sweep:
                fail("%s has no Configurations row and no shape sweep: its efficiency is not "
                     "measured" % name)
            eff, sclk, d = sweep[name]
            if sclk != clk:
                fail("%s: shape sweep at %d MHz, Qwen at %d" % (name, sclk, clk))
            if not 0.0 < eff <= 1.0:
                fail("%s: shape-sweep efficiency %.4f" % (name, eff))
            eff_from = "own shape sweep (min of 11 MIXED shapes, %s)" % d
        engine_us = plan["predicted_cycles"] / clk / eff       # sum over layers of the busiest
        active_us = float(s["layer_active_sum_us"])             # the same, measured
        wall_us = float(s["wall_pass_us"])
        for w, _p, s1, _c in rr:
            if float(s1["layer_active_sum_us"]) < engine_us:
                fail("%s (%s): active time %.1f us < engine time %.1f us -- the efficiency does "
                     "not fit this run" % (name, w.name, float(s1["layer_active_sum_us"]),
                                           engine_us))
            if float(s1["wall_pass_us"]) < engine_us:
                fail("%s (%s): a token took %.1f us in the soak, less than the %.1f us the "
                     "engines need -- impossible" % (name, w.name, float(s1["wall_pass_us"]),
                                                     engine_us))
        load_w, idle_w = float(s["board_load_W"]), float(s["board_idle_W"])
        macs = int(s["useful_macs"])
        static_mj, dynamic_mj = idle_w * wall_us / 1e3, (load_w - idle_w) * wall_us / 1e3
        acc = accuracy(name, clk)

        def quad(v, nd):
            return [round(v, nd) if kk == k else "" for kk in KINDS]

        row = dict(config_label=label, status="measured")
        for kk, e, o in zip(KINDS, quad(engine_us / 1e3, 3), quad((wall_us - engine_us) / 1e3, 3)):
            row["engine_ms_" + kk], row["overhead_ms_" + kk] = e, o
        for kk, st, dy in zip(KINDS, quad(static_mj, 3), quad(dynamic_mj, 3)):
            row["static_mJ_" + kk], row["dynamic_mJ_" + kk] = st, dy
        for col, v, nd in (("token_ms", wall_us / 1e3, 3), ("tokens_s", 1e6 / wall_us, 2),
                           ("gmac_s", macs / wall_us / 1e3, 3),
                           ("energy_token_mJ", (static_mj + dynamic_mj), 3),
                           ("nj_per_mac", float(s["nj_per_mac_wall"]), 4), ("idle_W", idle_w, 3),
                           ("acc_median_pct", acc["acc_median_pct"], 3)):
            for kk, x in zip(KINDS, quad(v, nd)):
                row["%s_%s" % (col, kk)] = x
        row.update(
            kind=KIND_TEXT[k], engine_shape=shape, engines=n, hbm_channels=channels,
            clock_mhz=clk, lanes_total=plan["lanes"] * n, layers=plan["layers"],
            layer_rows=plan["layer_rows"], width=plan["width"],
            layer_rows_per_engine=s["layer_rows_per_engine"],
            useful_macs_per_token=macs, padding_pct=float(s["padding_pct"]),
            efficiency_pct=round(100.0 * eff, 2), efficiency_from=eff_from,
            predicted_us=round(plan["predicted_cycles"] / clk, 1),
            engine_us=round(engine_us, 1), layer_active_sum_us=round(active_us, 1),
            token_us=round(wall_us, 1), layer_us=round(wall_us / plan["layers"], 2),
            overhead_us=round(wall_us - engine_us, 1),
            overhead_pct=round(100.0 * (wall_us - engine_us) / wall_us, 2),
            gmac_s=round(macs / wall_us / 1e3, 3), gflops=round(2 * macs / wall_us / 1e3, 3),
            tokens_per_s=round(1e6 / wall_us, 2),
            board_load_W=round(load_w, 4), board_idle_W=round(idle_w, 4),
            dynamic_W=round(load_w - idle_w, 3),
            energy_token_mJ=round(static_mj + dynamic_mj, 3), static_mJ=round(static_mj, 3),
            dynamic_mJ=round(dynamic_mj, 3), nj_per_mac=float(s["nj_per_mac_wall"]),
            finish_balance=round(float(s["finish_balance"]), 4),
            balance_planned=float(s["balance_planned"]),
            timed_passes=npass, soak_passes=int(s["soak_passes"]), soak_s=float(s["soak_s"]))
        row.update(acc)
        row["acc_same_data_as"] = first_with[dataset(name, clk)[0]]
        row.update(window_cols(rr, ("qwen", name)))
        row.update(
            source=sources(rr),
            estimator="per token = 40 layers, each one 9216 x 2048 MIXED stacked GEMV (9 experts "
                      "x gate+up, 2304 rows per sparsity 2:4/2:8/2:16/2:32), split B over the "
                      "engines in lockstep, a new vector every layer; token time, energy, idle: "
                      "measured (60 s soak of whole tokens, board power), the mean of %d run(s) "
                      "(runs_* = min / max / half-range over them); engine time: sum over "
                      "layers of the busiest engine's plan cycles / clock / steady-state "
                      "efficiency (efficiency_from); overhead = token - engine; accuracy: the "
                      "gate's realistic layer, |card - exact| / rms; host: %s" % (len(rr), HOST))
        row.update(run_spreads(rr, "token_ms", "energy_token_mJ"))
        rows.append((key, row))
        measured.append((name, clk))
        print("%-24s %4d %4d %6.2f %9.1f %9.1f %9.1f %6.2f %7.2f %8.2f %8.1f %6.2f %4.2f-%-4.2f "
              "%13s %5s %22s" % (
                  label, len(rr), clk, 100 * eff, engine_us, active_us, wall_us,
                  100.0 * row["runs_token_ms_halfrange"] * 1e3 / wall_us, row["overhead_pct"],
                  row["tokens_per_s"], row["energy_token_mJ"],
                  100.0 * row["runs_energy_token_mJ_halfrange"] / row["energy_token_mJ"],
                  row["runs_dynamic_W_min"], row["runs_dynamic_W_max"],
                  "%s/%s" % (row["server_load_mean"], row["server_load_max"]),
                  row["server_busy_max"], "%.2f/%.2f/%.2f/%+.3f" % (
                      acc["acc_median_pct"], acc["acc_p95_pct"], acc["acc_max_pct"],
                      acc["acc_mean_offset_pct"])))
    rows = [r for _, r in sorted(rows, key=lambda x: x[0])]
    fields = next(list(r.keys()) for r in rows if r.get("status") == "measured")
    return rows, fields, measured


def build_accuracy(measured):
    """The realistic layer's rows pooled by sparsity -- each DISTINCT data set once (twins
    were gated on the same data and returned the same bits, so pooling them again would only
    count those rows two or three times)."""
    pooled = dict((sp, []) for sp in SPARSITY_ORDER)
    seen = {}
    for name, clk in measured:
        h, rows = dataset(name, clk)
        if h in seen:
            continue
        seen[h] = name
        for r in rows:
            if r["sparsity"] not in pooled:
                fail("%s: unknown sparsity %s" % (name, r["sparsity"]))
            if r["model_toward0_trunc"] != "1":
                fail("%s: a row the arithmetic model does not match" % name)
            pooled[r["sparsity"]].append((name, r))
    out = []
    print("\n== accuracy of the realistic layer by sparsity (%d builds, %d distinct data sets, "
          "each pooled once)" % (len(measured), len(seen)))
    print("%-6s %6s %6s %7s %8s %8s %8s %10s" % ("sparse", "terms", "rows", "sets", "median %",
                                                 "p95 %", "max %", "bias %"))
    for sp in SPARSITY_ORDER:
        rs = pooled[sp]
        if not rs:
            fail("no rows at %s" % sp)
        terms = set(int(r["terms"]) for _, r in rs)
        if len(terms) != 1:
            fail("rows at %s have %s terms" % (sp, sorted(terms)))
        errs = sorted(abs(float(r["offset_rel_rms"])) for _, r in rs)
        offs = [float(r["offset_rel_rms"]) for _, r in rs]
        row = dict(sparsity=sp, terms_per_row=terms.pop(), rows=len(rs),
                   data_sets=len(set(nm for nm, _ in rs)), builds=len(measured),
                   median_pct=round(100 * errs[len(errs) // 2], 3),
                   p95_pct=round(100 * errs[int(0.95 * (len(errs) - 1))], 3),
                   max_pct=round(100 * errs[-1], 3),
                   mean_abs_pct=round(100 * statistics.mean(errs), 3),
                   mean_offset_pct=round(100 * statistics.mean(offs), 4),
                   model_match_pct=100.0,
                   basis="|card - exact| / rms(exact) of each build's realistic layer "
                         "(weights N(0,1) magnitude-pruned, vector N(0,1)); every distinct "
                         "data set pooled once (twins share theirs)")
        out.append(row)
        print("%-6s %6d %6d %7d %8.3f %8.3f %8.3f %+10.4f" % (
            sp, row["terms_per_row"], row["rows"], row["data_sets"], row["median_pct"],
            row["p95_pct"], row["max_pct"], row["mean_offset_pct"]))
    return out


def main():
    ap = argparse.ArgumentParser(description="chart tables of the measurement window(s)")
    ap.add_argument("--window", action="append",
                    help="a results/window_<stamp> folder (repeatable; default: every one)")
    ap.add_argument("--runs", choices=("all", "latest"), default="all",
                    help="all (default): the mean of every run of a build, with min / max / "
                         "half-range columns; latest: the latest run alone")
    a = ap.parse_args()
    # window_<date>_<HHMM>[_k] only: the shape sweep's window_shapes_* folders are another study
    paths = a.window or sorted(d for d in glob.glob(os.path.join(RESULTS, "window_*"))
                               if re.match(r"window_\d{8}_\d{4}(_\d+)?$", os.path.basename(d))
                               and os.path.isfile(os.path.join(d, "window_summary.csv")))
    if not paths:
        fail("no results/window_*/window_summary.csv")
    wins = [Window(os.path.abspath(p)) for p in paths]
    runs_of = {}
    for w in wins:                                  # sorted by stamp: runs in time order
        print("%s: %d builds OK; load at the start %s, busy: %s; load log %s" % (
            w.name, len(w.ok), w.start_load, w.start_busy, w.load_log or "none"))
        for h, tool in w.md5s:
            local = os.path.join(WORKLOAD, tool)
            if os.path.exists(local) and md5(local) != h:
                print("  NOTE: workload/%s differs from the copy %s ran with" % (tool, w.name))
        for k in w.ok:
            runs_of.setdefault(k, []).append(w)
    if a.runs == "latest":
        runs_of = dict((k, v[-1:]) for k, v in runs_of.items())
    configs = read_csv(CONFIGS)
    uc1 = build_uc1(runs_of, configs)
    qwen, fields, measured = build_qwen(runs_of, configs)
    acc = build_accuracy(measured)
    print()
    write_csv(UC1_OUT, uc1)
    write_csv(QWEN_OUT, qwen, fields)
    write_csv(ACC_OUT, acc)


if __name__ == "__main__":
    main()

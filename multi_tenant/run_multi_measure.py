"""Drive the multi-tenant card measurements: the interference matrix and the soak.

    python3 run_multi_measure.py --xclbin sparse_4x4_x2_375.xclbin --clock 375 --tenants 2
    python3 run_multi_measure.py --xclbin sparse_4x4_x4_375.xclbin --clock 375 --tenants 4 --soak 60

Runs on the server, beside host_sparse_multi and the t0/ t1/ ... directories.
Python 3.6 compatible, stdlib only (plus power_scraper.py, and only for --soak).

WHAT IT MEASURES, AND WHY IN THIS ORDER
=======================================
ONE BITSTREAM GIVES THE WHOLE SCALING SERIES. The host takes k:dir pairs, so
running 1, 2, 3 or N tenants on an N-tenant bitstream is a command-line choice --
same silicon, same clock, same placement. So the series {1 tenant} ... {N
tenants} is measured without rebuilding anything, and the 1-tenant point is the
uncontended baseline every interference number divides by.

    alone      tenant k on its own        -> baseline span for tenant k
    cumulative {0}, {0,1}, {0,1,2} ...    -> how each tenant degrades as
                                             neighbours are added

INTERFERENCE IS A RATIO OF RUNS, NEVER A NUMBER FROM ONE RUN:

    interference(k, set) = span(k in set) / span(k alone)

1.00 means the neighbours cost that tenant nothing. The host also reports the
OVERLAP window (the part of a run where EVERY tenant was executing); a ratio
taken from a run whose tenants barely overlapped is meaningless, so the overlap
fraction is carried into the CSV beside every number and flagged when low.

CORRECTNESS IS CHECKED IN EVERY COMBINATION, not just once. After each run each
participating tenant is compared against its OWN golden. Tenants hold different
matrices, so a cross-wired stream or a neighbour trampling another's HBM range
shows up as a mismatch. A PASS column with a FAIL in it is the only outcome that
invalidates everything else in the row.

⚠️ DO NOT POINT Vitis/measure_power.py AT THIS HOST. Its regex takes the FIRST
"<number> Mrow/s sustained" line, which here is tenant 0's, not the aggregate --
it would silently report one tenant's throughput as the whole card's. The --soak
path below parses the host's aggregate line itself and samples power through
power_scraper.py (imported, not reimplemented).
"""

import argparse
import csv
import io
import os
import re
import statistics
import subprocess
import sys
import time

PC_BYTES = 32
LANES = 16                      # 4x4 tenant; --cores/--blocks set it for other shapes

# tenant | span us | rows | Mrow/s | GMAC/s | beats/cyc | eff % | active us | act eff%
RE_ROW = re.compile(r"^\s+t(\d+)\s+([\d.]+)\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)"
                    r"\s+([\d.]+)\s+([\d.]+)\s*$")
RE_UNION = re.compile(r"union window\s*:\s*([\d.]+) us")
RE_OVERLAP = re.compile(r"overlap window\s*:\s*([\d.]+) us\s*\(([\d.]+)%")
RE_ACT_OVERLAP = re.compile(r"active overlap\s*:\s*([\d.]+) us\s*\(([\d.]+)%")
RE_SOAK_T = re.compile(r"^t(\d+) soak: (\d+) calculations in ([\d.]+) s -> ([\d.]+) Mrow/s")
RE_SOAK_AGG = re.compile(r"soak AGGREGATE: ([\d.]+) Mrow/s")
RE_SOAK_START = re.compile(r"SOAK_START_EPOCH\s+([0-9.]+)")
RE_SOAK_END = re.compile(r"SOAK_END_EPOCH\s+([0-9.]+)")


def run_host(host, xclbin, clock, soak, members, dirs):
    cmd = [host, xclbin, str(clock), str(soak)] + ["%d:%s" % (k, dirs[k]) for k in members]
    print("   $ " + " ".join(cmd))
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         universal_newlines=True)
    out, _ = p.communicate()
    if p.returncode != 0:
        sys.stdout.write(out)
        raise SystemExit("host failed (%d) on set %s" % (p.returncode, members))
    return out


def parse_run(out):
    """-> {tenant: dict(...)}, union us, overlap %, ACTIVE overlap %"""
    per = {}
    for line in out.split("\n"):
        m = RE_ROW.match(line)
        if m:
            per[int(m.group(1))] = dict(
                span_us=float(m.group(2)), rows=int(m.group(3)), mrow_s=float(m.group(4)),
                gmac_s=float(m.group(5)), beats_cyc=float(m.group(6)),
                eff_pct=float(m.group(7)), active_us=float(m.group(8)),
                act_eff_pct=float(m.group(9)))
    u = RE_UNION.search(out)
    o = RE_OVERLAP.search(out)
    ao = RE_ACT_OVERLAP.search(out)
    return (per, (float(u.group(1)) if u else None), (float(o.group(2)) if o else None),
            (float(ao.group(2)) if ao else None))


def compare(tdir):
    """That tenant's own golden vs the output it just wrote. -> True/False."""
    try:
        os.remove(os.path.join(tdir, "tlast.txt"))
    except OSError:
        pass
    p = subprocess.Popen([sys.executable, "compare_gemv4_py36.py"], cwd=tdir,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         universal_newlines=True)
    out, _ = p.communicate()
    return ("=== PASS ===" in out), out


def soak(a, members, dirs, beats):
    """One power soak of `members`, sampled through power_scraper. -> a CSV row.

    Energy per ACTUAL MAC comes from the host's own iteration counts: tenant k did
    `calcs` calculations of beats[k] x 2 x LANES MACs each in `secs` seconds. That is
    exact and sparsity-independent, unlike rows (a 2:4 row costs 8x a 2:32 row).
    """
    label = "+".join(str(k) for k in members)
    print("\npower soak {%s}: %.0f s" % (label, a.soak))
    sampler = None
    summarise = None
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(a.host)) or ".")
        from power_scraper import Sampler, summarise, read_once
        pre = read_once(a.bdf)
        print("  pre-run  : board %.2f W, VCCINT %.2f W"
              % (pre["board_w"], pre["vccint_w"] or 0.0))
        sampler = Sampler(a.bdf, interval=1.0)
        sampler.start()
    except Exception as e:
        if not a.allow_no_power:
            raise SystemExit("no power sampling (%s) -- this soak would have no energy. Fix the "
                             "XRT environment (xbutil on PATH) or pass --allow-no-power." % e)
        print("  WARNING: no power sampling (%s). Throughput only." % e)

    out = run_host(a.host, a.xclbin, a.clock, a.soak, members, dirs)
    t0m, t1m = RE_SOAK_START.search(out), RE_SOAK_END.search(out)
    agg = RE_SOAK_AGG.search(out)
    per_t, macs_s = {}, 0.0
    for line in out.split("\n"):
        m = RE_SOAK_T.match(line)
        if m:
            k, calcs, secs = int(m.group(1)), int(m.group(2)), float(m.group(3))
            per_t[k] = float(m.group(4))
            macs_s += calcs / secs * beats[k] * 2 * LANES
    if not (t0m and t1m and agg) or sorted(per_t) != sorted(members):
        sys.stdout.write(out)
        raise SystemExit("soak markers, aggregate or per-tenant lines missing")
    t0, t1 = float(t0m.group(1)), float(t1m.group(1))
    print("  soak     : %.1f s, aggregate %.3f Mrow/s = %.3f GMAC/s actual, per tenant %s"
          % (t1 - t0, float(agg.group(1)), macs_s / 1e9,
             " ".join("t%d %.3f" % (k, v) for k, v in sorted(per_t.items()))))

    row = dict(xclbin=a.xclbin, clock_mhz=a.clock, tenants_in_bitstream=a.tenants,
               set=label, set_size=len(members), tenant="soak",
               mrow_s=round(float(agg.group(1)), 3), gmac_s_actual=round(macs_s / 1e9, 4),
               union_us=round((t1 - t0) * 1e6, 0),
               runs=" ".join("t%d=%.3f" % (k, v) for k, v in sorted(per_t.items())))
    if sampler is not None:
        print("  idle     : settling, then sampling %.0f s..." % a.idle_after)
        time.sleep(a.idle_after + 5.0)
        sampler.stop()
        pw = summarise(sampler.window(t0 + a.warmup, t1))
        idle = summarise(sampler.window(t1 + 5.0, time.time()))
        if pw and idle:
            b_load, b_idle = pw["board_mean"], idle["board_mean"]
            rows_s = float(agg.group(1)) * 1e6
            row.update(board_load_w=round(b_load, 3), board_idle_w=round(b_idle, 3),
                       vccint_load_w=round(pw.get("vccint_mean", 0.0), 3),
                       vccint_idle_w=round(idle.get("vccint_mean", 0.0), 3),
                       static_share_pct=round(100.0 * b_idle / b_load, 2),
                       energy_per_row_nJ=round(b_load / rows_s * 1e9, 3),
                       energy_per_mac_pJ=round(b_load / macs_s * 1e12, 2))
            print("  power    : board %.2f W load / %.2f W idle (static %.1f%%) -> "
                  "%.1f pJ per actual MAC, %.1f nJ per row"
                  % (b_load, b_idle, 100.0 * b_idle / b_load, b_load / macs_s * 1e12,
                     b_load / rows_s * 1e9))
        else:
            print("  WARNING: no samples inside the load or idle window")
    return row


def main():
    ap = argparse.ArgumentParser(description="multi-tenant interference + soak driver")
    ap.add_argument("--xclbin", required=True)
    ap.add_argument("--clock", type=float, required=True, help="what the xclbin was LINKED at")
    ap.add_argument("--tenants", type=int, required=True)
    ap.add_argument("--host", default="./host_sparse_multi")
    ap.add_argument("--cores", type=int, default=4, help="engine shape of ONE tenant")
    ap.add_argument("--blocks", type=int, default=4, help="engine shape of ONE tenant")
    ap.add_argument("--reps", type=int, default=3, help="repeats per set (default 3)")
    ap.add_argument("--soak", type=float, default=0.0,
                    help="seconds; >0 adds a power soak of the FULL set after the sweep")
    ap.add_argument("--soak-each", action="store_true",
                    help="with --soak: soak EVERY occupancy level {0}, {0,1}, ... instead of "
                         "only the full set -> energy per MAC vs tenants, one bitstream")
    ap.add_argument("--bdf", default=os.environ.get("BDF", "0000:af:00.1"))
    ap.add_argument("--warmup", type=float, default=6.0, help="soak seconds discarded for power")
    ap.add_argument("--idle-after", type=float, default=20.0)
    ap.add_argument("--csv", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-no-power", action="store_true",
                    help="let a soak run without power telemetry (throughput only). Off by "
                         "default: on 2026-09-24 a whole chain lost its energy data silently "
                         "because xbutil was not on PATH.")
    a = ap.parse_args()

    global LANES
    LANES = a.cores * a.blocks          # MACs per beat = 2 x LANES, whatever the shape

    # Fail fast without XRT: the host otherwise aborts with "XILINX_XRT not set" after
    # printing its stimulus summary, which reads like a run that started.
    if not a.dry_run and not os.environ.get("XILINX_XRT"):
        raise SystemExit("XILINX_XRT is not set in this shell. Run first:\n"
                         "  source /opt/Xilinx/Vitis/2021.1/settings64.sh\n"
                         "  source /opt/xilinx/xrt/setup.sh")
    # Power is checked NOW, not when the first soak starts minutes later.
    if a.soak > 0 and not a.dry_run and not a.allow_no_power:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(a.host)) or ".")
            from power_scraper import read_once
            read_once(a.bdf)
        except Exception as e:
            raise SystemExit("power telemetry does not work (%s). The soaks would have no "
                             "energy. Source /opt/xilinx/xrt/setup.sh (xbutil must be on PATH) "
                             "or pass --allow-no-power." % e)

    dirs = {k: "t%d" % k for k in range(a.tenants)}
    for k, d in sorted(dirs.items()):
        if not os.path.exists(os.path.join(d, "bin/weights_pc0.bin")):
            raise SystemExit("bin/weights_pc0.bin missing in %s -- run prep_tenants.py first" % d)
    with_golden = [k for k, d in sorted(dirs.items())
                   if os.path.exists(os.path.join(d, "golden.txt"))]
    if len(with_golden) < a.tenants:
        print("NOTE: %d of %d tenants have no golden.txt (measurement-sized stimulus), so no "
              "bit-exactness check for them. Prove correctness with --mode correctness first."
              % (a.tenants - len(with_golden), a.tenants))
    if not a.dry_run and not os.path.exists(a.host):
        raise SystemExit("no host at %s" % a.host)

    # alone first (the baselines every ratio needs), then cumulative sets
    sets = [[k] for k in range(a.tenants)]
    sets += [list(range(n)) for n in range(2, a.tenants + 1)]
    csv_path = a.csv or ("multi_x%d_%dMHz.csv" % (a.tenants, int(a.clock)))

    print("multi-tenant measurement: %d tenants, %s @ %.0f MHz, %d reps"
          % (a.tenants, a.xclbin, a.clock, a.reps))
    print("  %d sets: %s" % (len(sets), ", ".join("+".join(str(x) for x in s) for s in sets)))
    print("  CSV -> %s" % csv_path)
    if a.dry_run:
        for s in sets:
            print("   would run: " + " ".join("%d:%s" % (k, dirs[k]) for k in s))
        return

    # HOST LOAD IS RECORDED WITH EVERY SET. Spans come from device timestamps, but
    # they include the host enqueuing each CU, so a busy host (a Vivado link on the
    # same machine, another user) inflates them and adds jitter -- of the same order
    # as the interference being measured. A number taken under load is provisional.
    ncpu = os.cpu_count() or 1

    def host_load():
        try:
            return os.getloadavg()[0]
        except (AttributeError, OSError):
            return None

    rows, alone, alone_active = [], {}, {}
    for s in sets:
        label = "+".join(str(x) for x in s)
        load = host_load()
        print("\nset {%s}   host load %s on %d CPUs%s"
              % (label, "%.1f" % load if load is not None else "n/a", ncpu,
                 "   <-- BUSY: treat timing as provisional"
                 if load is not None and load > 0.5 * ncpu else ""))
        per_rep = []
        for r in range(a.reps):
            out = run_host(a.host, a.xclbin, a.clock, 0.0, s, dirs)
            per, union, overlap, act_overlap = parse_run(out)
            missing = [k for k in s if k not in per]
            if missing:
                sys.stdout.write(out)
                raise SystemExit("no result line for tenant(s) %s" % missing)
            # correctness in THIS combination, every rep -- only where a golden
            # exists. Measurement-sized stimulus comes from gen_timing_stimulus.py,
            # which writes no golden (the Python model would have to walk every
            # MAC); correctness is proven separately on the small stimulus.
            fails = []
            for k in s:
                if not os.path.exists(os.path.join(dirs[k], "golden.txt")):
                    continue
                ok, cout = compare(dirs[k])
                if not ok:
                    sys.stdout.write(cout)
                    fails.append(k)
            if fails:
                raise SystemExit("tenant(s) %s not bit-exact in set {%s} -- stopping; every "
                                 "number after this would be from a broken run." % (fails, label))
            per_rep.append((per, union, overlap, act_overlap))
            print("   rep %d: %s | active overlap %s%%" % (
                r, " ".join("t%d active %.1fus (span %.1f)"
                            % (k, per[k]["active_us"], per[k]["span_us"]) for k in s),
                "%.1f" % act_overlap if act_overlap is not None else "n/a"))

        for k in s:
            spans = [p[0][k]["span_us"] for p in per_rep]
            mean = statistics.mean(spans)
            actives = [p[0][k]["active_us"] for p in per_rep]
            amean = statistics.mean(actives)
            if len(s) == 1:
                alone[k] = mean
                alone_active[k] = amean
            rows.append(dict(
                # ---- the headline: ACTIVE span, free of most launch stagger ----
                active_us_mean=round(amean, 3),
                interference_active=(round(amean / alone_active[k], 4)
                                     if k in alone_active else ""),
                active_overlap_pct=(round(statistics.mean([p[3] for p in per_rep]), 2)
                                    if per_rep[0][3] is not None else ""),
                act_eff_pct=round(statistics.mean([p[0][k]["act_eff_pct"] for p in per_rep]), 2),
                active_runs=" ".join("%.3f" % x for x in actives),
                # ---- the full span, as before ----
                xclbin=a.xclbin, clock_mhz=a.clock, tenants_in_bitstream=a.tenants,
                set=label, set_size=len(s), tenant=k, rows_computed=per_rep[0][0][k]["rows"],
                span_us_mean=round(mean, 3),
                span_us_min=round(min(spans), 3), span_us_max=round(max(spans), 3),
                spread_pct=round(100.0 * (max(spans) - min(spans)) / min(spans), 3),
                mrow_s=round(statistics.mean([p[0][k]["mrow_s"] for p in per_rep]), 3),
                gmac_s=round(statistics.mean([p[0][k]["gmac_s"] for p in per_rep]), 3),
                beats_per_cycle=round(statistics.mean([p[0][k]["beats_cyc"] for p in per_rep]), 4),
                eff_pct=round(statistics.mean([p[0][k]["eff_pct"] for p in per_rep]), 2),
                interference=round(mean / alone[k], 4) if k in alone else "",
                verified=("golden" if os.path.exists(os.path.join(dirs[k], "golden.txt"))
                          else "no-golden"),
                union_us=round(statistics.mean([p[1] for p in per_rep]), 3) if per_rep[0][1] else "",
                overlap_pct=round(statistics.mean([p[2] for p in per_rep]), 2) if per_rep[0][2] else "",
                bit_exact=("yes" if os.path.exists(os.path.join(dirs[k], "golden.txt"))
                           else "n/a"),
                host_load_1min=round(load, 2) if load is not None else "",
                host_cpus=ncpu,
                runs=" ".join("%.3f" % x for x in spans)))

    # ---- optional power soaks --------------------------------------------
    # --soak S           one soak of the FULL set (all tenants)
    # --soak S --soak-each
    #                    one soak per OCCUPANCY LEVEL: {0}, {0,1}, ... {0..N-1}. Same
    #                    bitstream, same clock, same session -> energy per actual MAC as a
    #                    function of how many tenants are working, with no cross-session
    #                    or cross-clock caveat. (All N slots are configured in every
    #                    soak, so an idle slot's static power is charged to the busy
    #                    ones -- which is exactly the cost of an under-occupied card.)
    if a.soak > 0:
        soak_sets = ([list(range(n)) for n in range(1, a.tenants + 1)] if a.soak_each
                     else [list(range(a.tenants))])
        # weight beats per tenant, straight from its image: MACs per calculation =
        # beats x 2 MACs x LANES blocks, independent of sparsity
        beats = dict((k, os.path.getsize(os.path.join(dirs[k], "bin/weights_pc0.bin"))
                      // PC_BYTES) for k in dirs)
        for members in soak_sets:
            rows.append(soak(a, members, dirs, beats))

    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with io.open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("\nwrote %s : %d rows" % (csv_path, len(rows)))

    # Headline = the ACTIVE span (latest input-mover start -> end), which excludes most
    # of the host's enqueue stagger; the full span is kept beside it for comparison.
    print("\n%-10s %-7s %11s %13s %11s %10s %13s"
          % ("set", "tenant", "active us", "interference", "act.ovlp%", "span us",
             "interf.(span)"))
    for r in rows:
        if r["tenant"] == "soak":
            continue
        print("%-10s %-7s %11s %13s %11s %10s %13s"
              % ("{%s}" % r["set"], "t%s" % r["tenant"], r["active_us_mean"],
                 r["interference_active"] if r["interference_active"] != "" else "-",
                 r["active_overlap_pct"] if r["active_overlap_pct"] != "" else "-",
                 r["span_us_mean"], r["interference"] if r["interference"] != "" else "-"))
    low = [r for r in rows if r["tenant"] != "soak" and r["set_size"] > 1
           and r["active_overlap_pct"] != "" and float(r["active_overlap_pct"]) < 70.0]
    if low:
        print("\n  !! active overlap below 70%% in %d row(s): those engines were not all "
              "working at the same time, so their interference numbers understate "
              "contention." % len(low))
    soaks = [r for r in rows if r["tenant"] == "soak"]
    if soaks:
        print("\n%-10s %12s %10s %10s %9s %10s" % ("soak set", "GMAC/s act", "board W",
                                                  "idle W", "static%", "pJ/MAC"))
        for r in soaks:
            print("%-10s %12s %10s %10s %9s %10s"
                  % ("{%s}" % r["set"], r["gmac_s_actual"], r.get("board_load_w", "-"),
                     r.get("board_idle_w", "-"), r.get("static_share_pct", "-"),
                     r.get("energy_per_mac_pJ", "-")))
    checked = sorted(set(r["tenant"] for r in rows
                         if r["tenant"] != "soak" and r["bit_exact"] == "yes"))
    if checked:
        print("\n  tenant(s) %s were compared against their own golden after EVERY run and "
              "passed." % ", ".join("t%s" % t for t in checked))
    if len(checked) < a.tenants:
        print("  the rest had no golden (measurement stimulus) -- correctness for those comes "
              "from the --mode correctness runs.")


if __name__ == "__main__":
    main()

"""Run the MIXED shape sweep on ONE multi-engine build, then its power soak (plan_shapes.py).

    cd ~/GEMV_Sparse/Vitis_multi_x3_bcast              # the build folder: the .xclbin
    python3 <tools>/run_shapes_xrt.py --arch 3x4x4_bcast

A NEW FILE (2026-10-01). Imports plan_shapes.py (the plan) and run_workload_xrt.py (its host
runner, clock check and output regexes -- that file is not changed). The host is the build's
host_workload_xrt (native XRT) with the plan's flags (--lockstep, --lockstep --shared-vector,
--broadcast). The data is gen_timing_stimulus.py's, as in the single engines' sweeps and soaks
(constant patterns: its power is comparable with theirs, not with the Qwen test's random data).
Python 3.6, stdlib only.

PER HOST CALL (a group of shapes): for each shape both batch sizes, every engine its R copies of
its share, in the same order on every engine (lockstep); identical shares are copied, which also
gives a shared / broadcast build's engines the byte-identical vector the host requires. Then
    host_workload_xrt <flags> <xclbin> <MHz> <passes> 0 <k:dirs> ...
A step's time in a pass = first start to last end over the engines (and the broadcast pair).

PER SHAPE: in every pass, slope = (t_big - t_small) / (R_big - R_small) = the busiest engine's
time for ONE matrix, every fixed cost cancelled; latency = the MEDIAN over the passes (the first
pass after a load runs a few % slow); fixed cost = the median of t_small - R_small x slope (the
launch + lockstep + completion cost of one step). Checked: every calculation's beats and laps
against the plan, and every output.txt of the last pass for its row count.

THE SOAK: one host call, every engine the same 1024-wide MIXED share (plan_shapes.SOAK_*),
1 timed pass + --soak seconds of passes, board power sampled through power_scraper.py; idle
sampled for --idle-after seconds after it (bitstream loaded). Reported like the single engines'
power_<tag>_<MHz>MHz.csv: label, V, soak_s, iterations, Mrow_s_sustained, board load / idle.

CSV (in the build folder):
  shapes_xrt_<arch>_<MHz>MHz_MIXED.csv        11 rows, one per shape
  shapes_xrt_<arch>_<MHz>MHz_MIXED_steps.csv  every step of every timed pass
  power_xrt_<arch>_<MHz>MHz_MIXED.csv         the soak (one row)
"""

import argparse
import csv
import io
import os
import re
import shutil
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plan_shapes as ps                                # noqa: E402
import run_workload_xrt as rw                           # noqa: E402

PC_BYTES = 32
STIM = "wl_shapes"
RE_BCAST = re.compile(r"^BCAST pass=(\d+) calc=(\d+) start_us=([\d.]+) end_us=([\d.]+)\s*$",
                      re.M)


def gen(a, p, nwin, segs, d, made):
    """gen_timing_stimulus.py for one calculation into d/bin (or a copy of an identical one)."""
    os.makedirs(os.path.join(d, "bin"))
    key = (nwin, tuple(tuple(s) for s in segs))
    if key in made:
        for f in os.listdir(made[key]):
            shutil.copy2(os.path.join(made[key], f), os.path.join(d, "bin", f))
    else:
        cmd = [sys.executable, "gen_timing_stimulus.py", "--cores", str(p["cores"]),
               "--blocks", str(p["blocks"]), "--nwin", str(nwin)]
        if len(segs) == 1:
            cmd += ["--sparsity", segs[0][0], "--nlaps", str(segs[0][1])]
        else:
            cmd += ["--mix", ",".join("%s:%d" % (c, n) for c, n in segs)]
        rw.run(cmd, cwd=a.emu, what="gen_timing_stimulus")
        for f in os.listdir(os.path.join(a.emu, "bin")):
            if f.endswith(".bin") and not f.startswith("c_pc"):
                shutil.move(os.path.join(a.emu, "bin", f), os.path.join(d, "bin", f))
        made[key] = os.path.join(d, "bin")
    beats, _cyc, laps = ps.share_cost(segs, nwin)
    wb = os.path.getsize(os.path.join(d, "bin", "weights_pc0.bin")) // PC_BYTES
    ab = os.path.getsize(os.path.join(d, "bin", "act_pc0.bin")) // PC_BYTES
    if wb != beats or ab != nwin:
        raise SystemExit("%s: %d weight / %d activation beats, the plan says %d / %d"
                         % (d, wb, ab, beats, nwin))
    return beats, laps


def build_calls(a, p, calls):
    """Stimulus for the given calls -> per call: specs, steps [(shape index, size, calc j)],
    expected (beats, laps) per (engine, calc)."""
    need = 0
    for steps in calls:
        for i in steps:
            s = p["shapes"][i]
            need += sum((s["r_small"] + s["r_big"]) * b for b in s["engine_beats"])
    need *= PC_BYTES * sum(p["channels_per_tenant"][:2])
    free = shutil.disk_usage(".").free
    if free < 2 * need / len(calls) + 2e9:
        raise SystemExit("need ~%.1f GB free for one call's stimulus, have %.1f GB"
                         % (need / len(calls) / 1e9, free / 1e9))
    return need


def make_call(a, p, idxs, base):
    if os.path.isdir(base):
        shutil.rmtree(base)
    made, dirs, steps, expect = {}, dict((k, []) for k in range(p["tenants"])), [], {}
    j = 0
    for i in idxs:
        s = p["shapes"][i]
        for size in ("small", "big"):
            for k in range(p["tenants"]):
                d = os.path.join(base, "t%d" % k, "c%d" % j)
                expect[(k, j)] = gen(a, p, s["nwin"], s["calc_" + size][k], d, made)
                dirs[k].append(os.path.relpath(d))
            steps.append((i, size, j))
            j += 1
    specs = ["%s:%s" % (rw.tenant_key(k, p["tenants"]), ",".join(dirs[k])) for k in sorted(dirs)]
    return specs, steps, expect


def parse(out, p, nsteps, passes, expect):
    calcs = []
    for m in rw.RE_CALC.finditer(out):
        calcs.append(dict(pass_=int(m.group(1)), tenant=rw.tenant_index(m.group(2)),
                          calc=int(m.group(3)), beats=int(m.group(4)), laps=int(m.group(5)),
                          start_us=float(m.group(6)), active_us=float(m.group(7)),
                          end_us=float(m.group(8))))
    if len(calcs) != passes * nsteps * p["tenants"]:
        sys.stdout.write(out[-3000:])
        raise SystemExit("expected %d passes x %d steps x %d engines in the host output, got %d"
                         % (passes, nsteps, p["tenants"], len(calcs)))
    for r in calcs:
        want = expect[(r["tenant"], r["calc"])]
        if (r["beats"], r["laps"]) != want:
            raise SystemExit("engine %d calc %d ran %d beats / %d laps, the plan says %d / %d"
                             % ((r["tenant"], r["calc"], r["beats"], r["laps"]) + tuple(want)))
    bc = dict(((int(m.group(1)), int(m.group(2))), (float(m.group(3)), float(m.group(4))))
              for m in RE_BCAST.finditer(out))
    t = {}
    for ps_ in range(passes):
        for j in range(nsteps):
            rs = [r for r in calcs if r["pass_"] == ps_ and r["calc"] == j]
            starts = [r["start_us"] for r in rs] + ([bc[(ps_, j)][0]] if (ps_, j) in bc else [])
            ends = [r["end_us"] for r in rs] + ([bc[(ps_, j)][1]] if (ps_, j) in bc else [])
            t[(ps_, j)] = (min(starts), max(ends))
    return t


def check_outputs(p, base, expect):
    bad = []
    for (k, j), (_beats, laps) in sorted(expect.items()):
        f = os.path.join(base, "t%d" % k, "c%d" % j, "output.txt")
        n = len(rw.read_lines(f)) if os.path.exists(f) else -1
        if n != laps * p["lanes"]:
            bad.append("engine %d calc %d: %s rows, expected %d" % (k, j, n if n >= 0 else "no",
                                                                    laps * p["lanes"]))
    return bad


def sweep(a, p, clk):
    rows, steps_out = [], []
    for g, idxs in enumerate(p["groups"]):
        print("call %d/%d: %s -- generating stimulus..." % (
            g + 1, len(p["groups"]), " ".join(p["shapes"][i]["shape"] for i in idxs)))
        sys.stdout.flush()
        t0 = time.time()
        specs, steps, expect = make_call(a, p, idxs, os.path.abspath(STIM))
        print("  %d calculations ready in %.0f s; running %d timed passes"
              % (len(expect), time.time() - t0, a.passes))
        out = rw.run_host([a.host] + p["host_flags"] + [p["xclbin"], str(clk), str(a.passes),
                                                        "0"] + specs,
                          timeout=a.host_timeout)
        times = parse(out, p, len(steps), a.passes, expect)
        bad = check_outputs(p, os.path.abspath(STIM), expect)
        if bad:
            raise SystemExit("outputs of the last pass malformed: %s" % "; ".join(bad[:6]))
        by = dict(((i, size), j) for i, size, j in steps)
        for i in idxs:
            s = p["shapes"][i]
            js, jb = by[(i, "small")], by[(i, "big")]
            slopes, fixed = [], []
            for q in range(a.passes):
                ts = times[(q, js)][1] - times[(q, js)][0]
                tb = times[(q, jb)][1] - times[(q, jb)][0]
                k = (tb - ts) / float(s["r_big"] - s["r_small"])
                slopes.append(k)
                fixed.append(ts - s["r_small"] * k)
                steps_out.append(dict(shape=s["shape"], pass_=q, r_small=s["r_small"],
                                      r_big=s["r_big"], t_small_us=round(ts, 3),
                                      t_big_us=round(tb, 3), slope_us=round(k, 5),
                                      fixed_us=round(ts - s["r_small"] * k, 3), call=g + 1))
            lat = statistics.median(slopes)
            if lat <= 0:
                raise SystemExit("%s: latency %.4f us -- the timing makes no sense" % (s["shape"],
                                                                                    lat))
            ideal = s["busiest_cycles"] / float(clk)
            eng = "/".join(str(x) for x in s["engine_laps"])
            rows.append(dict(
                design="sparse", config=p["arch"], kind=p["kind"], cores=p["cores"],
                blocks=p["blocks"], lanes=p["lanes"], engines=p["tenants"], sparsity="MIXED",
                clock_mhz=clk, shape=s["shape"], M_rows=s["M"], N_cols=s["N"], nwin=s["nwin"],
                laps_per_matrix=s["laps"], laps_per_engine=eng, padding_rows=s["padding_rows"],
                beats_per_matrix=s["total_beats"], busiest_beats=max(s["engine_beats"]),
                busiest_cycles=s["busiest_cycles"], balance=s["balance"],
                batch_R_small=s["r_small"], batch_R_big=s["r_big"],
                t_small_us=round(statistics.median(times[(q, js)][1] - times[(q, js)][0]
                                                   for q in range(a.passes)), 3),
                t_big_us=round(statistics.median(times[(q, jb)][1] - times[(q, jb)][0]
                                                 for q in range(a.passes)), 3),
                latency_per_matrix_us=round(lat, 5), fixed_cost_us=round(
                    statistics.median(fixed), 3),
                ideal_per_matrix_us=round(ideal, 5), occupancy=round(ideal / lat, 4),
                GFLOPS_effective=round(2.0 * s["M"] * s["N"] / lat / 1e3, 3),
                GFLOPS_actual=round(4.0 * p["lanes"] * s["total_beats"] / lat / 1e3, 3),
                useful_macs=s["useful_macs"],
                spread_pct=round(100.0 * (max(slopes[1:] or slopes) - min(slopes[1:] or slopes))
                                 / lat, 3),
                passes=a.passes, slopes_us=" ".join("%.5f" % x for x in slopes),
                host_load_1min=round(os.getloadavg()[0], 2) if hasattr(os, "getloadavg") else "",
                call=g + 1))
            print("  %-4s %5dx%-5d R %4d/%-5d  %9.3f us per matrix (ideal %9.3f, occ %.3f), "
                  "fixed %.0f us, spread %.2f%%" % (s["shape"], s["M"], s["N"], s["r_small"],
                                                     s["r_big"], lat, ideal, ideal / lat,
                                                     rows[-1]["fixed_cost_us"],
                                                     rows[-1]["spread_pct"]))
        if not a.keep:
            shutil.rmtree(STIM, ignore_errors=True)
    rows.sort(key=lambda r: int(r["shape"][1:]))
    return rows, steps_out


def soak(a, p, clk):
    try:
        sys.path.insert(0, os.getcwd())
        from power_scraper import Sampler, read_once, summarise
        r = read_once(a.bdf)
        print("power check: board %.2f W, VCCINT %.2f W" % (r["board_w"], r["vccint_w"] or 0))
    except Exception as e:                               # noqa: BLE001
        raise SystemExit("power telemetry does not work (%s) -- source /opt/xilinx/xrt/setup.sh"
                         % e)
    base = os.path.abspath(STIM + "_soak")
    if os.path.isdir(base):
        shutil.rmtree(base)
    made, specs, expect = {}, [], {}
    for k in range(p["tenants"]):
        d = os.path.join(base, "t%d" % k, "c0")
        expect[(k, 0)] = gen(a, p, p["soak_nwin"], p["soak_segments"], d, made)
        specs.append("%s:%s" % (rw.tenant_key(k, p["tenants"]), os.path.relpath(d)))
    sampler = Sampler(a.bdf, interval=1.0)
    sampler.start()
    out = rw.run_host([a.host] + p["host_flags"] + [p["xclbin"], str(clk), "1", str(a.soak)]
                      + specs, timeout=int(900 + 3 * a.soak))
    parse(out, p, 1, 1, expect)
    sm = rw.RE_SOAK.search(out)
    if not sm:
        sampler.stop()
        raise SystemExit("no SOAK line in the host output")
    npass, secs = int(sm.group(1)), float(sm.group(2))
    t0 = float(rw.RE_SOAK_START.search(out).group(1))
    t1 = float(rw.RE_SOAK_END.search(out).group(1))
    print("idle: sampling %.0f s after the soak..." % a.idle_after)
    time.sleep(a.idle_after + 5.0)
    sampler.stop()
    pwr = summarise(sampler.window(t0 + a.warmup, t1))
    idle = summarise(sampler.window(t1 + 5.0, time.time()))
    if not (pwr and idle):
        raise SystemExit("no power samples inside the soak or the idle window")
    if not a.keep:
        shutil.rmtree(base, ignore_errors=True)
    rows_s = p["soak_rows_per_pass"] * npass / secs
    return dict(label="%s MIXED" % p["arch"], clock_mhz=clk, V=32 * p["soak_nwin"],
                engines=p["tenants"], soak_s=round(secs, 2), iterations=npass,
                Mrow_s_sustained=round(rows_s / 1e6, 3),
                board_load_w=round(pwr["board_mean"], 3), board_idle_w=round(idle["board_mean"], 3),
                board_delta_w=round(pwr["board_mean"] - idle["board_mean"], 3),
                vccint_load_w=round(pwr.get("vccint_mean", 0.0), 3),
                vccint_idle_w=round(idle.get("vccint_mean", 0.0), 3),
                n_load=pwr.get("n", ""), n_idle=idle.get("n", ""),
                beats_per_engine_per_pass=p["soak_beats"],
                host_load_1min=round(os.getloadavg()[0], 2) if hasattr(os, "getloadavg") else "")


def write(path, rows):
    with io.open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print("wrote %s" % path)


def main():
    ap = argparse.ArgumentParser(description="the MIXED shape sweep on one multi-engine build")
    ap.add_argument("--arch", required=True, choices=[b[0] for b in ps.builds()])
    ap.add_argument("--emu", default=os.environ.get(
        "EMU", os.path.expanduser("~/GEMV_Sparse/GEMV_4.0_Source/Emulation")))
    ap.add_argument("--host", default="./host_workload_xrt")
    ap.add_argument("--passes", type=int, default=10, help="timed passes per call (default 10)")
    ap.add_argument("--soak", type=float, default=60.0, help="power soak seconds (default 60; "
                                                             "0 = no soak)")
    ap.add_argument("--idle-after", type=float, default=20.0)
    ap.add_argument("--warmup", type=float, default=6.0)
    ap.add_argument("--host-timeout", type=int, default=600)
    ap.add_argument("--bdf", default=os.environ.get("BDF", "0000:af:00.1"))
    ap.add_argument("--keep", action="store_true", help="keep the stimulus folders")
    a = ap.parse_args()

    if not os.environ.get("XILINX_XRT"):
        raise SystemExit("XILINX_XRT is not set -- run: source /opt/xilinx/xrt/setup.sh")
    if a.passes < 3:
        raise SystemExit("--passes must be at least 3 (the latency is a median over passes)")
    p = ps.plan(a.arch)
    if not os.path.exists(p["xclbin"]):
        raise SystemExit("no %s in %s" % (p["xclbin"], os.getcwd()))
    if not os.path.exists(a.host):
        raise SystemExit("no host at %s -- build host_workload_xrt for %dx%d first"
                         % (a.host, p["cores"], p["blocks"]))
    clk = rw.data_clk(p["xclbin"])
    if clk != p["clock_mhz"]:
        raise SystemExit("%s runs at %d MHz but the plan expects %d MHz" % (p["xclbin"], clk,
                                                                         p["clock_mhz"]))
    need = build_calls(a, p, p["groups"])
    print("%s: %d x %s (%s), %s at %d MHz, host %s; 11 MIXED shapes in %d host calls "
          "(%.1f GB of stimulus in all, one call at a time), %d timed passes each"
          % (p["arch"], p["tenants"], p["shape"], p["kind"], p["xclbin"], clk,
             " ".join(p["host_flags"]), len(p["groups"]), need / 1e9, a.passes))
    stem = "shapes_xrt_%s_%dMHz_MIXED" % (p["arch"], clk)
    rows, steps = sweep(a, p, clk)
    pw_row = soak(a, p, clk) if a.soak > 0 else None
    write(stem + ".csv", rows)
    write(stem + "_steps.csv", steps)
    if pw_row:
        write("power_xrt_%s_%dMHz_MIXED.csv" % (p["arch"], clk), [pw_row])
        print("soak: %d passes in %.1f s, %.1f Mrow/s; board %.2f W load / %.2f W idle"
              % (pw_row["iterations"], pw_row["soak_s"], pw_row["Mrow_s_sustained"],
                 pw_row["board_load_w"], pw_row["board_idle_w"]))
    occ = [r["occupancy"] for r in rows]
    print("\n=== SHAPES DONE %s at %d MHz: 11 MIXED shapes, occupancy %.3f .. %.3f, fixed cost "
          "per step %.0f .. %.0f us" % (p["arch"], clk, min(occ), max(occ),
                                        min(r["fixed_cost_us"] for r in rows),
                                        max(r["fixed_cost_us"] for r in rows)))


if __name__ == "__main__":
    main()

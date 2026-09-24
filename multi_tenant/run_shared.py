"""Latency (and energy) of the SHARED workload across the family's eleven MIXED matrices.

    python3 run_shared.py --xclbin sparse_4x4_x4_375.xclbin --clock 374 \\
        --emu ~/GEMV_Sparse/GEMV_4.0_Source/Emulation --soak 60

Runs on the server beside host_sparse_multi (and power_scraper.py, for --soak).
Python 3.6, stdlib only. Correctness is proven separately: prep_shared.py correctness.

TWO CONFIGURATIONS, SAME BITSTREAM, SAME CLOCK, for every MIXED shape:
  coop    four tenants; tenant k = quarter k at ONE mode (2:4, 2:8, 2:16, 2:32); one vector
  single  tenant 0 alone does the whole MIXED matrix -- what one 4x4 engine does
so the speedup is like for like. Expected 15/8 = 1.875x in steady state: the 2:4 quarter
carries 8/15 of the work and the job ends when it does.

MEASURED EXACTLY LIKE run_shapes.py, so the numbers can sit beside Chart 12:
  * BATCHING: R matrices back to back (each tenant runs R copies of its quarter, which the
    engine cannot tell from one tall matrix), R chosen so the 2:4 tenant clears
    --target-beats. The single run is batched the same way on its whole-matrix beats.
  * OVERHEAD: three sizes at N=1024, least squares of span vs beats, intercept = the launch
    overhead of THAT configuration -- 24 CUs for coop, 6 for single, so each gets its own.
  * latency per matrix = (mean span - overhead) / R
The coop span is the host's UNION window (first mover start -> last mover end, all four
tenants): the job is done when its slowest quarter is.

THE 2:4 TENANT IS LAUNCHED LAST, ON PURPOSE. The host starts tenants in argument order,
~44 us per CU apart. Launched first, the 2:4 tenant can finish BEFORE the last-launched
2:32 tenant on a short run or a busy host -- the stagger then sets the union window, not
the work, and the overhead fit is garbage (a stand-in test produced a "2588 MHz" slope
exactly that way). Launched last it starts latest AND has the most work, so it always
finishes last, and the stagger is a constant the fit's intercept absorbs. Every fit is
also checked: its slope must come back as roughly the clock, or nothing is written.

POWER (--soak S): one soak per configuration on G3 (1024x1024). The coop soak is LOCKSTEP
(host --lockstep: launch all four, wait for all, repeat), so the fast tenants idle exactly as
in the real job. Batched 3x deeper than the latency runs so the launch gap between
iterations stays a small share of the soak. Energy per matrix = power x latency, split
static = idle x latency and dynamic = (load - idle) x latency -- the family method of
make_family_mixed_csv.py, each configuration with its own soak.

Everything is regenerated per shape into <out>/t0..t3 (coop) and <out>/full (single), and the
shared Emulation directory is left holding the last shape's stimulus.
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

import prep_tenants as pt
from prep_shared import MODES, SHAPES, SHAPE, quarter_laps, W_FILES, I_FILES, A_FILES, fresh

MIX_SUM = sum(pt.FREEZE[c] for c in MODES)             # 8 + 4 + 2 + 1 = 15
MAX_BEATS_PER_PC = 256 * 1024 * 1024 // pt.PC_BYTES    # one HBM pseudo-channel
FIT_NWIN = 32                                          # overhead fits at N = 1024
SOAK_SHAPE = "G3"
SOAK_DEPTH = 3                                         # soak batch = 3 x --target-beats
LAUNCH_ORDER = [3, 2, 1, 0]                            # the heaviest quarter (2:4) last
SLOPE_BAND = (0.80, 1.02)                              # fitted MHz / clock must land here

RE_ROW = re.compile(r"^\s+t(\d+)\s+([\d.]+)\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)"
                    r"\s+([\d.]+)\s+([\d.]+)\s*$")
RE_UNION = re.compile(r"union window\s*:\s*([\d.]+) us")
RE_STIM = re.compile(r"^t(\d+) stimulus: (\d+) weight beats", re.M)
RE_SOAK_T = re.compile(r"^t(\d+) soak: (\d+) calculations in ([\d.]+) s", re.M)
RE_SOAK_START = re.compile(r"SOAK_START_EPOCH\s+([0-9.]+)")
RE_SOAK_END = re.compile(r"SOAK_END_EPOCH\s+([0-9.]+)")


def ceildiv(a, b):
    return -(-a // b)


def fit_line(xs, ys):
    """Least squares y = a + b x -> (intercept, slope, r2, intercept_se). As run_shapes.py."""
    n = float(len(xs))
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a0 = my - b * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (a0 + b * x)) ** 2 for x, y in zip(xs, ys))
    r2 = (1.0 - ss_res / ss_tot) if ss_tot > 0 else 1.0
    se = ((ss_res / (n - 2)) * (1.0 / n + mx * mx / sxx)) ** 0.5 if n > 2 else float("nan")
    return a0, b, r2, se


class Rig(object):
    """Stimulus staging and host runs for one xclbin."""

    def __init__(self, a):
        self.a = a
        self.emu = os.path.abspath(os.path.expanduser(a.emu))
        self.out = os.path.abspath(os.path.expanduser(a.out))
        self.tdir = [os.path.join(self.out, "t%d" % k) for k in range(4)]
        self.fdir = os.path.join(self.out, "full")

    # ---- stimulus --------------------------------------------------------------------
    def gen(self, args):
        pt.run([sys.executable, "gen_timing_stimulus.py", "--cores", str(pt.CORES),
                "--blocks", str(pt.BLOCKS)] + args, cwd=self.emu)

    def stage(self, dst):
        fresh(dst)
        for f in W_FILES + I_FILES + A_FILES:
            shutil.copy2(os.path.join(self.emu, "bin", f), os.path.join(dst, "bin"))

    def beats_in(self, d, f):
        return os.path.getsize(os.path.join(d, "bin", f)) // pt.PC_BYTES

    def prep_coop(self, nwin, Q):
        """Tenant k <- Q laps at MODES[k], every tenant the same nwin (the same vector)."""
        beats = []
        for k, code in enumerate(MODES):
            self.gen(["--nwin", str(nwin), "--sparsity", code, "--nlaps", str(Q)])
            self.stage(self.tdir[k])
            beats.append(Q * nwin * pt.FREEZE[code])
            if self.beats_in(self.tdir[k], W_FILES[0]) != beats[-1]:
                raise SystemExit("t%d: %d weight beats staged, expected %d"
                                 % (k, self.beats_in(self.tdir[k], W_FILES[0]), beats[-1]))
        act = [pt.md5(os.path.join(d, "bin", A_FILES[0])) for d in self.tdir]
        if len(set(act)) != 1 or self.beats_in(self.tdir[0], A_FILES[0]) != nwin:
            raise SystemExit("the four tenants do not carry one and the same %d-window vector"
                             % nwin)
        return beats

    def prep_single(self, nwin, Q):
        """Tenant 0 <- the whole MIXED matrix: Q laps at each of the four modes."""
        self.gen(["--nwin", str(nwin), "--mix", ",".join("%s:%d" % (c, Q) for c in MODES)])
        self.stage(self.fdir)
        beats = Q * nwin * MIX_SUM
        if self.beats_in(self.fdir, W_FILES[0]) != beats:
            raise SystemExit("full: %d weight beats staged, expected %d"
                             % (self.beats_in(self.fdir, W_FILES[0]), beats))
        return beats

    # ---- host ------------------------------------------------------------------------
    def host(self, members, soak=0.0, lockstep=False):
        cmd = [self.a.host, self.a.xclbin, str(self.a.clock), str(soak)]
        cmd += (["--lockstep"] if lockstep else [])
        cmd += ["%d:%s" % (k, os.path.relpath(d)) for k, d in members]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             universal_newlines=True)
        out = p.communicate()[0]
        if p.returncode != 0:
            sys.stdout.write(out)
            raise SystemExit("host failed (%d): %s" % (p.returncode, " ".join(cmd)))
        return out

    @staticmethod
    def rows_of(out):
        per = {}
        for line in out.split("\n"):
            m = RE_ROW.match(line)
            if m:
                per[int(m.group(1))] = dict(span=float(m.group(2)), rows=int(m.group(3)),
                                            active=float(m.group(8)))
        return per

    def check(self, out, expect):
        """expect = {tenant: (weight beats, rows)} -- the host must have run exactly that."""
        got = dict((int(k), int(b)) for k, b in RE_STIM.findall(out))
        per = self.rows_of(out)
        for k, (beats, rows) in expect.items():
            if got.get(k) != beats or k not in per or per[k]["rows"] != rows:
                sys.stdout.write(out)
                raise SystemExit("t%d: the host ran %s beats / %s rows, expected %d / %d"
                                 % (k, got.get(k), per.get(k, {}).get("rows"), beats, rows))
        return per

    def coop_run(self, beats, Q):
        # 2:4 (t0) LAST -- see the module docstring
        out = self.host([(k, self.tdir[k]) for k in LAUNCH_ORDER])
        per = self.check(out, dict((k, (beats[k], Q * pt.LANES)) for k in range(4)))
        u = RE_UNION.search(out)
        if not u:
            sys.stdout.write(out)
            raise SystemExit("no union window in the coop host output")
        return float(u.group(1)), per

    def single_run(self, beats, Q):
        out = self.host([(0, self.fdir)])
        per = self.check(out, {0: (beats, 4 * Q * pt.LANES)})
        return per[0]["span"], per


def host_load():
    try:
        return round(os.getloadavg()[0], 2)
    except (AttributeError, OSError):
        return ""


def soak(rig, a, members, beats, lockstep, label):
    """One power soak -> dict. Mirrors run_multi_measure.soak(), plus --lockstep."""
    print("\n  power soak, %s: %.0f s%s" % (label, a.soak, ", LOCKSTEP" if lockstep else ""))
    sampler = summarise = None
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(a.host)) or ".")
        from power_scraper import Sampler, summarise, read_once
        pre = read_once(a.bdf)
        print("    pre-run : board %.2f W, VCCINT %.2f W" % (pre["board_w"], pre["vccint_w"] or 0.0))
        sampler = Sampler(a.bdf, interval=1.0)
        sampler.start()
    except Exception as e:                                  # noqa: BLE001 -- report and go on
        print("    WARNING: no power sampling (%s)" % e)
    out = rig.host(members, soak=a.soak, lockstep=lockstep)
    t0m, t1m = RE_SOAK_START.search(out), RE_SOAK_END.search(out)
    calcs = dict((int(k), (int(n), float(s))) for k, n, s in RE_SOAK_T.findall(out))
    if not (t0m and t1m) or sorted(calcs) != sorted(k for k, _ in members):
        sys.stdout.write(out)
        raise SystemExit("soak markers or per-tenant lines missing")
    t0, t1 = float(t0m.group(1)), float(t1m.group(1))
    macs_s = sum(n / s * beats[k] * 2 * pt.LANES for k, (n, s) in calcs.items())
    jobs = min(n for n, _ in calcs.values())
    r = dict(s=round(t1 - t0, 1), jobs=jobs, gmac_s=round(macs_s / 1e9, 3))
    print("    %d jobs in %.1f s, %.2f GMAC/s actual" % (jobs, t1 - t0, macs_s / 1e9))
    if sampler is not None:
        time.sleep(a.idle_after + 5.0)
        sampler.stop()
        pw = summarise(sampler.window(t0 + a.warmup, t1))
        idle = summarise(sampler.window(t1 + 5.0, time.time()))
        if pw and idle:
            r.update(board_load_W=round(pw["board_mean"], 3), board_idle_W=round(idle["board_mean"], 3),
                     vccint_load_W=round(pw.get("vccint_mean", 0.0), 3),
                     vccint_idle_W=round(idle.get("vccint_mean", 0.0), 3))
            print("    board %.2f W load / %.2f W idle" % (r["board_load_W"], r["board_idle_W"]))
    return r


def main():
    ap = argparse.ArgumentParser(description="shared-workload latency/energy sweep")
    ap.add_argument("--xclbin", required=True)
    ap.add_argument("--clock", type=float, required=True, help="the RUNTIME clock (DATA_CLK)")
    ap.add_argument("--emu", required=True, help="GEMV_4.0_Source/Emulation directory")
    ap.add_argument("--host", default="./host_sparse_multi")
    ap.add_argument("--out", default="shared", help="stimulus directory (t0..t3, full)")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--target-beats", type=int, default=2097152)
    ap.add_argument("--only", default=None, help="comma-separated shape names, e.g. G1,G3")
    ap.add_argument("--soak", type=float, default=0.0, help="seconds per configuration; 0 = off")
    ap.add_argument("--bdf", default=os.environ.get("BDF", "0000:af:00.1"))
    ap.add_argument("--warmup", type=float, default=6.0)
    ap.add_argument("--idle-after", type=float, default=20.0)
    ap.add_argument("--csv", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not a.dry_run and not os.environ.get("XILINX_XRT"):
        raise SystemExit("XILINX_XRT is not set in this shell. Run first:\n"
                         "  source /opt/Xilinx/Vitis/2021.1/settings64.sh\n"
                         "  source /opt/xilinx/xrt/setup.sh")
    if not a.dry_run:
        for p, what in ((a.host, "host"), (a.xclbin, "xclbin"),
                        (os.path.join(os.path.expanduser(a.emu), "gen_timing_stimulus.py"),
                         "generator")):
            if not os.path.exists(p):
                raise SystemExit("no %s at %s" % (what, p))
    shapes = SHAPES
    if a.only:
        want = [s.strip() for s in a.only.split(",") if s.strip()]
        bad = [w for w in want if w not in SHAPE]
        if bad:
            raise SystemExit("unknown shape(s) %s -- known: %s" % (bad, ", ".join(SHAPE)))
        shapes = [(w,) + SHAPE[w] for w in want]
    csv_path = a.csv or "shared_x4_%dMHz.csv" % int(a.clock)

    # ---- plan -------------------------------------------------------------------------
    plan = []
    for name, M, N in shapes:
        if N % 32 or N > 8192:
            raise SystemExit("%s: N=%d must be a multiple of 32 and <= 8192" % (name, N))
        nwin, q = N // 32, quarter_laps(M)
        b1 = [q * nwin * pt.FREEZE[c] for c in MODES]                 # per matrix, per tenant
        R = max(1, ceildiv(a.target_beats, b1[0]))
        while R > 1 and R * b1[0] > MAX_BEATS_PER_PC:
            R -= 1
        Rs = max(1, ceildiv(a.target_beats, q * nwin * MIX_SUM))
        while Rs > 1 and Rs * q * nwin * MIX_SUM > MAX_BEATS_PER_PC:
            Rs -= 1
        plan.append(dict(name=name, M=M, N=N, nwin=nwin, q=q, b1=b1, R=R, Rs=Rs,
                         pad=4 * q * pt.LANES - M))
    print("shared workload on %s @ %.0f MHz: %d shape(s), %d rep(s); tenant k = quarter k "
          "at %s" % (a.xclbin, a.clock, len(plan), a.reps,
                     ", ".join(pt.SP_NAME[c] for c in MODES)))
    print("  %-4s %11s %5s %5s %28s %6s %6s" % ("", "M x N", "nwin", "laps", "beats/matrix t0..t3",
                                             "R", "R one"))
    for p in plan:
        print("  %-4s %11s %5d %5d %28s %6d %6d%s"
              % (p["name"], "%dx%d" % (p["M"], p["N"]), p["nwin"], p["q"],
                 "/".join(str(b) for b in p["b1"]), p["R"], p["Rs"],
                 "  (pad %d rows)" % p["pad"] if p["pad"] else ""))
    fitQ = [max(1, a.target_beats // (k * FIT_NWIN * pt.FREEZE[MODES[0]])) for k in (4, 2, 1)]
    fitQs = [max(1, a.target_beats // (k * FIT_NWIN * MIX_SUM)) for k in (4, 2, 1)]
    print("  overhead fits at N=%d: coop Q %s laps per tenant, single Q %s laps per quarter"
          % (32 * FIT_NWIN, fitQ, fitQs))
    if a.dry_run:
        print("dry run -- nothing generated, nothing run")
        return

    rig = Rig(a)
    load0 = host_load()
    print("\nhost load %s on %d CPUs%s" % (load0, os.cpu_count() or 1,
                                           "   <-- BUSY: timing is provisional"
                                           if load0 != "" and load0 > 0.5 * (os.cpu_count() or 1)
                                           else ""))

    # ---- launch overhead of each configuration ---------------------------------------
    xs, ys = [], []
    for Q in fitQ:
        beats = rig.prep_coop(FIT_NWIN, Q)
        for _ in range(a.reps):
            xs.append(float(beats[0]))
            ys.append(rig.coop_run(beats, Q)[0])
    ovh_c, slope_c, r2_c, se_c = fit_line(xs, ys)
    print("coop   overhead %.1f +/- %.1f us, slope %.6f us/beat (%.1f MHz), R^2 %.5f"
          % (ovh_c, se_c, slope_c, 1.0 / slope_c, r2_c))
    xs, ys = [], []
    for Q in fitQs:
        beats = rig.prep_single(FIT_NWIN, Q)
        for _ in range(a.reps):
            xs.append(float(beats))
            ys.append(rig.single_run(beats, Q)[0])
    ovh_s, slope_s, r2_s, se_s = fit_line(xs, ys)
    print("single overhead %.1f +/- %.1f us, slope %.6f us/beat (%.1f MHz), R^2 %.5f"
          % (ovh_s, se_s, slope_s, 1.0 / slope_s, r2_s))
    for what, ovh, r2, slope in (("coop", ovh_c, r2_c, slope_c), ("single", ovh_s, r2_s, slope_s)):
        if ovh <= 0:
            raise SystemExit("%s overhead fit %.1f us is not positive -- too noisy" % (what, ovh))
        ratio = 1.0 / slope / a.clock
        if not SLOPE_BAND[0] <= ratio <= SLOPE_BAND[1]:
            raise SystemExit("%s fit: the slope says %.1f MHz against a %.0f MHz clock -- the span "
                             "is not tracking the work (coop: the 2:4 tenant must finish last). "
                             "Nothing written." % (what, 1.0 / slope, a.clock))
        if r2 < 0.99:
            print("  WARNING: %s fit R^2 %.4f < 0.99 -- noisy" % (what, r2))

    # ---- the eleven MIXED matrices -----------------------------------------------------
    rows = []
    for p in plan:
        Q, Qs = p["R"] * p["q"], p["Rs"] * p["q"]
        beats = rig.prep_coop(p["nwin"], Q)
        unions, act = [], [[] for _ in range(4)]
        for _ in range(a.reps):
            u, per = rig.coop_run(beats, Q)
            unions.append(u)
            for k in range(4):
                act[k].append(per[k]["active"])
        sbeats = rig.prep_single(p["nwin"], Qs)
        spans = [rig.single_run(sbeats, Qs)[0] for _ in range(a.reps)]

        lat_c = (statistics.mean(unions) - ovh_c) / p["R"]
        lat_s = (statistics.mean(spans) - ovh_s) / p["Rs"]
        if lat_c <= 0 or lat_s <= 0:
            raise SystemExit("%s: overhead exceeds the measured span" % p["name"])
        ideal_c = p["b1"][0] / a.clock
        ideal_s = p["q"] * p["nwin"] * MIX_SUM / a.clock
        # The ceiling the engine can reach: one idle cycle per lap (the designed lap-boundary
        # settle bubble, measured at 1.016 cycles on x4). An unbiased overhead fit lands
        # within ~1% of it (family 4x4 sweep); a biased one sits below it on EVERY shape.
        ceil_c = float(p["b1"][0]) / (p["b1"][0] + p["q"])
        ceil_s = float(p["q"] * p["nwin"] * MIX_SUM) / (p["q"] * p["nwin"] * MIX_SUM + 4 * p["q"])
        row = dict(
            dimensions="%dx%d" % (p["M"], p["N"]),
            coop_latency_per_matrix_us=round(lat_c, 4),
            single_latency_per_matrix_us=round(lat_s, 4),
            speedup=round(lat_s / lat_c, 4),
            speedup_ideal=round(float(MIX_SUM) / pt.FREEZE[MODES[0]], 4),
            shape=p["name"], M_rows=p["M"], N_cols=p["N"], nwin=p["nwin"],
            laps_per_quarter=p["q"], padding_rows=p["pad"],
            batch_R_coop=p["R"], batch_R_single=p["Rs"])
        for k in range(4):
            row["beats_per_matrix_t%d" % k] = p["b1"][k]
        row.update(
            beats_per_matrix_single=p["q"] * p["nwin"] * MIX_SUM,
            coop_ideal_per_matrix_us=round(ideal_c, 4), single_ideal_per_matrix_us=round(ideal_s, 4),
            coop_occupancy=round(ideal_c / lat_c, 4), single_occupancy=round(ideal_s / lat_s, 4),
            coop_lap_ceiling=round(ceil_c, 4), single_lap_ceiling=round(ceil_s, 4),
            coop_union_mean_us=round(statistics.mean(unions), 3),
            single_span_mean_us=round(statistics.mean(spans), 3),
            coop_spread_pct=round(100.0 * (max(unions) - min(unions)) / min(unions), 3),
            single_spread_pct=round(100.0 * (max(spans) - min(spans)) / min(spans), 3))
        for k in range(4):
            row["active_us_t%d" % k] = round(statistics.mean(act[k]), 3)
        row.update(
            overhead_coop_us=round(ovh_c, 2), overhead_coop_se_us=round(se_c, 2),
            overhead_single_us=round(ovh_s, 2), overhead_single_se_us=round(se_s, 2),
            clock_mhz=a.clock, xclbin=a.xclbin, host_load_1min=host_load(),
            runs_coop=" ".join("%.3f" % x for x in unions),
            runs_single=" ".join("%.3f" % x for x in spans))
        rows.append(row)
        print("  %-4s %11s  coop %9.2f us  one tenant %9.2f us  speedup %.3fx (ideal %.3f)"
              "   occupancy/ceiling %.3f %.3f"
              % (p["name"], row["dimensions"], lat_c, lat_s, lat_s / lat_c, row["speedup_ideal"],
                 ideal_c / lat_c / ceil_c, ideal_s / lat_s / ceil_s))

    # A 2026-09-21 run during the x5 build (host load ~5) sat 4-7% under the ceiling on every
    # shape: the overhead fits came out ~345 us (coop) and ~180 us (single) too low, which
    # inflates every per-matrix latency by (bias / R). Ratios survive; absolutes do not.
    for tag in ("coop", "single"):
        gap = statistics.mean(r["%s_occupancy" % tag] / r["%s_lap_ceiling" % tag] for r in rows)
        if gap < 0.98:
            print("\n  WARNING: %s occupancy averages %.1f%% of the lap ceiling on these shapes -- the "
                  "overhead fit is probably biased low (busy host? launch timing varies). Ratios hold; "
                  "rerun on an idle host before charting absolute latency." % (tag, 100.0 * gap))

    # ---- power, and energy per matrix ------------------------------------------------
    if a.soak > 0:
        M, N = SHAPE[SOAK_SHAPE]
        nwin, q = N // 32, quarter_laps(M)
        Rk = ceildiv(SOAK_DEPTH * a.target_beats, q * nwin * pt.FREEZE[MODES[0]])
        beats = rig.prep_coop(nwin, Rk * q)
        pc = soak(rig, a, [(k, rig.tdir[k]) for k in LAUNCH_ORDER], dict(enumerate(beats)),
                  True, "coop, four tenants")
        Rs = ceildiv(SOAK_DEPTH * a.target_beats, q * nwin * MIX_SUM)
        sb = rig.prep_single(nwin, Rs * q)
        ps = soak(rig, a, [(0, rig.fdir)], {0: sb}, False, "one tenant, whole matrix")
        for r in rows:
            el = float(r["M_rows"] * r["N_cols"])
            for tag, pw in (("coop", pc), ("single", ps)):
                lat = r["%s_latency_per_matrix_us" % tag]
                if "board_load_W" in pw:
                    st, dy = pw["board_idle_W"] * lat, (pw["board_load_W"] - pw["board_idle_W"]) * lat
                    r.update({"%s_energy_static_uJ" % tag: round(st, 3),
                              "%s_energy_dynamic_uJ" % tag: round(dy, 3),
                              "%s_energy_pJ_per_element" % tag: round((st + dy) / el * 1e6, 3)})
                for key, v in pw.items():
                    r["%s_soak_%s" % (tag, key)] = v

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


if __name__ == "__main__":
    main()

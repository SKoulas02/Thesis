"""Run the workload on ONE architecture WITH THE NATIVE-XRT HOST (host_workload_xrt.cpp).

A COPY OF run_workload.py (2026-09-29), edited; run_workload.py (the OpenCL host) is untouched.
What differs, and only this:
  * the host is ./host_workload_xrt (native XRT, pre-built runs, one polling thread -- the
    lowest host overhead per calculation), built with -std=c++17 -lxrt_coreutil by
    run_all_workloads_xrt.sh;
  * EVERY build type from one runner: the 13 builds and the shared builds (plan_workload.py)
    and the broadcast builds (plan_workload_bcast.py); the host gets --lockstep /
    --shared-vector / --broadcast from the plan;
  * the CSVs are named workload_xrt_[permatrix_|moe_]<arch>_<MHz>MHz[_calcs].csv, so they never
    overwrite or mix with the OpenCL results;
  * the CALC times are HOST times (steady_clock) -- native XRT has no profiling events -- so
    "active" = from just after the last input mover was started to when polling saw the
    last mover complete; the columns keep their names;
  * --xclbin / --host-timeout (as run_workload_bcast.py) for a hw_emu run.
Everything else -- the plans, stimulus, gates, soak, power and CSV columns -- is
run_workload.py's, line for line. The text below is run_workload.py's.

Run the workload on ONE architecture: the correctness gate, or the measurement.

    cd ~/GEMV_Sparse/Vitis_multi_8x4_x3            # the build folder: the .xclbin is here
    python3 <tools>/run_workload.py --arch 3x8x4 --correctness
    python3 <tools>/run_workload.py --arch 3x8x4

Needs host_workload built for the shape in this folder (run_all_workloads.sh does that),
power_scraper.py in this folder, and the XRT environment. Python 3.6, stdlib only.

CORRECTNESS (--correctness). What is new in host_workload is that a tenant runs SEVERAL
calculations in a row, switching its movers between their buffers. That is proven first:
every tenant gets three small calculations with golden outputs (different widths, the
sparsity changing lap by lap, a different seed per tenant and per calculation), the host
runs them all, and every one must be bit-exact.

MEASUREMENT. The architecture's plan (plan_workload.py: which matrices each tenant runs,
chained into one calculation per width) is turned into timing stimulus, all of it loaded
to the card, then one host call runs --passes timed passes and a --soak of whole passes
in lockstep while the card's power is sampled. Reported per architecture, all MEASURED:

  wall-clock time per pass      over the soak: seconds / passes (launches included)
  energy per pass               board power over the soak x wall time per pass, split into
                                static (idle power) and dynamic (load - idle)
  active time                   per tenant, the sum of its calculations' active times (last
                                input mover started -> output mover done); the slowest
                                tenant's, mean of the timed passes. An UPPER bound on engine
                                time: every calculation still carries a fixed completion cost
  finish balance                per pass, the earliest tenant's finish / the latest's
  padding, planned balance      from the plan

NO ENGINE-ONLY ("steady-state") TIME IS DERIVED HERE. The first version fitted
active = a + (beats + b x laps) / f to these calculations and summed (active - a). On the
card (2026-09-25) the fit failed on 10 of 13 architectures -- a per-calculation fixed cost of
60-370 us, noisy by +-17-70 us, against only seven calculation sizes -- and with b pinned
the sum reduced to the prediction itself, i.e. it measured nothing. The engine-limited time
is derived in scripts/analysis/make_workload_csv.py from each build's efficiency measured in
the earlier campaigns (GEMV_Configurations.csv pct_of_peak).

CSV: workload_<arch>_<MHz>MHz.csv (one summary row) and ..._calcs.csv (every calculation of
every timed pass).

--vectors per-matrix (added 2026-09-28): the plan's per-matrix mode -- every matrix its own
calculation with its own input vector (the professor's multi-user inference case). Same
matrices, same checks, same measurement; the CSVs are named workload_permatrix_<arch>_...
so they can never be mixed up with the shared-vector run.

--vectors moe (added 2026-09-28, the professor's mixture-of-experts case): one token through
7 layers, one per width; a layer holds 12 MIXED experts of every shape of that width, split
over the engines so the layer ends as early as possible and STACKED per engine into one
calculation; the engines wait for each other after every layer (host --lockstep). On a
shared-vector build (plan_workload.SHARED_ARCHS) the host also gets --shared-vector: ONE copy
of each layer's vector, in HBM[0..1], read by every engine. CSVs workload_moe_<arch>_...
Its correctness gate is its own: per test layer ONE tall matrix -- every engine's stack of
small experts, split by the same rule -- with ONE vector and its golden, cut at lap
boundaries into one stack per engine (the method of multi_tenant/prep_shared.py); the cut is
proven byte for byte before the card runs, and every engine must then be bit-exact.
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
import plan_workload as pw                              # noqa: E402
import plan_workload_bcast as pwb                       # noqa: E402  (the broadcast builds)


def plan_for(arch, vectors, clock_override=None):
    """The plan of any build: a broadcast build from plan_workload_bcast.py, every other one
    from plan_workload.py (its clocks are the recorded ones)."""
    if arch in pwb.BCAST_NAMES:
        return pwb.plan(arch, vectors, clock_override=clock_override)
    p = pw.plan(arch, vectors)
    p.setdefault("broadcast", False)
    p.setdefault("shared_vector", False)
    return p

PC_BYTES = 32
# correctness: per tenant, three calculations -- (activation windows, sparsity per lap)
CORRECT_CALCS = [(2, "00,11,01"), (4, "10,10,00,11"), (1, "11,01,00")]
# MoE correctness: three layers -- (activation windows, laps per quarter of each expert).
# 12 small MIXED experts per layer (q laps each of 2:4, 2:8, 2:16, 2:32), split over the
# engines by the SAME rule as the plan and stacked per engine; the middle layer mixes two
# expert sizes, as a two-shape width does, so the stacks come out uneven.
MOE_CORRECT_LAYERS = [(2, [1] * 12), (4, [1, 2] * 6), (1, [1] * 12)]
STEM_PREFIX = {"shared": "xrt_", "per-matrix": "xrt_permatrix_", "moe": "xrt_moe_"}

RE_CALC = re.compile(r"^CALC pass=(\d+) tenant=(\w+) calc=(\d+) beats=(\d+) laps=(\d+) "
                     r"start_us=([\d.]+) active_us=([\d.]+) end_us=([\d.]+)\s*$", re.M)
RE_TENANT = re.compile(r"^TENANT pass=(\d+) tenant=(\w+) calcs=(\d+) busy_us=([\d.]+) "
                       r"active_us=([\d.]+) first_start_us=([\d.]+) last_end_us=([\d.]+)\s*$",
                       re.M)
RE_PASS = re.compile(r"^PASS pass=(\d+) makespan_us=([\d.]+) wall_us=([\d.]+) macs=(\d+)\s*$",
                     re.M)
RE_SOAK = re.compile(r"^SOAK passes=(\d+) seconds=([\d.]+)\s*$", re.M)
RE_SOAK_START = re.compile(r"SOAK_START_EPOCH\s+([0-9.]+)")
RE_SOAK_END = re.compile(r"SOAK_END_EPOCH\s+([0-9.]+)")


def run(cmd, cwd=None, what=None):
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         universal_newlines=True)
    out = p.communicate()[0]
    if p.returncode != 0:
        sys.stdout.write(out[-3000:])
        raise SystemExit("%s failed (exit %d)" % (what or cmd[0], p.returncode))
    return out


def run_host(cmd, timeout):
    """The host, with a timeout: a hung calculation must not hang the whole chain."""
    print("   $ " + " ".join(cmd)[:300])
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         universal_newlines=True)
    try:
        out = p.communicate(timeout=timeout)[0]
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        raise SystemExit("the host did not finish within %d s -- it is probably hung. Reset "
                         "the card before anything else: xbutil reset --device <BDF>" % timeout)
    if p.returncode != 0:
        sys.stdout.write(out[-3000:])
        raise SystemExit("host failed (exit %d)" % p.returncode)
    return out


def data_clk(xclbin):
    out = run(["xclbinutil", "--info", "--input", xclbin], what="xclbinutil")
    m = re.search(r"DATA_CLK.*?Frequency:\s*(\d+)\s*MHz", out, re.S)
    if not m:
        raise SystemExit("no DATA_CLK in xclbinutil --info of %s" % xclbin)
    return int(m.group(1))


def set_packer(emu, cores, blocks):
    """hex_to_bin.py's per-shape constants, set for this engine (correctness only)."""
    T = cores * blocks
    want = dict(W_PCS=pw.ceildiv(32 * T, 256), IND_PCS=pw.ceildiv(10 * T + 2, 256),
                C_PCS=pw.ceildiv(16 * T, 256), LANES=T)
    path = os.path.join(emu, "hex_to_bin.py")
    text = io.open(path, encoding="utf-8").read()
    for name, v in want.items():
        text, n = re.subn(r"^%s *= *\d+" % name, "%s = %d" % (name, v), text, flags=re.M)
        if n != 1:
            raise SystemExit("cannot find %s in %s" % (name, path))
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print("packer constants -> %dx%d: %s" % (cores, blocks,
          ", ".join("%s %d" % kv for kv in sorted(want.items()))))


def tenant_key(k, tenants):
    return "s" if tenants == 1 else str(k)


def tenant_index(tag):
    return 0 if tag == "s" else int(tag[1:])


def compare(d):
    try:
        os.remove(os.path.join(d, "tlast.txt"))
    except OSError:
        pass
    out = subprocess.Popen([sys.executable, "compare_gemv4_py36.py"], cwd=d,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True).communicate()[0]
    line = [ln for ln in out.split("\n") if ln.startswith("compared")]
    return ("=== PASS ===" in out), (line[0] if line else out.strip()[-200:])


def host_flags(p):
    """MoE: every engine waits for the others after each layer; a shared-vector build also
    reads ONE vector copy. Other modes: none (the 2026-09-25 host behaviour)."""
    if p["vectors"] != "moe":
        return []
    if p.get("broadcast"):
        return ["--broadcast"]                  # implies --lockstep --shared-vector
    return ["--lockstep"] + (["--shared-vector"] if p["shared_vector"] else [])


def sp_location(lanes):
    """(index PC, byte in each beat) of the 2-bit sparsity code: bit 10 x lanes of the
    joined index word, as the engine and the host read it."""
    bits = 10 * lanes
    return bits // 256, (bits % 256) // 8


def lap_walk(ind_img, nwin, sp_byte):
    """The host's lap walk (host_workload.cpp load_calc) in Python -> [code, ...]."""
    nbeats = len(ind_img) // PC_BYTES
    codes, pos = [], 0
    while pos < nbeats:
        code = "{:02b}".format(ind_img[pos * PC_BYTES + sp_byte] & 0x3)
        nb = nwin * pw.FREEZE[code]
        if pos + nb > nbeats:
            raise SystemExit("lap walk: lap %d needs %d beats, %d remain" % (len(codes), nb,
                                                                           nbeats - pos))
        for k in range(pos, pos + nb):
            if "{:02b}".format(ind_img[k * PC_BYTES + sp_byte] & 0x3) != code:
                raise SystemExit("lap walk: sparsity changes mid-lap at beat %d" % k)
        codes.append(code)
        pos += nb
    return codes


def read_bytes(path):
    with io.open(path, "rb") as f:
        return f.read()


def read_lines(path):
    with io.open(path, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip()]


# ---------------------------------------------------------------------------
def correctness(a, p, clk):
    """Three small golden-checked calculations per tenant, all run by host_workload."""
    if p["vectors"] == "moe":
        return correctness_moe(a, p, clk)
    set_packer(a.emu, p["cores"], p["blocks"])
    base = os.path.abspath("wl_correct")
    if os.path.isdir(base):
        shutil.rmtree(base)
    specs = []
    for k in range(p["tenants"]):
        dirs = []
        for j, (nwin, codes) in enumerate(CORRECT_CALCS):
            d = os.path.join(base, "t%d" % k, "c%d" % j)
            os.makedirs(os.path.join(d, "bin"))
            run([sys.executable, "gemv4_cosim_gen.py", "--cores", str(p["cores"]),
                 "--blocks", str(p["blocks"]), "--nwin", str(nwin), "--sparsities", codes,
                 "--seed", str(1000 + 97 * k + j)], cwd=a.emu, what="gemv4_cosim_gen")
            run([sys.executable, "hex_to_bin.py", "pack"], cwd=a.emu, what="hex_to_bin")
            for f in os.listdir(os.path.join(a.emu, "bin")):
                if f.endswith(".bin") and not f.startswith("c_pc"):
                    shutil.copy2(os.path.join(a.emu, "bin", f), os.path.join(d, "bin"))
            shutil.copy2(os.path.join(a.emu, "golden.txt"), d)
            shutil.copy2(os.path.join(a.emu, "compare_gemv4_py36.py"), d)
            dirs.append(os.path.relpath(d))
        specs.append("%s:%s" % (tenant_key(k, p["tenants"]), ",".join(dirs)))
    print("correctness: %d tenant(s) x %d calculations, widths %s, sparsity changing lap by lap"
          % (p["tenants"], len(CORRECT_CALCS), "/".join(str(32 * n) for n, _ in CORRECT_CALCS)))
    out = run_host([a.host, p["xclbin"], str(clk), "1", "0"] + specs,
                   timeout=a.host_timeout or 300)
    fails = 0
    for k in range(p["tenants"]):
        for j in range(len(CORRECT_CALCS)):
            ok, line = compare(os.path.join(base, "t%d" % k, "c%d" % j))
            print("  tenant %d calc %d: %s   %s" % (k, j, "PASS" if ok else "FAIL", line))
            fails += 0 if ok else 1
    if fails:
        raise SystemExit("%d calculation(s) not bit-exact -- stopping before any measurement"
                         % fails)
    if len(RE_CALC.findall(out)) != p["tenants"] * len(CORRECT_CALCS):
        raise SystemExit("the host did not report every calculation")
    print("CORRECTNESS PASSED: every tenant bit-exact on all %d of its calculations"
          % len(CORRECT_CALCS))


def correctness_moe(a, p, clk):
    """MoE gate: per layer ONE tall matrix -- every engine's stack of experts, each MIXED,
    one after the other -- with ONE vector and its golden, cut into one stack per engine
    (the experts split by plan_workload.moe_split, as in the measurement). Proven before the
    card runs: the pieces reassemble every image byte for byte, each piece's lap walk is
    exactly its stack's, every engine has the identical vector, the golden blocks reassemble
    the golden. Then every engine must be bit-exact -- on a shared-vector build while reading
    the ONE vector copy in HBM[0..1]."""
    set_packer(a.emu, p["cores"], p["blocks"])
    n, lanes = p["tenants"], p["lanes"]
    W, IND, A, C = p["channels_per_tenant"]
    sp_pc, sp_byte = sp_location(lanes)
    wf = ["weights_pc%d.bin" % i for i in range(W)]
    indf = ["ind_pc%d.bin" % i for i in range(IND)]
    af = ["act_pc%d.bin" % i for i in range(A)]
    base = os.path.abspath("wl_correct")
    if os.path.isdir(base):
        shutil.rmtree(base)
    dirs = dict((k, []) for k in range(n))
    for j, (nwin, qs) in enumerate(MOE_CORRECT_LAYERS):
        experts = [pw.moe_expert(4 * q * lanes, 32 * nwin, lanes, q=q) for q in qs]
        share, _ = pw.moe_split(experts, n)
        if any(not s for s in share):
            raise SystemExit("MoE gate: more engines than test experts")
        codes = [pw.stack_codes(experts, share[k]) for k in range(n)]   # per engine, lap order
        eb = [sum(experts[i]["beats"] for i in share[k]) for k in range(n)]
        rows = [sum(experts[i]["laps"] for i in share[k]) * lanes for k in range(n)]
        run([sys.executable, "gemv4_cosim_gen.py", "--cores", str(p["cores"]),
             "--blocks", str(p["blocks"]), "--nwin", str(nwin),
             "--sparsities", ",".join(sum(codes, [])), "--seed", str(3000 + j)],
            cwd=a.emu, what="gemv4_cosim_gen")
        run([sys.executable, "hex_to_bin.py", "pack"], cwd=a.emu, what="hex_to_bin")
        full = dict((f, read_bytes(os.path.join(a.emu, "bin", f))) for f in wf + indf + af)
        golden = read_lines(os.path.join(a.emu, "golden.txt"))
        bad = []
        for f in wf + indf:
            if len(full[f]) != sum(eb) * PC_BYTES:
                bad.append("%s is %d beats, expected %d" % (f, len(full[f]) // PC_BYTES, sum(eb)))
        for f in af:
            if len(full[f]) != nwin * PC_BYTES:
                bad.append("%s is %d beats, expected %d" % (f, len(full[f]) // PC_BYTES, nwin))
        if len(golden) != sum(rows):
            bad.append("golden.txt has %d rows, expected %d" % (len(golden), sum(rows)))
        if bad:
            raise SystemExit("MoE layer %d: generated stimulus is not what was asked for: %s"
                             % (j, "; ".join(bad)))
        # ---- the cut: engine k = its stack's beats and rows, engines in order ------------
        for k in range(n):
            d = os.path.join(base, "t%d" % k, "c%d" % j)
            os.makedirs(os.path.join(d, "bin"))
            lo, hi = sum(eb[:k]) * PC_BYTES, sum(eb[:k + 1]) * PC_BYTES
            for f in wf + indf:
                with io.open(os.path.join(d, "bin", f), "wb") as fh:
                    fh.write(full[f][lo:hi])
            for f in af:
                with io.open(os.path.join(d, "bin", f), "wb") as fh:
                    fh.write(full[f])                               # the SAME vector
            with io.open(os.path.join(d, "golden.txt"), "w", encoding="utf-8",
                         newline="\n") as fh:
                fh.write("\n".join(golden[sum(rows[:k]):sum(rows[:k + 1])]) + "\n")
            shutil.copy2(os.path.join(a.emu, "compare_gemv4_py36.py"), d)
            dirs[k].append(os.path.relpath(d))
        # ---- prove the cut from what was WRITTEN ----------------------------------------
        for f in wf + indf:
            joined = b"".join(read_bytes(os.path.join(base, "t%d" % k, "c%d" % j, "bin", f))
                              for k in range(n))
            if joined != full[f]:
                bad.append("%s: the experts do not reassemble the matrix" % f)
        for k in range(n):
            tb = os.path.join(base, "t%d" % k, "c%d" % j, "bin")
            walk = lap_walk(read_bytes(os.path.join(tb, indf[sp_pc])), nwin, sp_byte)
            if walk != codes[k]:
                bad.append("engine %d: lap walk finds %d laps %s, expected %d laps %s"
                           % (k, len(walk), walk[:8], len(codes[k]), codes[k][:8]))
            for f in af:
                if read_bytes(os.path.join(tb, f)) != full[f]:
                    bad.append("engine %d: %s is not the shared vector" % (k, f))
        joined = sum((read_lines(os.path.join(base, "t%d" % k, "c%d" % j, "golden.txt"))
                      for k in range(n)), [])
        if joined != golden:
            bad.append("the golden blocks do not reassemble golden.txt")
        if bad:
            raise SystemExit("MoE layer %d: the cut is wrong -- nothing run: %s"
                             % (j, "; ".join(bad)))
    specs = ["%s:%s" % (tenant_key(k, n), ",".join(dirs[k])) for k in range(n)]
    print("MoE correctness: %d engine(s) x %d layers of 12 MIXED experts (q laps of 2:4, 2:8, "
          "2:16, 2:32), split and stacked as in the plan, ONE vector per layer%s; cut proven "
          "byte for byte" % (n, len(MOE_CORRECT_LAYERS),
                             ", read from ONE copy in HBM[0..1]" if p["shared_vector"] else ""))
    out = run_host([a.host] + host_flags(p) + [p["xclbin"], str(clk), "1", "0"] + specs,
                   timeout=a.host_timeout or 300)
    fails = 0
    for k in range(n):
        for j in range(len(MOE_CORRECT_LAYERS)):
            ok, line = compare(os.path.join(base, "t%d" % k, "c%d" % j))
            print("  engine %d layer %d: %s   %s" % (k, j, "PASS" if ok else "FAIL", line))
            fails += 0 if ok else 1
    if fails:
        raise SystemExit("%d stack(s) not bit-exact -- stopping before any measurement" % fails)
    if len(RE_CALC.findall(out)) != n * len(MOE_CORRECT_LAYERS):
        raise SystemExit("the host did not report every calculation")
    print("CORRECTNESS PASSED: every engine bit-exact on all %d layers, the same vector for "
          "every engine%s" % (len(MOE_CORRECT_LAYERS),
                              " (ONE copy in HBM[0..1])" if p["shared_vector"] else ""))


# ---------------------------------------------------------------------------
def generate(a, p):
    """Timing stimulus for every calculation of every tenant -> list of k:dir,... specs."""
    need = sum(c["beats"] * PC_BYTES * (sum(p["channels_per_tenant"][:2]))
               for t in p["tenant_plans"] for c in t["calcs"])
    free = shutil.disk_usage(".").free
    if free < 2 * need:
        raise SystemExit("need ~%.1f GB free for the stimulus, have %.1f GB"
                         % (need / 1e9, free / 1e9))
    base = os.path.abspath("wl")
    if os.path.isdir(base):
        shutil.rmtree(base)
    specs = []
    print("generating %.2f GB of stimulus: %d calculations" %
          (need / 1e9, sum(len(t["calcs"]) for t in p["tenant_plans"])))
    made = {}              # MoE: (nwin, segments) -> the bin/ that already holds that stimulus
    for t in p["tenant_plans"]:
        k = t["tenant"]
        dirs = []
        for j, c in enumerate(t["calcs"]):
            d = os.path.join(base, "t%d" % k, "c%d" % j)
            os.makedirs(os.path.join(d, "bin"))
            key = (c["nwin"], tuple(tuple(s) for s in c["segments"]))
            if p["vectors"] == "moe" and key in made:
                # every engine's expert of a layer has the same shape: copy the stimulus, which
                # also gives every engine the byte-identical vector --shared-vector requires
                for f in os.listdir(made[key]):
                    shutil.copy2(os.path.join(made[key], f), os.path.join(d, "bin", f))
            else:
                cmd = [sys.executable, "gen_timing_stimulus.py", "--cores", str(p["cores"]),
                       "--blocks", str(p["blocks"]), "--nwin", str(c["nwin"])]
                if len(c["segments"]) == 1:
                    cmd += ["--sparsity", c["segments"][0][0], "--nlaps",
                            str(c["segments"][0][1])]
                else:
                    cmd += ["--mix", ",".join("%s:%d" % (s, l) for s, l in c["segments"])]
                run(cmd, cwd=a.emu, what="gen_timing_stimulus")
                for f in os.listdir(os.path.join(a.emu, "bin")):
                    if f.endswith(".bin") and not f.startswith("c_pc"):
                        shutil.move(os.path.join(a.emu, "bin", f), os.path.join(d, "bin", f))
                made[key] = os.path.join(d, "bin")
            wb = os.path.getsize(os.path.join(d, "bin", "weights_pc0.bin")) // PC_BYTES
            ab = os.path.getsize(os.path.join(d, "bin", "act_pc0.bin")) // PC_BYTES
            if wb != c["beats"] or ab != c["nwin"]:
                raise SystemExit("tenant %d calc %d: %d weight / %d activation beats, the plan "
                                 "says %d / %d" % (k, j, wb, ab, c["beats"], c["nwin"]))
            dirs.append(os.path.relpath(d))
        specs.append("%s:%s" % (tenant_key(k, p["tenants"]), ",".join(dirs)))
    return specs


def pass_times(calcs, npasses):
    """Per timed pass: the slowest tenant's summed active time, and the finish balance
    (earliest tenant's last end / latest's). Both straight from the profiling events."""
    active, balance = [], []
    for ps in range(npasses):
        act, end = {}, {}
        for r in calcs:
            if r["pass_"] == ps:
                act[r["tenant"]] = act.get(r["tenant"], 0.0) + r["active_dur_us"]
                end[r["tenant"]] = max(end.get(r["tenant"], 0.0), r["end_us"])
        active.append(max(act.values()))
        balance.append(min(end.values()) / max(end.values()))
    return active, balance


def measure(a, p, clk):
    if a.soak > 0 and not a.allow_no_power:
        try:
            sys.path.insert(0, os.getcwd())
            from power_scraper import read_once
            r = read_once(a.bdf)
            print("power check: board %.2f W, VCCINT %.2f W" % (r["board_w"], r["vccint_w"] or 0))
        except Exception as e:                           # noqa: BLE001
            raise SystemExit("power telemetry does not work (%s) -- source "
                             "/opt/xilinx/xrt/setup.sh, or pass --allow-no-power" % e)
    specs = generate(a, p)
    load = os.getloadavg()[0] if hasattr(os, "getloadavg") else None

    sampler = summarise = None
    if a.soak > 0:
        try:
            from power_scraper import Sampler, summarise
            sampler = Sampler(a.bdf, interval=1.0)
            sampler.start()
        except Exception as e:                           # noqa: BLE001
            if not a.allow_no_power:
                raise SystemExit("cannot start power sampling: %s" % e)
            print("WARNING: no power sampling (%s)" % e)
    out = run_host([a.host] + host_flags(p) + [p["xclbin"], str(clk), str(a.passes),
                                               str(a.soak)] + specs,
                   timeout=a.host_timeout or int(900 + a.soak * 3))

    # ---- parse -----------------------------------------------------------------
    calcs = []
    for m in RE_CALC.finditer(out):
        calcs.append(dict(pass_=int(m.group(1)), tenant=tenant_index(m.group(2)),
                          calc=int(m.group(3)), beats=int(m.group(4)), laps=int(m.group(5)),
                          start_us=float(m.group(6)), active_start_us=float(m.group(7)),
                          end_us=float(m.group(8))))
    passes = [dict(pass_=int(m.group(1)), makespan_us=float(m.group(2)),
                   wall_us=float(m.group(3)), macs=int(m.group(4)))
              for m in RE_PASS.finditer(out)]
    ncalc = sum(len(t["calcs"]) for t in p["tenant_plans"])
    if len(passes) != a.passes or len(calcs) != a.passes * ncalc:
        sys.stdout.write(out[-3000:])
        raise SystemExit("expected %d passes x %d calculations in the host output"
                         % (a.passes, ncalc))
    for r in calcs:
        c = p["tenant_plans"][r["tenant"]]["calcs"][r["calc"]]
        if r["beats"] != c["beats"] or r["laps"] != c["laps"]:
            raise SystemExit("tenant %d calc %d ran %d beats / %d laps, the plan says %d / %d"
                             % (r["tenant"], r["calc"], r["beats"], r["laps"], c["beats"],
                                c["laps"]))
        r["active_dur_us"] = r["end_us"] - r["active_start_us"]
        r["span_us"] = r["end_us"] - r["start_us"]
        r["predicted_us"] = (c["beats"] + pw.LAP_BUBBLE * c["laps"]) / clk
    active, fbal = pass_times(calcs, a.passes)

    row = dict(arch=p["arch"], vectors=p["vectors"], shape=p["shape"], tenants=p["tenants"],
               lanes=p["lanes"],
               clock_mhz=clk, xclbin=p["xclbin"], matrices=p["jobs"],
               useful_macs=p["useful_macs"], padded_macs=p["padded_macs"],
               padding_pct=p["padding_pct"],
               calcs_per_tenant="/".join(str(n) for n in p["calcs_per_tenant"]),
               predicted_us=round(p["predicted_cycles"] / clk, 1),
               active_sum_us=round(statistics.mean(active), 1),
               makespan_us=round(statistics.mean(x["makespan_us"] for x in passes), 1),
               host_wall_us=round(statistics.mean(x["wall_us"] for x in passes), 1),
               finish_balance=round(statistics.mean(fbal), 4),
               balance_planned=p["balance"],
               host_load_1min=round(load, 2) if load is not None else "")
    if p["vectors"] == "moe":
        # lockstep: every layer lasts as long as its slowest engine, so the engine-side time
        # of a token is the sum over layers of the slowest engine's active time
        la = []
        for ps in range(a.passes):
            slowest = {}
            for r in calcs:
                if r["pass_"] == ps:
                    slowest[r["calc"]] = max(slowest.get(r["calc"], 0.0), r["active_dur_us"])
            la.append(sum(slowest.values()))
        row.update(layer_active_sum_us=round(statistics.mean(la), 1), layers=p["layers"],
                   experts_per_token=p["experts_per_token"],
                   experts_per_layer="/".join(str(x) for x in p["experts_per_layer"]),
                   shared_vector="yes" if p["shared_vector"] else "no")

    sm = RE_SOAK.search(out)
    if a.soak > 0 and sm:
        n, secs = int(sm.group(1)), float(sm.group(2))
        t_pass = secs / n * 1e6
        row.update(soak_passes=n, soak_s=round(secs, 2), wall_pass_us=round(t_pass, 1),
                   wall_gmac_s=round(p["useful_macs"] / t_pass / 1e3, 3))
        if sampler is not None:
            t0 = float(RE_SOAK_START.search(out).group(1))
            t1 = float(RE_SOAK_END.search(out).group(1))
            print("idle: sampling %.0f s after the soak..." % a.idle_after)
            time.sleep(a.idle_after + 5.0)
            sampler.stop()
            pwr = summarise(sampler.window(t0 + a.warmup, t1))
            idle = summarise(sampler.window(t1 + 5.0, time.time()))
            if not (pwr and idle):
                raise SystemExit("no power samples inside the soak or the idle window")
            load_w, idle_w = pwr["board_mean"], idle["board_mean"]
            e_static = idle_w * t_pass                    # W x us = uJ
            e_dyn = (load_w - idle_w) * t_pass
            row.update(board_load_W=round(load_w, 3), board_idle_W=round(idle_w, 3),
                       vccint_load_W=round(pwr.get("vccint_mean", 0.0), 3),
                       vccint_idle_W=round(idle.get("vccint_mean", 0.0), 3),
                       energy_wall_uJ=round(e_static + e_dyn, 1),
                       energy_wall_static_uJ=round(e_static, 1),
                       energy_dynamic_uJ=round(e_dyn, 1),
                       nj_per_mac_wall=round((e_static + e_dyn) / p["useful_macs"] * 1e3, 4))
    elif sampler is not None:
        sampler.stop()

    # ---- write -----------------------------------------------------------------
    stem = "workload_%s%s_%dMHz" % (STEM_PREFIX[p["vectors"]], p["arch"], clk)
    with io.open(stem + ".csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()), lineterminator="\n")
        w.writeheader()
        w.writerow(row)
    keys = ["pass_", "tenant", "calc", "beats", "laps", "start_us", "active_start_us", "end_us",
            "span_us", "active_dur_us", "predicted_us"]
    with io.open(stem + "_calcs.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        for r in calcs:
            w.writerow(dict((k, round(v, 3) if isinstance(v, float) else v)
                            for k, v in r.items()))
    if not a.keep:
        shutil.rmtree("wl", ignore_errors=True)

    print("\n=== %s at %d MHz: %d matrices, %.1f M useful MACs, padding %.2f%%"
          % (p["arch"], clk, p["jobs"], p["useful_macs"] / 1e6, p["padding_pct"]))
    print("  predicted engine time   %9.1f us (100%% efficiency)" % row["predicted_us"])
    print("  active time, slowest    %9.1f us (engine + a fixed cost per calculation)"
          % row["active_sum_us"])
    print("  one timed pass          %9.1f us device, %.1f us host" % (row["makespan_us"],
                                                                         row["host_wall_us"]))
    if "wall_pass_us" in row:
        print("  soak: %d passes in %.1f s -> %.1f us per pass, %.2f GMAC/s"
              % (row["soak_passes"], row["soak_s"], row["wall_pass_us"], row["wall_gmac_s"]))
    if "board_load_W" in row:
        print("  power %.2f W load / %.2f W idle -> %.0f uJ per pass (%.3f nJ/MAC)"
              % (row["board_load_W"], row["board_idle_W"], row["energy_wall_uJ"],
                 row["nj_per_mac_wall"]))
    print("  finish balance %.4f (planned balance %.4f)" % (row["finish_balance"],
                                                          row["balance_planned"]))
    print("wrote %s.csv and %s_calcs.csv" % (stem, stem))


def main():
    ap = argparse.ArgumentParser(description="run the workload on one architecture")
    ap.add_argument("--arch", required=True, choices=sorted(set(pw.ARCH) | pwb.BCAST_NAMES),
                    help="one of plan_workload's builds (the *_shared ones: --vectors moe only)")
    ap.add_argument("--emu", default=os.environ.get(
        "EMU", os.path.expanduser("~/GEMV_Sparse/GEMV_4.0_Source/Emulation")))
    ap.add_argument("--host", default="./host_workload_xrt")
    ap.add_argument("--xclbin", default=None,
                    help="run this bitstream instead of the plan's (a hw_emu one); needs --clock")
    ap.add_argument("--host-timeout", type=int, default=None,
                    help="seconds before the host counts as hung (default 300 for a gate, "
                         "900 + 3 x soak for a measurement; hw_emu needs hours)")
    ap.add_argument("--clock", type=int, default=None,
                    help="override the clock (default: the xclbin's DATA_CLK)")
    ap.add_argument("--correctness", action="store_true",
                    help="run the golden-checked gate instead of the measurement")
    ap.add_argument("--passes", type=int, default=3, help="timed passes (default 3)")
    ap.add_argument("--soak", type=float, default=60.0, help="power soak seconds (default 60)")
    ap.add_argument("--bdf", default=os.environ.get("BDF", "0000:af:00.1"))
    ap.add_argument("--warmup", type=float, default=6.0)
    ap.add_argument("--idle-after", type=float, default=20.0)
    ap.add_argument("--allow-no-power", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep the stimulus (wl/) afterwards")
    ap.add_argument("--vectors", default="shared", choices=pw.VECTORS,
                    help="shared: matrices of one width share a calculation and its vector "
                         "(default, the 2026-09-25 run); per-matrix: every matrix its own "
                         "calculation and vector")
    a = ap.parse_args()

    if not os.environ.get("XILINX_XRT"):
        raise SystemExit("XILINX_XRT is not set -- run: source /opt/xilinx/xrt/setup.sh")
    if a.xclbin and not a.clock:
        raise SystemExit("--xclbin needs --clock (the target clock of the build, for hw_emu)")
    p = plan_for(a.arch, a.vectors, clock_override=a.clock)
    if a.xclbin:
        p["xclbin"] = a.xclbin
    if not os.path.exists(p["xclbin"]):
        raise SystemExit("no %s in %s" % (p["xclbin"], os.getcwd()))
    if not os.path.exists(a.host):
        raise SystemExit("no host at %s -- build host_workload_xrt for %dx%d first"
                         % (a.host, p["cores"], p["blocks"]))
    clk = a.clock or data_clk(p["xclbin"])
    if a.clock is None and clk != p["clock_mhz"]:
        raise SystemExit("%s runs at %d MHz but the plan expects %d MHz for %s -- wrong or "
                         "rebuilt xclbin? (--clock overrides)" % (p["xclbin"], clk,
                                                                  p["clock_mhz"], p["arch"]))
    print("%s: %d tenant(s) of %s, %s at %d MHz, vectors %s (%s calculations per tenant)"
          % (p["arch"], p["tenants"], p["shape"], p["xclbin"], clk, p["vectors"],
             "/".join(str(n) for n in p["calcs_per_tenant"])))
    if a.correctness:
        correctness(a, p, clk)
    else:
        measure(a, p, clk)


if __name__ == "__main__":
    main()

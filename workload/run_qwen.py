"""Run the Qwen MoE step-1 test on ONE build: the correctness gate, or the measurement.

    cd ~/GEMV_Sparse/Vitis_multi_x3                        # the build folder: the .xclbin
    python3 <tools>/run_qwen.py --arch 3x4x4 --correctness  # the gate (before the window)
    python3 <tools>/run_qwen.py --arch 3x4x4                # the measurement (in the window)

A NEW FILE (2026-09-29). It imports plan_qwen.py (the plan), gen_qwen_stimulus.py (the data)
and run_workload_xrt.py (its host runner, clock check, packer and compare helpers -- that
file is not changed). The host is host_workload_xrt (native XRT, one polling thread), built
by run_all_qwen.sh; it gets --lockstep, --lockstep --shared-vector, or --broadcast from the
plan. Python 3.6, stdlib only.

THE TEST (plan_qwen.py): per layer the stacked gate + up matrices of 9 experts, 9216 x 2048,
MIXED (2304 rows per sparsity), cut at lap boundaries so every engine has equal cycles
(split B); 40 layers, each with new weights and a NEW VECTOR; the engines in lockstep.

CORRECTNESS (--correctness), before the measurement window. Four layers, all in ONE host
call, run exactly as the measurement runs them (same flags, same cut rule, lockstep, a new
vector per layer, every engine the same vector):
  layers 0-2  GOLDEN, bit-exact. Per layer ONE tall matrix from gemv4_cosim_gen.py (small
              exact-in-bf16 integers, its own seed, so its own vector), with the laps split
              over the engines by plan_qwen.split_laps, cut at lap boundaries into one stack
              per engine. Layer 0 is at the real width, 2048 (64 windows, one lap of every
              sparsity per engine); layers 1 and 2 are narrow with uneven lap counts, so the
              engines' stacks differ and some start at another sparsity. The cut is proven
              byte for byte before anything runs; every engine must then be bit-exact on every
              layer -- which also proves the host fed each layer its OWN vector.
  layer 3     REALISTIC (gen_qwen_stimulus.py, the measurement's data) at width 2048:
              INFORMATIONAL. The engine's accumulator is fixed point with its LSB at 2^-7
              (Accumulator.xci: C_Accum_Lsb = -7, C_Accum_Msb = 25): with integer test data
              that never matters, with weights ~0.02 every summand is quantised to 1/128, so
              the result is compared against several models of that quantisation and the
              match rate of each is printed, with the error against exact arithmetic. It
              fails only on structure: the wrong number of rows, or a NaN/Inf.

MEASUREMENT. 40 layers of realistic data (a new seed per run, from os.urandom, recorded in
the CSV; --seed repeats one), every layer's weights, indices and vector new, all loaded to the
card, then --passes timed passes and a --soak of whole tokens while the board power is
sampled. Reported, all per TOKEN (40 layers):
  predicted_us          ideal engine time: 40 x the busiest engine's cycles / clock
  layer_active_sum_us   engine-side time measured: per layer the slowest engine's active time
                        (last input mover started -> last mover done), summed over the layers.
                        An upper bound on engine time (a fixed cost per calculation remains)
  host_wall_us          one timed pass (token) on the host clock, launches included
  wall_pass_us          wall-clock time per token over the soak; wall_gmac_s from it
  energy                board power over the soak x wall time per token (static + dynamic)
The outputs of the last timed pass are checked for their row counts and for NaN/Inf.
CSV: workload_xrt_qwen_<arch>_<MHz>MHz.csv (one row) and ..._calcs.csv (every calculation).

WEIGHT SIGMA (2026-10-01, the user's decision): the realistic data -- the gate's realistic layer
AND the measurement's 40 layers -- are plain unit-scale random numbers: dense weights N(0, 1)
(--weight-sigma, default 1.0; environment QWEN_WEIGHT_SIGMA overrides it) magnitude-pruned to
2:M, the vector N(0, 1). Big enough for the engine's fixed-point accumulator (LSB 2^-7): the
card's result differs from exact arithmetic only by bf16's own rounding (median ~0.26% of the
rms output). A trained model's weights (~0.02, --weight-sigma 0.02) give ~3-4% median error
from that LSB -- to be tested later with a wider accumulator. Recorded in every CSV; the accuracy
files carry it in their name (..._wsigma<sigma>.csv).
"""

import argparse
import csv
import io
import math
import os
import random
import shutil
import statistics
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plan_qwen as pq                                  # noqa: E402
import gen_qwen_stimulus as gq                          # noqa: E402
import run_workload_xrt as rw                           # noqa: E402  (helpers only)

PC_BYTES = 32
GATE_DIR = "wl_qwen_correct"
MEAS_DIR = "wl_qwen"
GATE_SEED = 5000                   # golden layer j: gemv4_cosim_gen --seed GATE_SEED + j
REAL_GATE_SEED = 6000              # the realistic gate layer


def gate_layers(n):
    """(kind, windows, {code: laps of the whole layer}) of the four gate layers, n engines."""
    return [("golden", 64, {"00": n, "01": n, "10": n, "11": n}),
            ("golden", 2, {"00": n + 1, "01": 2 * n + 1, "10": 1, "11": 3}),
            ("golden", 1, {"00": 1, "01": 1, "10": 2 * n, "11": n}),
            ("realistic", 64, {"00": n, "01": n + 1, "10": n, "11": 2 * n})]


# ---- the engine's arithmetic, as gemv4_cosim_gen.py models it (copied, bit-identical) ----
def _fp32_bits(x):
    return struct.unpack(">I", struct.pack(">f", float(x)))[0]


def _bits_fp32(b):
    return struct.unpack(">f", struct.pack(">I", b & 0xFFFFFFFF))[0]


def bf16_rne(x):
    b = _fp32_bits(x)
    b = (b + 0x7FFF + ((b >> 16) & 1)) & 0xFFFFFFFF
    return _bits_fp32(b & 0xFFFF0000)


def bf16_trunc_raw(x):
    return (_fp32_bits(x) >> 16) & 0xFFFF


def bf16_rne_raw(x):
    b = _fp32_bits(x)
    return ((b + 0x7FFF + ((b >> 16) & 1)) >> 16) & 0xFFFF


def raw_float(raw):
    return _bits_fp32((raw & 0xFFFF) << 16)


def round_half_even(x):
    f = math.floor(x)
    d = x - f
    if d > 0.5 or (d == 0.5 and f % 2):
        return f + 1
    return f


# the accumulator models for the realistic layer: (name, summand -> fixed point, out rounding)
ACC_LSB = 2.0 ** -7
MODELS = [
    ("float, out trunc (the golden model)", None, "trunc"),
    ("fix 2^-7 floor, out trunc", math.floor, "trunc"),
    ("fix 2^-7 toward 0, out trunc", math.trunc, "trunc"),
    ("fix 2^-7 nearest-even, out trunc", round_half_even, "trunc"),
    ("fix 2^-7 floor, out RNE", math.floor, "rne"),
    ("fix 2^-7 toward 0, out RNE", math.trunc, "rne"),
    ("fix 2^-7 nearest-even, out RNE", round_half_even, "rne"),
]


def model_out(adds, quant, rnd):
    if quant is None:
        s = sum(adds)
    else:
        s = sum(quant(x / ACC_LSB) for x in adds) * ACC_LSB
    return bf16_trunc_raw(s) if rnd == "trunc" else bf16_rne_raw(s)


def decode_calc(bindir, lanes, nwin, segments):
    """From the FILES, independently of the generator: per lap, per lane, the list of the
    engine's per-beat sums (w0 x a[i0] + w1 x a[i1], each step bf16 RNE) and the exact
    products' sum. -> [(adds per lane, exact per lane)] per lap."""
    w_pcs = pq.pw.ceildiv(32 * lanes, 256)
    ind_pcs = pq.pw.ceildiv(10 * lanes + 2, 256)
    rd = rw.read_bytes
    w = [rd(os.path.join(bindir, "weights_pc%d.bin" % i)) for i in range(w_pcs)]
    ind = [rd(os.path.join(bindir, "ind_pc%d.bin" % i)) for i in range(ind_pcs)]
    act = [rd(os.path.join(bindir, "act_pc%d.bin" % i)) for i in range(2)]
    vec = []
    for win in range(nwin):
        vec.append([raw_float(struct.unpack_from("<H", act[k // 16], win * 32 + 2 * (k % 16))[0])
                    for k in range(32)])
    laps, beat = [], 0
    for code, n in segments:
        freeze = pq.FREEZE[code]
        for _ in range(n):
            adds = [[] for _ in range(lanes)]
            exact = [0.0] * lanes
            for t in range(nwin * freeze):
                a = vec[t // freeze]
                wb = int.from_bytes(b"".join(x[beat * 32:(beat + 1) * 32] for x in w), "little")
                ib = int.from_bytes(b"".join(x[beat * 32:(beat + 1) * 32] for x in ind), "little")
                for r in range(lanes):
                    w0 = raw_float(wb >> (32 * r))
                    w1 = raw_float(wb >> (32 * r + 16))
                    f = ib >> (10 * r)
                    x0, x1 = a[f & 0x1F], a[(f >> 5) & 0x1F]
                    adds[r].append(bf16_rne(bf16_rne(w0 * x0) + bf16_rne(w1 * x1)))
                    exact[r] += w0 * x0 + w1 * x1
                beat += 1
            laps.append((adds, exact))
    return laps


def read_output(d):
    p = os.path.join(d, "output.txt")
    if not os.path.exists(p):
        return None
    return [int(x, 16) for x in rw.read_lines(p)]


def finite(raw):
    return (raw >> 7) & 0xFF != 0xFF


# ---------------------------------------------------------------------------
def correctness(a, p, clk):
    rw.set_packer(a.emu, p["cores"], p["blocks"])
    n, lanes = p["tenants"], p["lanes"]
    W, IND, A, C = p["channels_per_tenant"]
    sp_pc, sp_byte = rw.sp_location(lanes)
    S = gq.shape(p["cores"], p["blocks"])
    wf = ["weights_pc%d.bin" % i for i in range(W)]
    indf = ["ind_pc%d.bin" % i for i in range(IND)]
    af = ["act_pc%d.bin" % i for i in range(A)]
    base = os.path.abspath(GATE_DIR)
    if os.path.isdir(base):
        shutil.rmtree(base)
    dirs = dict((k, []) for k in range(n))
    layers = gate_layers(n)
    vectors, real, expect = [], {}, {}
    for j, (kind, nwin, lpc) in enumerate(layers):
        segs, _ = pq.split_laps(lpc, nwin, n)
        if any(not s for s in segs):
            raise SystemExit("gate layer %d: an engine gets no lap" % j)
        codes = [pq.segments_codes(s) for s in segs]
        eb = [sum(gq.segment_beats(nwin, s)) for s in segs]
        rows = [len(c) * lanes for c in codes]
        for k in range(n):
            expect[(k, j)] = (eb[k], len(codes[k]))
        tdirs = [os.path.join(base, "t%d" % k, "c%d" % j) for k in range(n)]
        bad = []
        if kind == "golden":
            rw.run([sys.executable, "gemv4_cosim_gen.py", "--cores", str(p["cores"]),
                    "--blocks", str(p["blocks"]), "--nwin", str(nwin),
                    "--sparsities", ",".join(sum(codes, [])), "--seed", str(GATE_SEED + j)],
                   cwd=a.emu, what="gemv4_cosim_gen")
            rw.run([sys.executable, "hex_to_bin.py", "pack"], cwd=a.emu, what="hex_to_bin")
            full = dict((f, rw.read_bytes(os.path.join(a.emu, "bin", f))) for f in wf + indf + af)
            golden = rw.read_lines(os.path.join(a.emu, "golden.txt"))
            for f in wf + indf:
                if len(full[f]) != sum(eb) * PC_BYTES:
                    bad.append("%s is %d beats, expected %d" % (f, len(full[f]) // PC_BYTES,
                                                               sum(eb)))
            for f in af:
                if len(full[f]) != nwin * PC_BYTES:
                    bad.append("%s is %d beats, expected %d" % (f, len(full[f]) // PC_BYTES,
                                                               nwin))
            if len(golden) != sum(rows):
                bad.append("golden.txt has %d rows, expected %d" % (len(golden), sum(rows)))
            if bad:
                raise SystemExit("gate layer %d: generated stimulus is not what was asked for: "
                                 "%s" % (j, "; ".join(bad)))
            # the cut: engine k = its stack's beats and rows, engines in order
            for k in range(n):
                d = tdirs[k]
                os.makedirs(os.path.join(d, "bin"))
                lo, hi = sum(eb[:k]) * PC_BYTES, sum(eb[:k + 1]) * PC_BYTES
                for f in wf + indf:
                    with io.open(os.path.join(d, "bin", f), "wb") as fh:
                        fh.write(full[f][lo:hi])
                for f in af:
                    with io.open(os.path.join(d, "bin", f), "wb") as fh:
                        fh.write(full[f])                           # the SAME vector
                with io.open(os.path.join(d, "golden.txt"), "w", encoding="utf-8",
                             newline="\n") as fh:
                    fh.write("\n".join(golden[sum(rows[:k]):sum(rows[:k + 1])]) + "\n")
                shutil.copy2(os.path.join(a.emu, "compare_gemv4_py36.py"), d)
            # prove the cut from what was WRITTEN
            for f in wf + indf:
                joined = b"".join(rw.read_bytes(os.path.join(tdirs[k], "bin", f))
                                  for k in range(n))
                if joined != full[f]:
                    bad.append("%s: the stacks do not reassemble the matrix" % f)
            joined = sum((rw.read_lines(os.path.join(tdirs[k], "golden.txt"))
                          for k in range(n)), [])
            if joined != golden:
                bad.append("the golden blocks do not reassemble golden.txt")
        else:
            rng = gq.Rng(REAL_GATE_SEED)
            vec, _ = gq.make_vector(nwin, rng)
            pools = {}
            for k in range(n):
                gq.write_calc(os.path.join(tdirs[k], "bin"), S, nwin, segs[k], vec, rng, pools)
                bad += ["engine %d: %s" % (k, x) for x in
                        gq.verify_calc(os.path.join(tdirs[k], "bin"), S, nwin, segs[k], vec)]
            real[j] = (nwin, segs, rows)
        for k in range(n):
            tb = os.path.join(tdirs[k], "bin")
            walk = rw.lap_walk(rw.read_bytes(os.path.join(tb, indf[sp_pc])), nwin, sp_byte)
            if walk != codes[k]:
                bad.append("engine %d: lap walk finds %d laps %s, expected %d laps %s"
                           % (k, len(walk), walk[:8], len(codes[k]), codes[k][:8]))
            for f in af:
                if rw.read_bytes(os.path.join(tb, f)) != rw.read_bytes(os.path.join(tdirs[0],
                                                                                   "bin", f)):
                    bad.append("engine %d: %s is not the layer's vector" % (k, f))
            dirs[k].append(os.path.relpath(tdirs[k]))
        if bad:
            raise SystemExit("gate layer %d: the stimulus is wrong -- nothing run: %s"
                             % (j, "; ".join(bad[:6])))
        vectors.append(rw.read_bytes(os.path.join(tdirs[0], "bin", af[0])))
        print("  layer %d %-9s width %4d  %s" % (
            j, kind, 32 * nwin, " | ".join(
                "e%d: %s" % (k, ",".join("%s x%d" % (pq.SP_NAME[c], l) for c, l in segs[k]))
                for k in range(n))[:150]))
    if len(set(vectors)) != len(vectors):
        raise SystemExit("two gate layers have the same vector -- the per-layer vector would "
                         "not be tested")
    specs = ["%s:%s" % (rw.tenant_key(k, n), ",".join(dirs[k])) for k in range(n)]
    print("Qwen gate: %d engine(s) x %d layers (3 golden, 1 realistic), split B, a new vector "
          "per layer%s; stacks proven before the run" % (
              n, len(layers), {"shared": ", ONE copy in HBM[0..1]", "broadcast":
                               ", ONE broadcast pair"}.get(p["kind"], "")))
    out = rw.run_host([a.host] + p["host_flags"] + [p["xclbin"], str(clk), "1", "0"] + specs,
                      timeout=a.host_timeout or 300)
    got = [(rw.tenant_index(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)))
           for m in rw.RE_CALC.finditer(out)]
    if len(got) != n * len(layers):
        raise SystemExit("the host did not report every calculation")
    for k, j, beats, laps in got:
        if (beats, laps) != expect[(k, j)]:
            raise SystemExit("engine %d layer %d: the host ran %d beats / %d laps, the stack "
                             "has %d / %d" % ((k, j, beats, laps) + expect[(k, j)]))
    fails = 0
    for j, (kind, nwin, lpc) in enumerate(layers):
        if kind != "golden":
            continue
        for k in range(n):
            ok, line = rw.compare(os.path.join(base, "t%d" % k, "c%d" % j))
            print("  engine %d layer %d: %s   %s" % (k, j, "PASS" if ok else "FAIL", line))
            fails += 0 if ok else 1
    if fails:
        raise SystemExit("%d stack(s) not bit-exact -- stopping before any measurement" % fails)
    for j, (nwin, segs, rows) in sorted(real.items()):
        fails += realistic_report(base, j, n, lanes, nwin, segs, rows, p, clk)
    if fails:
        raise SystemExit("the realistic layer came back malformed -- stopping")
    print("CORRECTNESS PASSED: every engine bit-exact on the %d golden layers (the real width "
          "2048 included), a new vector every layer; the realistic layer well-formed"
          % sum(1 for x in layers if x[0] == "golden"))


def realistic_report(base, j, n, lanes, nwin, segs, rows, p, clk):
    """The realistic layer: structure is pass/fail, the numbers are reported AND RECORDED --
    every row's offset from the exact value (card - exact) in qwen_accuracy_<arch>_<MHz>MHz
    _rows.csv, the summary in qwen_accuracy_<arch>_<MHz>MHz.csv. -> failures."""
    counts = [0] * len(MODELS)
    total, errs, scale, fails = 0, [], [], 0
    recs = []
    for k in range(n):
        d = os.path.join(base, "t%d" % k, "c%d" % j)
        hw = read_output(d)
        if hw is None or len(hw) != rows[k]:
            print("  engine %d layer %d (realistic): FAIL -- %s rows, expected %d"
                  % (k, j, "no output.txt, no" if hw is None else len(hw), rows[k]))
            fails += 1
            continue
        if not all(finite(x) for x in hw):
            print("  engine %d layer %d (realistic): FAIL -- NaN/Inf in the output" % (k, j))
            fails += 1
            continue
        laps = decode_calc(os.path.join(d, "bin"), lanes, nwin, segs[k])
        lap_codes = pq.segments_codes(segs[k])
        for li, (adds, exact) in enumerate(laps):
            for r in range(lanes):
                h = hw[li * lanes + r]
                hit = [model_out(adds[r], quant, rnd) == h for _, quant, rnd in MODELS]
                for mi in range(len(MODELS)):
                    counts[mi] += hit[mi]
                off = raw_float(h) - exact[r]
                errs.append(abs(off))
                scale.append(exact[r] * exact[r])
                total += 1
                recs.append(dict(engine=k, lap=li, lane=r, row=li * lanes + r,
                                 sparsity=pq.SP_NAME[lap_codes[li]], terms=2 * len(adds[r]),
                                 card_hex="%04X" % h, card=raw_float(h), exact=exact[r],
                                 offset=off,
                                 offset_rel_exact=(off / abs(exact[r]) if exact[r] else ""),
                                 model_toward0_trunc=int(hit[2])))
    if fails:
        return fails
    rms = math.sqrt(sum(scale) / len(scale)) or 1.0
    errs.sort()
    print("  layer %d (realistic, weights N(0, %g), %d rows, informational): the card vs models "
          "of the engine's arithmetic" % (j, gq.weight_sigma(), total))
    for (name, _, _), c in zip(MODELS, counts):
        print("      %-38s %6.2f%% of rows bit-identical" % (name, 100.0 * c / total))
    print("      error vs exact arithmetic, relative to the rms output %.4f: median %.2f%%, "
          "p95 %.2f%%, max %.2f%%" % (rms, 100 * errs[len(errs) // 2] / rms,
                                      100 * errs[int(0.95 * (len(errs) - 1))] / rms,
                                      100 * errs[-1] / rms))
    for x in recs:
        x["offset_rel_rms"] = x["offset"] / rms
    offs = [x["offset"] for x in recs]
    summ = dict(arch=p["arch"], kind=p["kind"], clock_mhz=clk, layer_width=32 * nwin,
                weight_sigma=gq.weight_sigma(), rows=total, rms_exact=rms,
                mean_offset=statistics.mean(offs),
                mean_offset_rel_rms=statistics.mean(offs) / rms,
                mean_abs_offset=statistics.mean(errs),
                median_abs_offset_rel_rms=errs[len(errs) // 2] / rms,
                p95_abs_offset_rel_rms=errs[int(0.95 * (len(errs) - 1))] / rms,
                max_abs_offset_rel_rms=errs[-1] / rms)
    for (name, _, _), c in zip(MODELS, counts):
        summ["match_" + name.split(" (")[0].replace("fix 2^-7 ", "fix7_").replace(", out ", "_out_")
             .replace(" ", "_").replace(",", "")] = round(100.0 * c / total, 2)
    stem = "qwen_accuracy_%s_%dMHz_wsigma%g" % (p["arch"], clk, gq.weight_sigma())
    rnd = lambda v: round(v, 8) if isinstance(v, float) else v              # noqa: E731
    with io.open(stem + ".csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summ.keys()), lineterminator="\n")
        w.writeheader()
        w.writerow(dict((k, rnd(v)) for k, v in summ.items()))
    keys = ["engine", "lap", "lane", "row", "sparsity", "terms", "card_hex", "card", "exact",
            "offset", "offset_rel_rms", "offset_rel_exact",
            "model_toward0_trunc"]
    with io.open(stem + "_rows.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys, lineterminator="\n")
        w.writeheader()
        for x in recs:
            w.writerow(dict((k, rnd(x[k])) for k in keys))
    print("      mean signed offset (card - exact) %+.5f = %+.2f%% of rms; recorded per row in "
          "%s_rows.csv, summary in %s.csv" % (summ["mean_offset"],
                                               100 * summ["mean_offset_rel_rms"], stem, stem))
    return 0


# ---------------------------------------------------------------------------
def generate(a, p, seed):
    """40 layers of realistic data: per layer a new vector, new index pools and every engine
    its own weights and indices. -> [k:dir,...] specs."""
    S = gq.shape(p["cores"], p["blocks"])
    W, IND, A, _ = p["channels_per_tenant"]
    need = sum(c["beats"] * PC_BYTES * (W + IND) for t in p["tenant_plans"] for c in t["calcs"])
    free = shutil.disk_usage(".").free
    if free < 2 * need:
        raise SystemExit("need ~%.1f GB free for the stimulus, have %.1f GB"
                         % (need / 1e9, free / 1e9))
    base = os.path.abspath(MEAS_DIR)
    if os.path.isdir(base):
        shutil.rmtree(base)
    master = gq.Rng(seed)
    print("generating %.2f GB of realistic stimulus, seed %d, weights N(0, %g): %d layers x %d "
          "engine(s)" % (need / 1e9, seed, gq.weight_sigma(), p["layers"], p["tenants"]))
    t0 = time.time()
    dirs = dict((t["tenant"], []) for t in p["tenant_plans"])
    seen = set()
    for li in range(p["layers"]):
        rng = gq.Rng(master.getrandbits(64))
        vec, _ = gq.make_vector(p["nwin"], rng)
        if vec[0] in seen:
            raise SystemExit("layer %d repeats an earlier layer's vector" % li)
        seen.add(vec[0])
        pools = {}
        for t in p["tenant_plans"]:
            c = t["calcs"][li]
            d = os.path.join(base, "t%d" % t["tenant"], "c%d" % li)
            gq.write_calc(os.path.join(d, "bin"), S, c["nwin"], c["segments"], vec, rng, pools)
            bad = gq.verify_calc(os.path.join(d, "bin"), S, c["nwin"], c["segments"], vec,
                                 sample=16, rng=random.Random(li))
            if bad:
                raise SystemExit("layer %d engine %d: %s" % (li, t["tenant"], "; ".join(bad)))
            walk = rw.lap_walk(rw.read_bytes(os.path.join(d, "bin", "ind_pc%d.bin"
                                                          % S["sp_pc"])),
                               c["nwin"], S["sp_byte"])
            if walk != pq.segments_codes(c["segments"]):
                raise SystemExit("layer %d engine %d: the lap walk does not find the plan"
                                 % (li, t["tenant"]))
            dirs[t["tenant"]].append(os.path.relpath(d))
        if li == 0 or (li + 1) % 10 == 0:
            print("  layer %2d done (%.0f s)" % (li + 1, time.time() - t0))
            sys.stdout.flush()
    return ["%s:%s" % (rw.tenant_key(k, p["tenants"]), ",".join(dirs[k])) for k in sorted(dirs)]


def check_outputs(p):
    """The last timed pass's outputs: every calculation's row count, no NaN/Inf."""
    bad = []
    for t in p["tenant_plans"]:
        for li, c in enumerate(t["calcs"]):
            hw = read_output(os.path.join(MEAS_DIR, "t%d" % t["tenant"], "c%d" % li))
            if hw is None or len(hw) != c["rows"]:
                bad.append("engine %d layer %d: %s rows, expected %d"
                           % (t["tenant"], li, "no" if hw is None else len(hw), c["rows"]))
            elif not all(finite(x) for x in hw):
                bad.append("engine %d layer %d: NaN/Inf" % (t["tenant"], li))
    return bad


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
    seed = a.seed if a.seed is not None else int.from_bytes(os.urandom(8), "little")
    specs = generate(a, p, seed)
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
    out = rw.run_host([a.host] + p["host_flags"] + [p["xclbin"], str(clk), str(a.passes),
                                                    str(a.soak)] + specs,
                      timeout=a.host_timeout or int(900 + a.soak * 3))

    calcs = []
    for m in rw.RE_CALC.finditer(out):
        calcs.append(dict(pass_=int(m.group(1)), tenant=rw.tenant_index(m.group(2)),
                          calc=int(m.group(3)), beats=int(m.group(4)), laps=int(m.group(5)),
                          start_us=float(m.group(6)), active_start_us=float(m.group(7)),
                          end_us=float(m.group(8))))
    passes = [dict(pass_=int(m.group(1)), makespan_us=float(m.group(2)),
                   wall_us=float(m.group(3)), macs=int(m.group(4)))
              for m in rw.RE_PASS.finditer(out)]
    ncalc = sum(len(t["calcs"]) for t in p["tenant_plans"])
    if len(passes) != a.passes or len(calcs) != a.passes * ncalc:
        sys.stdout.write(out[-3000:])
        raise SystemExit("expected %d passes x %d calculations in the host output"
                         % (a.passes, ncalc))
    for r in calcs:
        c = p["tenant_plans"][r["tenant"]]["calcs"][r["calc"]]
        if r["beats"] != c["beats"] or r["laps"] != c["laps"]:
            raise SystemExit("engine %d layer %d ran %d beats / %d laps, the plan says %d / %d"
                             % (r["tenant"], r["calc"], r["beats"], r["laps"], c["beats"],
                                c["laps"]))
        r["active_dur_us"] = r["end_us"] - r["active_start_us"]
        r["span_us"] = r["end_us"] - r["start_us"]
        r["predicted_us"] = c["cycles"] / clk
    bad = check_outputs(p)
    if bad:
        raise SystemExit("the outputs of the last timed pass are malformed: %s"
                         % "; ".join(bad[:6]))
    active, fbal = rw.pass_times(calcs, a.passes)
    la = []
    for ps in range(a.passes):
        slowest = {}
        for r in calcs:
            if r["pass_"] == ps:
                slowest[r["calc"]] = max(slowest.get(r["calc"], 0.0), r["active_dur_us"])
        la.append(sum(slowest.values()))

    row = dict(arch=p["arch"], test="qwen", kind=p["kind"], shape=p["shape"],
               tenants=p["tenants"], lanes=p["lanes"], clock_mhz=clk, xclbin=p["xclbin"],
               layers=p["layers"], layer_rows=p["layer_rows"], width=p["width"],
               useful_macs=p["useful_macs"], padding_pct=p["padding_pct"],
               layer_rows_per_engine="/".join(str(t["layer_rows"]) for t in p["tenant_plans"]),
               predicted_us=round(p["predicted_cycles"] / clk, 1),
               predicted_layer_us=round(p["layer_cycles"] / clk, 2),
               layer_active_sum_us=round(statistics.mean(la), 1),
               active_sum_us=round(statistics.mean(active), 1),
               makespan_us=round(statistics.mean(x["makespan_us"] for x in passes), 1),
               host_wall_us=round(statistics.mean(x["wall_us"] for x in passes), 1),
               finish_balance=round(statistics.mean(fbal), 4),
               balance_planned=p["balance"], seed=seed,
               weight_sigma=gq.weight_sigma(),
               host_load_1min=round(load, 2) if load is not None else "")
    sm = rw.RE_SOAK.search(out)
    if a.soak > 0 and sm:
        nps, secs = int(sm.group(1)), float(sm.group(2))
        t_pass = secs / nps * 1e6
        row.update(soak_passes=nps, soak_s=round(secs, 2), wall_pass_us=round(t_pass, 1),
                   wall_gmac_s=round(p["useful_macs"] / t_pass / 1e3, 3))
        if sampler is not None:
            t0 = float(rw.RE_SOAK_START.search(out).group(1))
            t1 = float(rw.RE_SOAK_END.search(out).group(1))
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

    stem = "workload_xrt_qwen_%s_%dMHz" % (p["arch"], clk)
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
        shutil.rmtree(MEAS_DIR, ignore_errors=True)

    print("\n=== %s (%s) at %d MHz: Qwen step 1, %d layers x %d x %d, %.1f M useful MACs "
          "per token, seed %d, weights N(0, %g)" % (p["arch"], p["kind"], clk, p["layers"],
                                                    p["layer_rows"], p["width"],
                                                    p["useful_macs"] / 1e6, seed,
                                                    gq.weight_sigma()))
    print("  ideal engine time       %9.1f us per token (100%% efficiency)" % row["predicted_us"])
    print("  engine time, measured   %9.1f us per token (sum of each layer's slowest engine)"
          % row["layer_active_sum_us"])
    print("  one timed token         %9.1f us host wall" % row["host_wall_us"])
    if "wall_pass_us" in row:
        print("  soak: %d tokens in %.1f s -> %.1f us per token, %.2f GMAC/s"
              % (row["soak_passes"], row["soak_s"], row["wall_pass_us"], row["wall_gmac_s"]))
    if "board_load_W" in row:
        print("  power %.2f W load / %.2f W idle -> %.0f uJ per token (%.3f nJ/MAC)"
              % (row["board_load_W"], row["board_idle_W"], row["energy_wall_uJ"],
                 row["nj_per_mac_wall"]))
    print("  finish balance %.4f (planned %.4f); outputs of the last pass well-formed"
          % (row["finish_balance"], row["balance_planned"]))
    print("wrote %s.csv and %s_calcs.csv" % (stem, stem))


def main():
    ap = argparse.ArgumentParser(description="the Qwen MoE step-1 test on one build")
    ap.add_argument("--arch", required=True, choices=sorted(pq.ARCH))
    ap.add_argument("--emu", default=os.environ.get(
        "EMU", os.path.expanduser("~/GEMV_Sparse/GEMV_4.0_Source/Emulation")))
    ap.add_argument("--host", default="./host_workload_xrt")
    ap.add_argument("--xclbin", default=None,
                    help="run this bitstream instead of the plan's (a hw_emu one); needs --clock")
    ap.add_argument("--host-timeout", type=int, default=None,
                    help="seconds before the host counts as hung (default 300 for the gate, "
                         "900 + 3 x soak for a measurement)")
    ap.add_argument("--clock", type=int, default=None,
                    help="override the clock (default: the xclbin's DATA_CLK)")
    ap.add_argument("--correctness", action="store_true",
                    help="run the gate instead of the measurement")
    ap.add_argument("--seed", type=int, default=None,
                    help="the measurement's data seed (default: a new one from os.urandom)")
    ap.add_argument("--weight-sigma", type=float,
                    default=float(os.environ.get("QWEN_WEIGHT_SIGMA", "1.0")),
                    help="standard deviation of the realistic dense weights before pruning "
                         "(default 1.0, or $QWEN_WEIGHT_SIGMA; 0.02 = a trained model's scale)")
    ap.add_argument("--passes", type=int, default=3, help="timed passes (default 3)")
    ap.add_argument("--soak", type=float, default=60.0, help="power soak seconds (default 60)")
    ap.add_argument("--bdf", default=os.environ.get("BDF", "0000:af:00.1"))
    ap.add_argument("--warmup", type=float, default=6.0)
    ap.add_argument("--idle-after", type=float, default=20.0)
    ap.add_argument("--allow-no-power", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep the stimulus (wl_qwen/) afterwards")
    a = ap.parse_args()

    if not os.environ.get("XILINX_XRT"):
        raise SystemExit("XILINX_XRT is not set -- run: source /opt/xilinx/xrt/setup.sh")
    gq.set_weight_sigma(a.weight_sigma)
    if a.xclbin and not a.clock:
        raise SystemExit("--xclbin needs --clock (the target clock of the build, for hw_emu)")
    p = pq.plan(a.arch, clock_override=a.clock)
    if a.xclbin:
        p["xclbin"] = a.xclbin
    if not os.path.exists(p["xclbin"]):
        raise SystemExit("no %s in %s" % (p["xclbin"], os.getcwd()))
    if not os.path.exists(a.host):
        raise SystemExit("no host at %s -- build host_workload_xrt for %dx%d first"
                         % (a.host, p["cores"], p["blocks"]))
    clk = a.clock or rw.data_clk(p["xclbin"])
    if a.clock is None and clk != p["clock_mhz"]:
        raise SystemExit("%s runs at %d MHz but the plan expects %d MHz for %s -- wrong or "
                         "rebuilt xclbin? (--clock overrides)" % (p["xclbin"], clk,
                                                                  p["clock_mhz"], p["arch"]))
    print("%s: %d engine(s) of %s (%s), %s at %d MHz, host %s; per layer %s rows per engine"
          % (p["arch"], p["tenants"], p["shape"], p["kind"], p["xclbin"], clk,
             " ".join(p["host_flags"]),
             "/".join(str(t["layer_rows"]) for t in p["tenant_plans"])))
    if a.correctness:
        correctness(a, p, clk)
    else:
        measure(a, p, clk)


if __name__ == "__main__":
    main()

"""The MoE plan of the BROADCAST-VECTOR builds (6 x 4x4, 7 x 4x4, 2 x 8x8).

    python workload/plan_workload_bcast.py                     # the broadcast builds, MoE
    python workload/plan_workload_bcast.py --arch 7x4x4_bcast  # one, with its calculations

A COPY OF plan_workload.py (2026-09-28), edited; plan_workload.py is untouched and still
plans every other build. What differs, and only this:
  * BCAST_ARCHS: the three broadcast builds (multi_tenant/make_bcast_build.py). Like the
    shared builds, MoE only and one vector copy in HBM[0..1] -- but the vector reaches the
    engines through ONE broadcast mover pair, not a mover pair per engine (the 32-port limit
    made 6 x 4x4, 7 x 4x4 and 2 x 8x8 impossible any other way). Clock None until built.
  * archs_for() and the summary list ONLY the broadcast builds; ARCH still knows all the
    others, so any build can be planned here for comparison. The MoE plan of a broadcast
    build is made by the SAME plan_moe: the same experts, split and stacking rule.
  * plan(..., clock_override=MHz): a clock for a build whose DATA_CLK is not recorded yet --
    the hw_emu gate runs before the hardware build exists.
  * the plan carries "broadcast": true/false.

Everything below is plan_workload.py's description, still true.

The workload, and how each of the 13 architectures will run it.

THE WORKLOAD (decided 2026-09-25, option (a)): the eleven GEMV shapes of the thesis's
shape sweep (Vitis/run_shapes.py SHAPES) x the four sparsities 2:4, 2:8, 2:16, 2:32 x
COPIES copies = 132 matrices, ~196 M useful multiply-accumulates per pass. The same
matrices for every architecture.

HOW A MATRIX RUNS. A matrix of M rows and N columns at 2:m occupies laps = ceil(M / lanes)
passes of the engine (lanes = cores x blocks rows each; the extra rows of the last lap are
PADDING, computed and discarded), each pass reading N / 32 activation windows, each window
held for 32 / m cycles. So it streams beats = laps x (N / 32) x (32 / m) weight beats, one
per clock, plus one settle cycle per lap:  cycles = beats + b x laps  (b = 1, the measured
lap model gives 1.01-1.08).

ONE CALCULATION = ONE ACTIVATION VECTOR. Matrices of the same width N are chained into one
calculation, a lap at a time, and the sparsity may change from lap to lap; matrices of
different widths need separate calculations. So each tenant runs one calculation per
distinct N in its share, with its matrices grouped by sparsity inside it.

WHICH TENANT RUNS WHICH MATRIX (decided 2026-09-25): static, balanced by predicted time,
before runtime -- longest predicted matrix first, each to the tenant with the least
predicted work so far (ties to the lowest tenant). A matrix's data then lives on that
tenant's HBM channels for the whole run.

CAPACITY. Every matrix of a tenant is resident at once, and each HBM pseudo-channel holds
256 MB. A tenant's weight (and index) channel holds beats x 32 bytes, summed over its
calculations; the single 4x4 engine, with the whole workload on two weight channels, is
the binding case. The plan refuses to write anything that would not fit.

TWO MODES (--vectors):
  shared      (default; the 2026-09-25 run) matrices of one width share one calculation and
              therefore ONE input vector: 7 calculations per tenant, the fewest launches the
              engine allows -- the best case for launch overhead.
  per-matrix  (added 2026-09-28, the professor's multi-user inference case) every matrix is
              its OWN calculation with its OWN vector, as a request from one user would be:
              132 calculations on a single engine, 26-67 per tenant on multi-tenant builds.
              With that many launches the start-up cost is a large part of every tenant's
              time, so the balancing adds OVERHEAD_US_PER_CALC (measured) to every matrix's
              predicted time. The engine work itself is identical in both modes.
  moe         (added 2026-09-28, the professor's mixture-of-experts case; redesigned the
              same day at the user's request) ONE TOKEN THROUGH 7 MoE LAYERS, ONE PER WIDTH
              (512 ... 8192). A layer holds MOE_EXPERTS_PER_SHAPE = 12 experts of EVERY shape
              with that width -- e.g. the width-768 layer has 12 x 768x768 and 12 x 3072x768
              -- each an M x N MIXED matrix (four row quarters at 2:4, 2:8, 2:16, 2:32 of
              ceil(M / (4 x lanes)) laps each -- the family MIXED definition). All experts of
              a layer read the SAME token vector, so they can be stacked: in every layer the
              experts are split over the engines longest first to the least-loaded (the
              layer finishes as early as possible), each engine STACKS its share into ONE
              calculation (one launch), and the engines wait for each other after every layer
              (host --lockstep). 132 experts = 195.6 M MACs per token on EVERY build, the
              same work as the other two modes. Predicted engine time = the sum over layers
              of the busiest engine's cycles. Runs on the 13 builds above AND the shared-
              vector builds (SHARED_ARCHS), where the vector is one copy in HBM[0..1] (host
              --shared-vector).
The shared mode is unchanged by the other modes: same assignment, same calculations.

Python 3.6, stdlib only: it also runs on the server (run_workload.py imports it).
"""

import argparse
import io
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PLAN_DIR = os.path.join(HERE, "plans")

# The thesis's eleven GEMV shapes -- COPIED from Vitis/run_shapes.py SHAPES (the shape sweep
# and the family MIXED charts use exactly these). Keep them identical.
SHAPES = [
    ("G1", 512, 512), ("G2", 768, 768), ("G3", 1024, 1024), ("G4", 3072, 768),
    ("G5", 768, 3072), ("G6", 4096, 1024), ("G7", 1024, 4096), ("G8", 2048, 2048),
    ("G9", 4096, 4096), ("G10", 8192, 2048), ("G11", 2048, 8192),
]
SPARSITIES = ["00", "01", "10", "11"]                  # 2:4, 2:8, 2:16, 2:32
SP_M = {"00": 4, "01": 8, "10": 16, "11": 32}
SP_NAME = {"00": "2:4", "01": "2:8", "10": "2:16", "11": "2:32"}
FREEZE = {c: 32 // SP_M[c] for c in SPARSITIES}       # 8 / 4 / 2 / 1 cycles per window
COPIES = 3
LAP_BUBBLE = 1.0                                       # cycles per lap (lap model b)

PC_BYTES = 32
PC_CAPACITY = 256 * 1024 * 1024                        # one U280 HBM pseudo-channel
MAX_N = 8192                                           # the replay buffer holds 256 windows

SHAPE_OF = {"4x4": (4, 4), "8x4": (8, 4), "16x3": (16, 3), "8x8": (8, 8),
            "4x24": (4, 24), "4x32": (4, 32)}

# The 13 architectures: name, engine shape, tenants, the clock the card runs the bitstream
# at (its DATA_CLK -- used for the printed predictions; the run reads the real one from the
# .xclbin and stops if the two differ, i.e. a wrong or rebuilt .xclbin), the build folder
# under ~/GEMV_Sparse on the server, and the .xclbin in it.
# Single engines have tenants = 1 and compute units WITHOUT the "_t<k>" suffix.
ARCHS = [
    ("4x4", "4x4", 1, 400, "Vitis_4x4", "sparse_4x4_400.xclbin"),
    ("8x4", "8x4", 1, 375, "Vitis_8x4", "sparse_8x4_375.xclbin"),
    ("16x3", "16x3", 1, 350, "Vitis_16x3", "sparse_16x3_350_missed.xclbin"),
    ("8x8", "8x8", 1, 325, "Vitis_325", "gemv_sparse_325.xclbin"),
    ("4x24", "4x24", 1, 300, "Vitis_4x24", "sparse_4x24_300.xclbin"),
    ("4x32", "4x32", 1, 250, "Vitis_4x32", "sparse_4x32_250.xclbin"),
    ("2x4x4", "4x4", 2, 394, "Vitis_multi_x2", "sparse_4x4_x2_400.xclbin"),
    ("3x4x4", "4x4", 3, 399, "Vitis_multi_x3", "sparse_4x4_x3_400.xclbin"),
    ("4x4x4", "4x4", 4, 374, "Vitis_multi_x4", "sparse_4x4_x4_375.xclbin"),
    ("5x4x4", "4x4", 5, 329, "Vitis_multi_x5_split", "sparse_4x4_x5_375_split.xclbin"),
    ("2x8x4", "8x4", 2, 361, "Vitis_multi_8x4_x2", "sparse_8x4_x2_375.xclbin"),
    ("3x8x4", "8x4", 3, 354, "Vitis_multi_8x4_x3", "sparse_8x4_x3_375.xclbin"),
    ("2x16x3", "16x3", 2, 341, "Vitis_multi_16x3_x2", "sparse_16x3_x2_375.xclbin"),
]
# The shared-vector builds (multi_tenant/*_shared, generated 2026-09-28): ONE copy of the
# vector in HBM[0..1], read by every tenant. MoE mode only. The clock is None until the
# build exists: fill in its DATA_CLK (xclbinutil --info) -- a None clock keeps the build
# out of every run, and naming it explicitly is refused by the chain's preflight.
# (6 x 4x4, 7 x 4x4 and 2 x 8x8 shared were planned too, but need 36, 42 and 34 movers and
# the HBM subsystem has 32 kernel ports: 6 x 4x4 failed to link on it, 2026-09-28.)
SHARED_ARCHS = [
    ("3x4x4_shared", "4x4", 3, None, "Vitis_multi_x3_shared", "sparse_4x4_x3_shared_400.xclbin"),
    ("3x8x4_shared", "8x4", 3, None, "Vitis_multi_8x4_x3_shared",
     "sparse_8x4_x3_shared_375.xclbin"),
    ("2x16x3_shared", "16x3", 2, None, "Vitis_multi_16x3_x2_shared",
     "sparse_16x3_x2_shared_375.xclbin"),
]
# The broadcast-vector builds (multi_tenant/make_bcast_build.py, 2026-09-28): the vector ONCE
# in HBM[0..1], read by ONE pair of krnl_mm2s_bcast<N> movers that stream it to all N engines.
# movers = n x (weights + indices + outputs) + 2 -> 26, 30 and 32 of the 32 HBM kernel ports.
# MoE mode only; the clock is None until the build exists (fill in its DATA_CLK).
BCAST_ARCHS = [
    # x3_bcast (added 2026-09-29): the first broadcast build to bring up -- 17 CUs, the
    # broadcast twin of 3x4x4 and 3x4x4_shared; its kernel is krnl_mm2s_bcast3
    # DATA_CLK 393 MHz recorded 2026-09-29 (xclbinutil --info; target 400, twins 399 / 392)
    ("3x4x4_bcast", "4x4", 3, 393, "Vitis_multi_x3_bcast", "sparse_4x4_x3_bcast_400.xclbin"),
    # DATA_CLK 343 MHz recorded 2026-10-01 (target 375; kernel WNS -0.246 -> the clock rule; HBM +0.009)
    ("6x4x4_bcast", "4x4", 6, 343, "Vitis_multi_x6_bcast", "sparse_4x4_x6_bcast_375.xclbin"),
    # DATA_CLK 311 MHz recorded 2026-10-01 (target 375; link done 04:36; 30 ports)
    ("7x4x4_bcast", "4x4", 7, 311, "Vitis_multi_x7_bcast", "sparse_4x4_x7_bcast_375.xclbin"),
    # 2x8x8_bcast: the FIRST link (Vitis_multi_8x8_x2_bcast, 325 MHz, timing met) READS BACK WRONG from
    # the host -- bits 16/17/134/219 of every even 32-byte beat on all 32 banks (hbm_dma_test,
    # 2026-10-01); the computation itself is right. Relinked with --layout writes-last (all 8 write
    # ports on HBM[24..31], as in the single 4x32 that reads back fine). The relink (2026-10-01,
    # restarted after the server reboot) READS BACK CLEAN on all 32 banks (hbm_dma_test). DATA_CLK
    # 305 MHz recorded 2026-10-02 (target 325; kernel WNS -0.198 / 7818 paths -> the clock rule;
    # HBM +0.085; 0 CW; AlternateRoutability) -- the first link's 325 was a different placement.
    ("2x8x8_bcast", "8x8", 2, 305, "Vitis_multi_8x8_x2_bcast_wl",
     "sparse_8x8_x2_bcast_wl_325.xclbin"),
    # the broadcast versions of the shared twins 3x8x4_shared / 2x16x3_shared (added
    # 2026-09-30): the twins' target (375) and floorplan (split); kernels bcast3 / bcast2
    # DATA_CLK 337 MHz recorded 2026-10-01 (target 375; kernel WNS -0.298 / 4088 paths -> the clock
    # rule; HBM +0.089; 0 CW; hbm_dma_test clean)
    ("3x8x4_bcast", "8x4", 3, 337, "Vitis_multi_8x4_x3_bcast",
     "sparse_8x4_x3_bcast_375.xclbin"),
    # DATA_CLK 339 MHz recorded 2026-10-01 (target 375; kernel WNS -0.281 / 3712 paths -> the clock
    # rule; HBM +0.039; 0 CW; hbm_dma_test clean)
    ("2x16x3_bcast", "16x3", 2, 339, "Vitis_multi_16x3_x2_bcast",
     "sparse_16x3_x2_bcast_375.xclbin"),
]
ARCH = dict((a[0], a) for a in ARCHS + SHARED_ARCHS + BCAST_ARCHS)
SHARED_NAMES = set(a[0] for a in SHARED_ARCHS)
BCAST_NAMES = set(a[0] for a in BCAST_ARCHS)

VECTORS = ("shared", "per-matrix", "moe")
MOE_EXPERTS_PER_SHAPE = 12          # every shape puts 12 MIXED experts into its width's layer


def archs_for(vectors):
    """The architectures THIS planner runs: the broadcast builds whose clock has been
    recorded, MoE only (the other builds are run by plan_workload.py)."""
    if vectors == "moe":
        return [a for a in BCAST_ARCHS if a[3] is not None]
    return []


def is_shared(name):
    """One vector copy in HBM[0..1]: the shared builds and the broadcast builds."""
    return name in SHARED_NAMES or name in BCAST_NAMES


def is_bcast(name):
    return name in BCAST_NAMES

# Start-up cost of ONE calculation, measured in the shared-vector run of 2026-09-25:
# (wall time per pass - derived engine time) / 7 calculations, per architecture
# (results/GEMV_Workload.csv overhead_us / 7). Used ONLY to balance the per-matrix mode.
OVERHEAD_US_PER_CALC = {
    "4x4": 237.5, "8x4": 334.2, "16x3": 384.8, "8x8": 492.4, "4x24": 675.6, "4x32": 937.8,
    "2x4x4": 260.1, "3x4x4": 377.1, "4x4x4": 381.9, "5x4x4": 440.4,
    "2x8x4": 389.4, "3x8x4": 585.3, "2x16x3": 530.1,
}


def ceildiv(a, b):
    return -(-a // b)


def workload():
    """-> the 132 matrices: dict(id, shape, M, N, code, copy, macs). Deterministic order."""
    jobs = []
    for name, M, N in SHAPES:
        if N % 32 or N > MAX_N:
            raise SystemExit("%s: N=%d must be a multiple of 32 and <= %d" % (name, N, MAX_N))
        for code in SPARSITIES:
            for c in range(COPIES):
                jobs.append(dict(id=len(jobs), shape=name, M=M, N=N, code=code, copy=c,
                                 macs=M * N * 2 // SP_M[code]))
    return jobs


def channels_of(shape):
    """(weight, index, activation, output) HBM channels of one tenant of this shape."""
    c, b = SHAPE_OF[shape]
    T = c * b
    return ceildiv(32 * T, 256), ceildiv(10 * T + 2, 256), 2, ceildiv(16 * T, 256)


def plan(arch_name, vectors="shared", clock_override=None):
    """-> the full plan of one architecture (a dict, JSON-ready). Raises if it cannot fit.
    clock_override: the clock to plan at when none is recorded (the hw_emu gate)."""
    if vectors not in VECTORS:
        raise SystemExit("vectors must be one of %s" % ", ".join(VECTORS))
    name, shape, tenants, clock, folder, xclbin = ARCH[arch_name]
    if is_shared(name) and vectors != "moe":
        raise SystemExit("%s is a shared-vector build -- MoE mode only" % name)
    if clock_override:
        clock = clock_override
    if clock is None:
        raise SystemExit("%s: no clock recorded yet -- fill in its DATA_CLK in BCAST_ARCHS "
                         "of plan_workload_bcast.py once it is built" % name)
    cores, blocks = SHAPE_OF[shape]
    lanes = cores * blocks
    if vectors == "moe":
        return plan_moe(name, shape, tenants, clock, folder, xclbin, cores, blocks)
    jobs = workload()
    for j in jobs:
        j["laps"] = ceildiv(j["M"], lanes)
        j["nwin"] = j["N"] // 32
        j["beats"] = j["laps"] * j["nwin"] * FREEZE[j["code"]]
        j["cycles"] = j["beats"] + LAP_BUBBLE * j["laps"]
        j["padding_rows"] = j["laps"] * lanes - j["M"]

    # static balance: longest predicted first, to the least-loaded tenant. Shared: the
    # predicted engine cycles. Per-matrix: engine time + one measured start-up per matrix
    # (every matrix is a calculation of its own), in microseconds.
    if vectors == "shared":
        cost = lambda j: j["cycles"]                                   # noqa: E731
    else:
        ovh = OVERHEAD_US_PER_CALC[name]
        cost = lambda j: j["cycles"] / float(clock) + ovh              # noqa: E731
    load = [0.0] * tenants
    share = [[] for _ in range(tenants)]
    for j in sorted(jobs, key=lambda x: (-cost(x), x["id"])):
        k = min(range(tenants), key=lambda t: (load[t], t))
        share[k].append(j)
        load[k] += cost(j)

    ts = []
    for k in range(tenants):
        calcs = []
        if vectors == "shared":
            for N in sorted(set(j["N"] for j in share[k])):
                mine = sorted((j for j in share[k] if j["N"] == N),
                              key=lambda x: (SPARSITIES.index(x["code"]), x["id"]))
                segs = []
                for j in mine:                           # merge consecutive equal codes
                    if segs and segs[-1][0] == j["code"]:
                        segs[-1][1] += j["laps"]
                    else:
                        segs.append([j["code"], j["laps"]])
                calcs.append(dict(nwin=N // 32, segments=segs,
                                  jobs=[j["id"] for j in mine],
                                  laps=sum(j["laps"] for j in mine),
                                  beats=sum(j["beats"] for j in mine),
                                  cycles=sum(j["cycles"] for j in mine),
                                  macs=sum(j["macs"] for j in mine)))
        else:
            # one calculation per matrix, in a fixed order: width, then sparsity, then id
            for j in sorted(share[k], key=lambda x: (x["N"], SPARSITIES.index(x["code"]),
                                                     x["id"])):
                calcs.append(dict(nwin=j["nwin"], segments=[[j["code"], j["laps"]]],
                                  jobs=[j["id"]], laps=j["laps"], beats=j["beats"],
                                  cycles=j["cycles"], macs=j["macs"]))
        beats = sum(c["beats"] for c in calcs)
        fill = beats * PC_BYTES / float(PC_CAPACITY)
        if fill > 1.0:
            raise SystemExit("%s tenant %d: %d weight beats per channel = %.0f%% of a 256 MB "
                             "HBM channel -- the workload does not fit"
                             % (arch_name, k, beats, 100 * fill))
        ts.append(dict(tenant=k, calcs=calcs, jobs=len(share[k]), beats=beats,
                       cycles=sum(c["cycles"] for c in calcs),
                       macs=sum(c["macs"] for c in calcs),
                       channel_fill=round(fill, 4)))
        if vectors == "per-matrix":
            ts[-1]["predicted_total_us"] = round(load[k], 1)

    macs = sum(j["macs"] for j in jobs)
    padded = sum(j["laps"] * lanes * j["N"] * 2 // SP_M[j["code"]] for j in jobs)
    makespan = max(t["cycles"] for t in ts)
    out = dict(arch=name, vectors=vectors, shape=shape, cores=cores, blocks=blocks,
               lanes=lanes, tenants=tenants, clock_mhz=clock, folder=folder, xclbin=xclbin,
               channels_per_tenant=channels_of(shape),
               jobs=len(jobs), useful_macs=macs, padded_macs=padded,
               padding_pct=round(100.0 * (padded - macs) / padded, 3),
               calcs_per_tenant=[len(t["calcs"]) for t in ts],
               predicted_cycles=makespan,
               predicted_us=round(makespan / clock, 1),
               predicted_gmac_s=round(macs / (makespan / clock) / 1e3, 2),
               balance=round(min(load) / max(load), 4),
               max_channel_fill=max(t["channel_fill"] for t in ts),
               tenant_plans=ts)
    if vectors == "per-matrix":
        out["overhead_us_per_calc"] = OVERHEAD_US_PER_CALC[name]
        out["predicted_total_us"] = round(max(load), 1)
    return out


def moe_layers():
    """-> [(width N, [(shape name, M), ...])], ascending width: the shapes of one width share
    one layer and its token vector."""
    return [(N, [(name, M) for name, M, n in SHAPES if n == N])
            for N in sorted(set(n for _, _, n in SHAPES))]


def moe_expert(M, N, lanes, q=None):
    """One M x N MIXED expert: q laps of each of 2:4, 2:8, 2:16, 2:32 (q = ceil(M / (4 lanes))
    unless given -- the correctness gate uses small q directly)."""
    q = ceildiv(M, 4 * lanes) if q is None else q
    nwin = N // 32
    beats = sum(q * nwin * FREEZE[c] for c in SPARSITIES)
    return dict(M=M, N=N, q=q, nwin=nwin, laps=4 * q, beats=beats,
                cycles=beats + LAP_BUBBLE * 4 * q,
                macs=sum(M // 4 * N * 2 // SP_M[c] for c in SPARSITIES),
                padded_macs=sum(q * lanes * N * 2 // SP_M[c] for c in SPARSITIES))


def moe_split(experts, engines):
    """One layer's experts over the engines so the layer ends as early as possible: longest
    first, each to the least-loaded engine (ties: the lowest index). -> ([expert indices per
    engine, ascending], [predicted cycles per engine])."""
    load = [0.0] * engines
    share = [[] for _ in range(engines)]
    for i in sorted(range(len(experts)), key=lambda i: (-experts[i]["cycles"], i)):
        k = min(range(engines), key=lambda t: (load[t], t))
        share[k].append(i)
        load[k] += experts[i]["cycles"]
    return [sorted(s) for s in share], load


def stack_codes(experts, idxs):
    """The lap-by-lap sparsity codes of a stack of experts, in stack order."""
    return [c for i in idxs for c in SPARSITIES for _ in range(experts[i]["q"])]


def stack_segments(experts, idxs):
    """A stack of experts as [code, laps] segments (neighbouring equal codes merged)."""
    segs = []
    for i in idxs:
        for c in SPARSITIES:
            if segs and segs[-1][0] == c:
                segs[-1][1] += experts[i]["q"]
            else:
                segs.append([c, experts[i]["q"]])
    return segs


def plan_moe(name, shape, tenants, clock, folder, xclbin, cores, blocks):
    """MoE: 7 layers (one per width); in each, 12 MIXED experts of every shape of that width,
    split over the engines to finish the layer as early as possible, each engine's share
    STACKED into one calculation. Every tenant runs 7 calculations; lockstep per layer."""
    lanes = cores * blocks
    calcs = [[] for _ in range(tenants)]
    layer_max, layer_mean, per_layer = [], [], []
    macs = padded = 0
    for li, (N, shapes) in enumerate(moe_layers()):
        if N % 32 or N > MAX_N:
            raise SystemExit("width %d must be a multiple of 32 and <= %d" % (N, MAX_N))
        experts = []
        for sname, M in shapes:
            for e in range(MOE_EXPERTS_PER_SHAPE):
                x = moe_expert(M, N, lanes)
                x.update(shape=sname, copy=e, id=len(experts))
                experts.append(x)
        share, load = moe_split(experts, tenants)
        if any(not s for s in share):
            raise SystemExit("%s layer %d: more engines than experts" % (name, li))
        layer_max.append(max(load))
        layer_mean.append(sum(load) / tenants)
        per_layer.append(len(experts))
        macs += sum(x["macs"] for x in experts)
        padded += sum(x["padded_macs"] for x in experts)
        for k in range(tenants):
            mine = [experts[i] for i in share[k]]
            calcs[k].append(dict(
                layer=li, N=N, nwin=N // 32, segments=stack_segments(experts, share[k]),
                experts=len(mine),
                shapes=", ".join("%d x %s" % (sum(1 for x in mine if x["shape"] == s), s)
                                 for s, _ in shapes if any(x["shape"] == s for x in mine)),
                jobs=[x["id"] for x in mine], laps=sum(x["laps"] for x in mine),
                beats=sum(x["beats"] for x in mine), cycles=sum(x["cycles"] for x in mine),
                macs=sum(x["macs"] for x in mine)))
    ts = []
    for k in range(tenants):
        beats = sum(c["beats"] for c in calcs[k])
        fill = beats * PC_BYTES / float(PC_CAPACITY)
        if fill > 1.0:
            raise SystemExit("%s tenant %d: %d weight beats per channel = %.0f%% of a 256 MB "
                             "HBM channel -- the MoE experts do not fit" % (name, k, beats,
                                                                             100 * fill))
        ts.append(dict(tenant=k, calcs=calcs[k], jobs=sum(c["experts"] for c in calcs[k]),
                       beats=beats, cycles=sum(c["cycles"] for c in calcs[k]),
                       macs=sum(c["macs"] for c in calcs[k]), channel_fill=round(fill, 4)))
    cycles = sum(layer_max)                     # lockstep: every layer waits for its slowest
    return dict(arch=name, vectors="moe", shape=shape, cores=cores, blocks=blocks,
                lanes=lanes, tenants=tenants, clock_mhz=clock, folder=folder, xclbin=xclbin,
                channels_per_tenant=channels_of(shape), shared_vector=is_shared(name),
                broadcast=is_bcast(name),
                layers=len(per_layer), experts_per_layer=per_layer,
                experts_per_token=sum(per_layer),
                jobs=sum(per_layer), useful_macs=macs, padded_macs=padded,
                padding_pct=round(100.0 * (padded - macs) / padded, 3),
                calcs_per_tenant=[len(c) for c in calcs],
                predicted_cycles=cycles,
                predicted_us=round(cycles / clock, 1),
                predicted_gmac_s=round(macs / (cycles / clock) / 1e3, 2),
                balance=round(sum(layer_mean) / sum(layer_max), 4),
                max_channel_fill=max(t["channel_fill"] for t in ts),
                tenant_plans=ts)


def write_plan(p):
    if not os.path.isdir(PLAN_DIR):
        os.makedirs(PLAN_DIR)
    path = os.path.join(PLAN_DIR, "%s%s.json" % (p["arch"], {"shared": "", "per-matrix":
                                                             "_permatrix", "moe": "_moe"}[p["vectors"]]))
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(p, indent=1, sort_keys=False))
        f.write("\n")
    return path


def main():
    ap = argparse.ArgumentParser(description="plan the MoE workload on the broadcast builds")
    ap.add_argument("--arch", default=None, choices=sorted(ARCH), help="one architecture only")
    ap.add_argument("--vectors", default="moe", choices=VECTORS,
                    help="moe (the only mode of the broadcast builds; default)")
    ap.add_argument("--clock", type=int, default=None,
                    help="plan at this clock (MHz) -- for a build with no DATA_CLK recorded")
    a = ap.parse_args()

    jobs = workload()
    macs = sum(j["macs"] for j in jobs)
    print("workload: %d shapes x %d sparsities x %d copies = %d matrices, %.1f M useful MACs; "
          "vectors: %s" % (len(SHAPES), len(SPARSITIES), COPIES, len(jobs), macs / 1e6,
                           a.vectors))
    names = [a.arch] if a.arch else [x[0] for x in archs_for(a.vectors)]
    if a.clock and not a.arch:
        names = [x[0] for x in BCAST_ARCHS]              # --clock: every broadcast build
    if a.vectors == "moe":
        waiting = [x[0] for x in BCAST_ARCHS if x[3] is None and not a.clock]
        print("MoE: one token through %d layers (one per width), %d MIXED experts of every "
              "shape, each engine stacking its share of a layer into one calculation"
              % (len(moe_layers()), MOE_EXPERTS_PER_SHAPE))
        if waiting and not a.arch:
            print("not listed until their DATA_CLK is recorded in BCAST_ARCHS (or give --clock "
                  "to plan them anyway): %s" % ", ".join(waiting))
    pm = a.vectors == "per-matrix"
    print("\n%-7s %6s %5s %4s %15s %9s %9s %8s %8s %7s %6s%s"
          % ("arch", "tenant", "lanes", "MHz", "calcs", "pred us", "GMAC/s", "balance",
             "padding", "fill", "jobs", "  with launches" if pm else ""))
    for n in names:
        p = plan(n, a.vectors, clock_override=a.clock)
        if not a.clock:
            write_plan(p)                     # a --clock plan is a preview, not written
        print("%-7s %6d %5d %4d %15s %9.1f %9.2f %7.1f%% %7.2f%% %6.0f%% %6s%s"
              % (p["arch"], p["tenants"], p["lanes"], p["clock_mhz"],
                 "/".join(str(c) for c in p["calcs_per_tenant"]), p["predicted_us"],
                 p["predicted_gmac_s"], 100 * p["balance"], p["padding_pct"],
                 100 * p["max_channel_fill"],
                 "/".join(str(t["jobs"]) for t in p["tenant_plans"]),
                 "  %9.1f" % p["predicted_total_us"] if pm else ""))
        if a.arch:
            for t in p["tenant_plans"]:
                print("  tenant %d: %d matrices, %.0f predicted cycles" % (t["tenant"], t["jobs"],
                                                                           t["cycles"]))
                for c in t["calcs"]:
                    print("    N=%-5d %4d laps %9d beats  %s"
                          % (32 * c["nwin"], c["laps"], c["beats"],
                             ", ".join("%s x %d laps" % (SP_NAME[s], l) for s, l in c["segments"])))
    print("\npred us = predicted engine time for one pass, launch overhead excluded; balance ="
          " least-loaded tenant / most-loaded%s; fill = the fullest HBM channel."
          % (" (engine time + %s per calculation)" % "measured start-up" if pm else ""))
    if pm:
        print("with launches = the slowest tenant's engine time + its calculations x the "
              "start-up measured on 2026-09-25 (an estimate, used only for balancing).")
    print("preview at --clock %d MHz: no plan written" % a.clock if a.clock else
          "plans written to %s" % os.path.relpath(PLAN_DIR))


if __name__ == "__main__":
    main()

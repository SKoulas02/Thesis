"""The Qwen MoE step-1 test, and how every build runs it.

    python workload/plan_qwen.py                      # every build with a recorded clock
    python workload/plan_qwen.py --arch 3x4x4         # one build, with its per-engine split
    python workload/plan_qwen.py --arch 7x4x4_bcast --clock 375   # a build not measured yet

A NEW FILE (2026-09-29). It imports plan_workload.py (the 13 builds and the shared builds) and
plan_workload_bcast.py (the broadcast builds) for their build tables only, and changes
neither. Python 3.6, stdlib only: it also runs on the server (run_qwen.py imports it).

THE MODEL (the user's decisions, 2026-09-29). An example after Qwen3.5-35B-A3B, not an exact
copy of it: hidden size 2048, 40 MoE layers, per token 8 routed experts + 1 shared expert,
each with a gate and an up projection of 512 x 2048. STEP 1 ONLY: the gate and up matrices of
the 9 active experts all read the SAME token vector, so they are stacked into ONE
9216 x 2048 GEMV per layer -- no router, no SiLU, no down projection. 40 layers per token,
each with its own weights AND its own vector (a new token state per layer).

MIXED SPARSITY. Every expert is MIXED: a quarter of its rows at each of 2:4, 2:8, 2:16, 2:32
(the family MIXED definition). Stacked, the layer is 2304 rows of each sparsity, grouped by
sparsity. 2304 = 2^8 x 9 is a multiple of every lane count (16, 32, 48, 64, 96, 128), so no
build pads a single row.

SPLIT B -- ROWS BALANCED BY CYCLES. A layer is cut at lap boundaries (one lap = one engine
pass of `lanes` rows over the whole vector); the laps are dealt one at a time, the costliest
sparsity first, each to the engine with the fewest predicted cycles so far (ties: the lowest
engine). An engine then runs its share as ONE calculation: its 2:4 laps, then its 2:8, 2:16
and 2:32 laps. A lap at 2:m costs (2048/32) x (32/m) weight beats + LAP_BUBBLE cycles. The
engines wait for each other after every layer (host --lockstep), so a token takes
sum over layers of the busiest engine's cycles = 40 x the busiest engine's per-layer cycles.

VECTOR. Every engine of a layer reads the SAME vector: a per-engine copy on the 13 builds,
ONE copy in HBM[0..1] on the shared builds (host --shared-vector), one broadcast mover pair
on the broadcast builds (host --broadcast).

CAPACITY. All 40 layers are resident at once: an engine's weight (and index) channel holds
40 x its per-layer beats x 32 bytes. The single 4x4 is the binding case (177 MB of 256 MB).
The plan refuses anything that would not fit.
"""

import argparse
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plan_workload as pw                              # noqa: E402
import plan_workload_bcast as pwb                       # noqa: E402

PLAN_DIR = os.path.join(HERE, "plans")

# ---- the model --------------------------------------------------------------------------
HIDDEN = 2048                       # N: the token vector
EXPERT_ROWS = 512                   # moe_intermediate_size: rows of one gate (or up) matrix
ACTIVE_EXPERTS = 9                  # 8 routed + 1 shared
MATRICES_PER_EXPERT = 2             # gate and up (step 1 only; down is step 2)
LAYERS = 40
SPARSITIES = pw.SPARSITIES          # 00 01 10 11 = 2:4 2:8 2:16 2:32
SP_M = pw.SP_M
SP_NAME = pw.SP_NAME
FREEZE = pw.FREEZE
LAP_BUBBLE = pw.LAP_BUBBLE
PC_BYTES = pw.PC_BYTES
PC_CAPACITY = pw.PC_CAPACITY
SHAPE_OF = pw.SHAPE_OF

LAYER_ROWS = ACTIVE_EXPERTS * MATRICES_PER_EXPERT * EXPERT_ROWS        # 9216
ROWS_PER_CODE = LAYER_ROWS // len(SPARSITIES)                          # 2304
NWIN = HIDDEN // 32                                                    # 64 windows

# ---- the builds: (name, shape, engines, clock, folder, xclbin), their kind -------------
# The clocks are the recorded DATA_CLKs of plan_workload.py (13 + shared) and
# plan_workload_bcast.py (broadcast); a None clock keeps a build out until it is recorded.
BUILDS = list(pw.ARCHS) + list(pw.SHARED_ARCHS) + list(pwb.BCAST_ARCHS)
ARCH = dict((a[0], a) for a in BUILDS)
SHARED_NAMES = set(a[0] for a in pw.SHARED_ARCHS)
BCAST_NAMES = set(a[0] for a in pwb.BCAST_ARCHS)


def kind(name):
    if name in BCAST_NAMES:
        return "broadcast"
    if name in SHARED_NAMES:
        return "shared"
    return "single" if ARCH[name][2] == 1 else "multi"


def archs():
    """Every build whose clock is recorded, in table order."""
    return [a for a in BUILDS if a[3] is not None]


def host_flags(name):
    """Engines wait for each other after every layer; one vector copy on the shared builds,
    the broadcast pair on the broadcast builds (--broadcast implies both)."""
    k = kind(name)
    if k == "broadcast":
        return ["--broadcast"]
    if k == "shared":
        return ["--lockstep", "--shared-vector"]
    return ["--lockstep"]


def lap_cost(code, nwin):
    return nwin * FREEZE[code] + LAP_BUBBLE


def split_laps(laps_per_code, nwin, engines):
    """Split B: laps dealt one at a time, costliest sparsity first, each to the engine with
    the fewest predicted cycles (ties: the lowest). laps_per_code = {code: laps}.
    -> [[[code, laps], ...] per engine, codes in 2:4 .. 2:32 order, empty codes left out],
       [predicted cycles per engine]."""
    load = [0.0] * engines
    got = [dict((c, 0) for c in SPARSITIES) for _ in range(engines)]
    for c in sorted(SPARSITIES, key=lambda x: -FREEZE[x]):
        for _ in range(laps_per_code.get(c, 0)):
            k = min(range(engines), key=lambda t: (load[t], t))
            got[k][c] += 1
            load[k] += lap_cost(c, nwin)
    segs = [[[c, g[c]] for c in SPARSITIES if g[c]] for g in got]
    return segs, load


def segments_codes(segs):
    """[[code, laps], ...] -> the lap-by-lap codes, in order."""
    return [c for c, n in segs for _ in range(n)]


def calc_of(segs, nwin, lanes, layer):
    beats = sum(n * nwin * FREEZE[c] for c, n in segs)
    laps = sum(n for _, n in segs)
    return dict(layer=layer, nwin=nwin, segments=[list(s) for s in segs], laps=laps,
                rows=laps * lanes, beats=beats, cycles=beats + LAP_BUBBLE * laps,
                macs=sum(n * lanes * nwin * 32 * 2 // SP_M[c] for c, n in segs))


def plan(arch_name, clock_override=None):
    """-> the plan of one build (a dict, JSON-ready). Raises if it cannot run the test."""
    if arch_name not in ARCH:
        raise SystemExit("unknown build %s (known: %s)" % (arch_name, " ".join(sorted(ARCH))))
    name, shape, engines, clock, folder, xclbin = ARCH[arch_name]
    if clock_override:
        clock = clock_override
    if clock is None:
        raise SystemExit("%s: no clock recorded yet -- fill in its DATA_CLK (plan_workload.py "
                         "SHARED_ARCHS / plan_workload_bcast.py BCAST_ARCHS), or give --clock"
                         % name)
    cores, blocks = SHAPE_OF[shape]
    lanes = cores * blocks
    if ROWS_PER_CODE % lanes:
        raise SystemExit("%s: %d rows per sparsity is not a multiple of %d lanes -- the test "
                         "would pad" % (name, ROWS_PER_CODE, lanes))
    laps_per_code = dict((c, ROWS_PER_CODE // lanes) for c in SPARSITIES)
    segs, load = split_laps(laps_per_code, NWIN, engines)
    if any(not s for s in segs):
        raise SystemExit("%s: an engine gets no lap of the layer" % name)
    ts = []
    for k in range(engines):
        calcs = [calc_of(segs[k], NWIN, lanes, li) for li in range(LAYERS)]
        beats = sum(c["beats"] for c in calcs)
        fill = beats * PC_BYTES / float(PC_CAPACITY)
        if fill > 1.0:
            raise SystemExit("%s engine %d: %d weight beats per channel = %.0f%% of a 256 MB "
                             "HBM channel -- %d layers do not fit" % (name, k, beats,
                                                                      100 * fill, LAYERS))
        ts.append(dict(tenant=k, calcs=calcs, segments=segs[k], layer_beats=calcs[0]["beats"],
                       layer_cycles=calcs[0]["cycles"], layer_rows=calcs[0]["rows"],
                       beats=beats, cycles=sum(c["cycles"] for c in calcs),
                       macs=sum(c["macs"] for c in calcs), channel_fill=round(fill, 4)))
    if sum(t["layer_rows"] for t in ts) != LAYER_ROWS:
        raise SystemExit("%s: the split covers %d rows, the layer has %d"
                         % (name, sum(t["layer_rows"] for t in ts), LAYER_ROWS))
    layer_macs = sum(ROWS_PER_CODE * HIDDEN * 2 // SP_M[c] for c in SPARSITIES)
    if sum(t["macs"] for t in ts) != LAYERS * layer_macs:
        raise SystemExit("%s: the split does not hold the layer's MACs" % name)
    layer_cycles = max(load)
    cycles = LAYERS * layer_cycles
    macs = LAYERS * layer_macs
    return dict(arch=name, test="qwen", kind=kind(name), shape=shape, cores=cores,
                blocks=blocks, lanes=lanes, tenants=engines, clock_mhz=clock, folder=folder,
                xclbin=xclbin, channels_per_tenant=pw.channels_of(shape),
                host_flags=host_flags(name),
                shared_vector=kind(name) in ("shared", "broadcast"),
                broadcast=kind(name) == "broadcast",
                layers=LAYERS, layer_rows=LAYER_ROWS, width=HIDDEN, nwin=NWIN,
                experts_per_layer=ACTIVE_EXPERTS, rows_per_code=ROWS_PER_CODE,
                laps_per_code=laps_per_code[SPARSITIES[0]],
                useful_macs=macs, layer_macs=layer_macs, padding_pct=0.0,
                calcs_per_tenant=[LAYERS] * engines,
                layer_cycles=layer_cycles, predicted_cycles=cycles,
                predicted_layer_us=round(layer_cycles / clock, 2),
                predicted_us=round(cycles / clock, 1),
                predicted_gmac_s=round(macs / (cycles / clock) / 1e3, 2),
                balance=round(min(load) / max(load), 4),
                max_channel_fill=max(t["channel_fill"] for t in ts),
                tenant_plans=ts)


def write_plan(p):
    if not os.path.isdir(PLAN_DIR):
        os.makedirs(PLAN_DIR)
    path = os.path.join(PLAN_DIR, "qwen_%s.json" % p["arch"])
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(p, indent=1, sort_keys=False))
        f.write("\n")
    return path


def main():
    ap = argparse.ArgumentParser(description="plan the Qwen MoE step-1 test on every build")
    ap.add_argument("--arch", default=None, choices=sorted(ARCH), help="one build only")
    ap.add_argument("--clock", type=int, default=None,
                    help="MHz for a build whose DATA_CLK is not recorded yet (with --arch)")
    ap.add_argument("--no-write", action="store_true", help="print only, write no plans/*.json")
    a = ap.parse_args()
    if a.clock and not a.arch:
        raise SystemExit("--clock needs --arch")
    layer_macs = sum(ROWS_PER_CODE * HIDDEN * 2 // SP_M[c] for c in SPARSITIES)
    print("Qwen MoE step 1: %d layers, each %d experts x (gate + up) x %d rows = %d x %d, "
          "MIXED (%d rows per sparsity), a new vector per layer"
          % (LAYERS, ACTIVE_EXPERTS, EXPERT_ROWS, LAYER_ROWS, HIDDEN, ROWS_PER_CODE))
    print("useful MACs: %.3f M per layer, %.1f M per token" % (layer_macs / 1e6,
                                                               LAYERS * layer_macs / 1e6))
    names = [a.arch] if a.arch else [x[0] for x in archs()]
    waiting = [x[0] for x in BUILDS if x[3] is None]
    print("\n%-14s %-9s %3s %5s %4s %-17s %9s %10s %9s %8s %6s"
          % ("build", "kind", "n", "lanes", "MHz", "laps/code", "cyc/layer", "us/token",
             "GMAC/s", "balance", "fill"))
    for n in names:
        p = plan(n, clock_override=a.clock)
        if not a.no_write:
            write_plan(p)
        print("%-14s %-9s %3d %5d %4d %-17s %9.0f %10.1f %9.2f %7.2f%% %5.0f%%"
              % (p["arch"], p["kind"], p["tenants"], p["lanes"], p["clock_mhz"],
                 "%d (of %d rows)" % (p["laps_per_code"], p["lanes"]), p["layer_cycles"],
                 p["predicted_us"], p["predicted_gmac_s"], 100 * p["balance"],
                 100 * p["max_channel_fill"]))
        if a.arch:
            for t in p["tenant_plans"]:
                print("  engine %d: %4d rows, %6d beats, %7.0f cycles per layer  %s"
                      % (t["tenant"], t["layer_rows"], t["layer_beats"], t["layer_cycles"],
                         ", ".join("%s x %d laps" % (SP_NAME[c], l) for c, l in t["segments"])))
    print("\ncyc/layer = the busiest engine's predicted cycles for one layer (beats + %g per "
          "lap); us/token = %d x that at the clock (the ideal engine time, launches excluded); "
          "balance = least-loaded engine / busiest; fill = the fullest HBM channel."
          % (LAP_BUBBLE, LAYERS))
    if waiting and not a.arch:
        print("not listed until their DATA_CLK is recorded: %s" % ", ".join(waiting))
    if not a.no_write:
        print("plans written to %s/qwen_*.json" % os.path.relpath(PLAN_DIR))


if __name__ == "__main__":
    main()

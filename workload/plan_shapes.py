"""The shape sweep on the MULTI-ENGINE builds -- the 8x8 shape study's eleven matrices, MIXED,
each ONE matrix split over every engine of the build in lockstep: the latency of one request
on the whole build. Imported by run_shapes_xrt.py and run_window_shapes.py.

A NEW FILE (2026-10-01; the user chose "split across engines" and "MIXED on the 15 multi-engine
builds"). The six single engines already have this workload in results/family_measurements/
(run_shapes.py, 2026-09-13/14), so they are not here. Python 3.6, stdlib only.

THE MATRIX: shape G1..G11 (M x N, the list of Vitis/run_shapes.py), MIXED = four row quarters
at 2:4 / 2:8 / 2:16 / 2:32, each quarter whole laps of `lanes` rows: laps per sparsity =
ceil(M / (4 x lanes)), padding included -- the single engines' rule, so an engine computes
exactly the rows it computed in its own sweep.
THE SPLIT: those laps dealt one at a time, costliest sparsity first, each to the engine with the
fewest predicted cycles (plan_qwen.split_laps, the Qwen test's split B). The busiest engine sets
the matrix's latency; the others wait for it (lockstep).
THE BATCH: every engine runs R copies of ITS share as one calculation (the same vector), at two
sizes, R_small and R_big = 4 x R_small. A step lasts a fixed cost + R x the busiest share, so
(t_big - t_small) / (R_big - R_small) is the busiest engine's time for one matrix with every
fixed cost (launch, lockstep, completion) cancelled -- the quantity the single engines' batched
sweeps measure. R_small is chosen so the busiest engine streams ~TARGET_BEATS / 4 weight beats
(R_big: ~TARGET_BEATS, ~3 ms).
GROUPS: shapes go to the card a few at a time -- one host call per group, CHANNEL_BUDGET bytes
of weights per HBM channel at most (a channel holds 256 MB).
THE SOAK: one more host call -- every engine the same 1024-wide MIXED share, 4096 laps of each
sparsity (the single engines' soak: V = 1024, MIXED; 1,966,080 beats), repeated for 60 s with
the board power sampled: the build's power at full load.
"""

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plan_workload as pw                              # noqa: E402
import plan_qwen as pq                                  # noqa: E402  split_laps, builds, flags

SHAPES = [("G1", 512, 512), ("G2", 768, 768), ("G3", 1024, 1024), ("G4", 3072, 768),
          ("G5", 768, 3072), ("G6", 4096, 1024), ("G7", 1024, 4096), ("G8", 2048, 2048),
          ("G9", 4096, 4096), ("G10", 8192, 2048), ("G11", 2048, 8192)]
CODES = pw.SPARSITIES                                   # 00 01 10 11 = 2:4 2:8 2:16 2:32
FREEZE = pw.FREEZE
LAP_BUBBLE = pw.LAP_BUBBLE
PC_BYTES = pw.PC_BYTES
TARGET_BEATS = 2 ** 20                                  # the busiest engine at R_big
CHANNEL_BUDGET = 160 * 2 ** 20                          # weight bytes per HBM channel per call
SOAK_NWIN, SOAK_LAPS = 32, 4096                         # V = 1024, 4096 laps per sparsity
DENSITY_MIXED = sum(2.0 / pw.SP_M[c] for c in CODES) / len(CODES)    # 0.234375


def builds():
    """The multi-engine builds whose clock is recorded, in plan_qwen order."""
    return [a for a in pq.archs() if a[2] > 1]


def share_cost(segs, nwin):
    beats = sum(n * nwin * FREEZE[c] for c, n in segs)
    laps = sum(n for _, n in segs)
    return beats, beats + LAP_BUBBLE * laps, laps


def plan(name, clock_override=None):
    if name not in pq.ARCH:
        raise SystemExit("unknown build %s" % name)
    _n, shape, engines, clock, folder, xclbin = pq.ARCH[name]
    if engines < 2:
        raise SystemExit("%s has one engine: its shape sweep is in results/family_measurements"
                         % name)
    clock = clock_override or clock
    if clock is None:
        raise SystemExit("%s: no clock recorded yet" % name)
    cores, blocks = pq.SHAPE_OF[shape]
    lanes = cores * blocks
    out = []
    for g, M, N in SHAPES:
        nwin = N // 32
        lpc = int(math.ceil(float(M) / (4 * lanes)))
        segs, _load = pq.split_laps(dict((c, lpc) for c in CODES), nwin, engines)
        if any(not s for s in segs):
            raise SystemExit("%s %s: an engine gets no lap" % (name, g))
        cost = [share_cost(s, nwin) for s in segs]
        busiest_beats = max(c[0] for c in cost)
        r_small = max(1, int(math.ceil(TARGET_BEATS / 4.0 / busiest_beats)))
        r_big = 4 * r_small
        total_beats = sum(c[0] for c in cost)
        if total_beats != lpc * nwin * sum(FREEZE[c] for c in CODES):
            raise SystemExit("%s %s: the split loses beats" % (name, g))
        out.append(dict(
            shape=g, M=M, N=N, nwin=nwin, laps_per_code=lpc, laps=4 * lpc,
            padding_rows=4 * lpc * lanes - M, segments=segs,
            engine_beats=[c[0] for c in cost], engine_cycles=[c[1] for c in cost],
            engine_laps=[c[2] for c in cost], total_beats=total_beats,
            busiest_cycles=max(c[1] for c in cost),
            balance=round(min(c[1] for c in cost) / float(max(c[1] for c in cost)), 4),
            useful_macs=int(round(M * N * DENSITY_MIXED)),
            r_small=r_small, r_big=r_big,
            # per engine, the batched calculation: R copies of its share, sparsity by sparsity
            calc_small=[[[c, n * r_small] for c, n in s] for s in segs],
            calc_big=[[[c, n * r_big] for c, n in s] for s in segs],
            channel_bytes=max((r_small + r_big) * b for b in (c[0] for c in cost)) * PC_BYTES))
    # host calls: shapes in order, a new call whenever the busiest channel would pass the budget
    groups, cur, used = [], [], 0
    for i, s in enumerate(out):
        if s["channel_bytes"] > CHANNEL_BUDGET:
            raise SystemExit("%s %s: %.0f MB on one channel" % (name, s["shape"],
                                                                s["channel_bytes"] / 2.0 ** 20))
        if cur and used + s["channel_bytes"] > CHANNEL_BUDGET:
            groups.append(cur)
            cur, used = [], 0
        cur.append(i)
        used += s["channel_bytes"]
    groups.append(cur)
    soak = [[c, SOAK_LAPS] for c in CODES]
    soak_beats = share_cost(soak, SOAK_NWIN)[0]
    return dict(arch=name, kind=pq.kind(name), shape=shape, cores=cores, blocks=blocks,
                lanes=lanes, tenants=engines, clock_mhz=clock, folder=folder, xclbin=xclbin,
                host_flags=pq.host_flags(name), channels_per_tenant=pw.channels_of(shape),
                shapes=out, groups=groups, soak_nwin=SOAK_NWIN, soak_segments=soak,
                soak_beats=soak_beats, soak_rows_per_pass=engines * 4 * SOAK_LAPS * lanes)


def main():
    for a in builds():
        p = plan(a[0])
        print("%-15s %d x %-5s %3d MHz  %d host calls  %s" % (
            p["arch"], p["tenants"], p["shape"], p["clock_mhz"], len(p["groups"]),
            "  ".join("%s:R%d/%d bal %.2f" % (s["shape"], s["r_small"], s["r_big"], s["balance"])
                      for s in p["shapes"][:3])))


if __name__ == "__main__":
    main()

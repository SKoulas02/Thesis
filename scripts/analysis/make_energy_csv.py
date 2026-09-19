"""Build results/GEMV_Energy_300MHz.csv -- energy per matrix, split static/dynamic.

HOW IT IS DERIVED, AND WHY THAT IS LEGITIMATE.

    energy = power x time

Both halves are measured, just in separate experiments:

  * TIME comes from the shape sweep (results/GEMV_shapes_300MHz.csv):
    latency_per_matrix_us, for 11 matrix shapes x 6 configurations, each shape
    batched so the launch overhead is subtracted rather than dominating.

  * POWER comes from the 60 s soaks (results/power_results_0903.csv): board
    load and board idle for the same 6 configurations.

    static  energy = board_idle          x latency
    dynamic energy = (board_load - idle) x latency
    total          = board_load          x latency

  W x us = uJ directly, so no unit conversion is needed.

THE ASSUMPTION, STATED PLAINLY. Power was measured at V=1024 and is applied to
every shape. That holds because the engine consumes ~1 weight beat per clock in
every configuration, so switching activity is per-cycle and essentially
shape-independent -- and it was measured flat (35.85-35.99 W) across a 16x
throughput range. It is an assumption nonetheless; a spot-check soak at a wide
shape would convert it into a measurement.

WHY BOTH ABSOLUTE AND PER-ELEMENT COLUMNS. Absolute energy per matrix spans
16.7 uJ to 14,445 uJ across these shapes -- 864x -- so a linear chart is
unreadable and a log axis cannot carry a stacked bar. Dividing by M x N
collapses the size dependence: energy per element is flat across shapes and
separated purely by sparsity, which is both the readable chart and the more
honest comparison.

ONE THING THE DATA SAYS BEFORE ANY CHART: static is 84-86% of board energy in
every configuration, and the split does not move with shape. On a device this
size running one accelerator, leakage dominates, so the only way to save energy
is to FINISH SOONER. Sparse does not draw less power -- it draws more (35.9 W
vs 31.8 W) -- it just spends far less time drawing it.

Run:  python make_energy_csv.py
"""

import argparse
import csv
import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "results")


def _data(name):
    p = os.path.join(DATA, name)
    return p if os.path.exists(p) else os.path.join(HERE, name)


# Per clock: (shape sweep, [power files, in priority order]).
#
#   300 keeps power_results_0903.csv -- a COMPLETE six-configuration set measured
#   in ONE session, which is exactly what an energy comparison wants. Do not
#   switch it to power_results.csv (2026-08-31): that file has no MIXED row, and
#   swapping the source would move every 300 MHz energy value by ~1.6% and
#   silently invalidate the energy charts already built from this file.
#
#   325 needs TWO files because MIXED was measured separately and later:
#   power_results_325.csv (2026-09-02) covers dense + the four sparsities;
#   power_mixed_both.csv (2026-09-05) supplies MIXED. Later files are listed
#   FIRST so their rows win -- power_mixed_both.csv also carries a 300 MHz MIXED
#   row, which is a useful cross-check against the 0903 one (236.8 vs 242.4
#   nJ/row, 2.3% apart across sessions) but is not what the 300 set is built on.
#
#   ⚠️ power_mixed_325.csv is deliberately NOT used. It is a valid earlier
#   soak of the same configuration, but it was taken while the card was still
#   warm from a five-hour build: idle 30.88 W against 30.26 W once settled,
#   which inflated its energy by 3.9% at identical throughput (158.311 vs
#   158.930 Mrow/s, 0.39% apart). Kept as evidence that energy measurements
#   need a settled card and throughput measurements do not.
CLOCKS = {
    300: ("GEMV_shapes_300MHz.csv", ["power_results_0903.csv"]),
    325: ("GEMV_shapes_325MHz.csv", ["power_mixed_both.csv",
                                     "power_results_325.csv"]),
}

# shape-sweep label -> power-soak label
MAP = {"dense": "dense", "2:4": "sparse 2:4", "2:8": "sparse 2:8",
       "2:16": "sparse 2:16", "2:32": "sparse 2:32", "MIXED": "sparse MIXED"}
ORDER = ["dense", "2:4", "2:8", "2:16", "2:32", "MIXED"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clock", type=int, default=300, choices=sorted(CLOCKS))
    a = ap.parse_args()
    SHAPES, POWERS = CLOCKS[a.clock]
    OUT = os.path.join(DATA, "GEMV_Energy_%dMHz.csv" % a.clock)

    pw = {}
    # later-listed files must NOT overwrite earlier ones: first file wins, so
    # the freshest measurement of a configuration is the one used.
    for pf in POWERS:
        for r in csv.DictReader(io.open(_data(pf), newline="", encoding="utf-8")):
            if abs(float(r["clock_mhz"]) - a.clock) > 0.5:
                continue          # power_mixed_both.csv holds BOTH clocks
            pw.setdefault(r["label"], (float(r["board_load_w"]),
                                       float(r["board_idle_w"])))
    missing = [MAP[c] for c in ORDER if MAP[c] not in pw]
    if missing:
        raise SystemExit("no %d MHz power row for: %s" % (a.clock, ", ".join(missing)))

    shp = {}
    for r in csv.DictReader(io.open(_data(SHAPES), newline="", encoding="utf-8")):
        if r["shape"] == "GEOMEAN":          # no dimensions, nothing to plot
            continue
        shp[(r["sparsity"], r["shape"])] = r

    shapes = sorted({k[1] for k in shp}, key=lambda s: int(shp[("dense", s)]["shape_order"]))

    out = []
    for ci, cfg in enumerate(ORDER, start=1):
        for sh in shapes:
            r = shp[(cfg, sh)]
            load, idle = pw[MAP[cfg]]
            lat = float(r["latency_per_matrix_us"])
            elems = int(r["M_rows"]) * int(r["N_cols"])
            st = idle * lat                      # W x us = uJ
            dy = (load - idle) * lat
            tot = st + dy
            d = shp[("dense", sh)]
            dtot = pw["dense"][0] * float(d["latency_per_matrix_us"])
            out.append(dict(
                design=r["design"], sparsity=cfg, sparsity_order=ci,
                shape=sh, shape_order=int(r["shape_order"]),
                dimensions=r["dimensions"], matrix_elements=elems,
                M_rows=int(r["M_rows"]), N_cols=int(r["N_cols"]),
                latency_per_matrix_us=round(lat, 4),
                board_load_W=load, board_idle_W=idle,
                board_dynamic_W=round(load - idle, 4),
                energy_static_uJ=round(st, 3),
                energy_dynamic_uJ=round(dy, 3),
                energy_total_uJ=round(tot, 3),
                energy_static_pJ_per_element=round(st / elems * 1e6, 3),
                energy_dynamic_pJ_per_element=round(dy / elems * 1e6, 3),
                energy_total_pJ_per_element=round(tot / elems * 1e6, 3),
                static_pct=round(100.0 * st / tot, 2),
                energy_vs_dense=round(dtot / tot, 4),
                GFLOPS_effective=float(r["GFLOPS_effective"])))

    cols = ["design", "sparsity", "sparsity_order", "shape", "shape_order",
            "dimensions", "matrix_elements", "M_rows", "N_cols",
            "latency_per_matrix_us", "board_load_W", "board_idle_W",
            "board_dynamic_W", "energy_static_uJ", "energy_dynamic_uJ",
            "energy_total_uJ", "energy_static_pJ_per_element",
            "energy_dynamic_pJ_per_element", "energy_total_pJ_per_element",
            "static_pct", "energy_vs_dense", "GFLOPS_effective"]
    if not os.path.isdir(DATA):
        os.makedirs(DATA)
    with io.open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\r\n")
        w.writeheader()
        for r in out:
            w.writerow(r)
    print("wrote %s : %d rows x %d cols" % (OUT, len(out), len(cols)))

    print("\n  energy per element (pJ), stacked static + dynamic:")
    print("  %-8s %10s %10s %10s %10s" %
          ("config", "static", "dynamic", "total", "vs dense"))
    for cfg in ORDER:
        rs = [r for r in out if r["sparsity"] == cfg]
        st = sum(r["energy_static_pJ_per_element"] for r in rs) / len(rs)
        dy = sum(r["energy_dynamic_pJ_per_element"] for r in rs) / len(rs)
        vd = sum(r["energy_vs_dense"] for r in rs) / len(rs)
        print("  %-8s %10.1f %10.1f %10.1f %9.2fx" % (cfg, st, dy, st + dy, vd))


if __name__ == "__main__":
    main()

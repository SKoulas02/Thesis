"""Build the two MIXED-workload sheets of the family study: latency and energy per matrix.

    python scripts/analysis/make_family_mixed_csv.py

Inputs : results/family_measurements/<tag>/shapes_<tag>_<MHz>_MIXED.csv   11 shapes each
         results/family_measurements/<tag>/power_<tag>_<MHz>.csv           the MIXED soak row
Outputs: results/GEMV_Family_Mixed_Latency.csv   WIDE, 11 rows (shape) x one column per family
         results/GEMV_Family_Mixed_Energy.csv    LONG, 66 rows (shape x family)

ONE WORKLOAD, SIX ENGINES. Every family ran the same eleven MIXED matrices (four row
quarters at 2:4 / 2:8 / 2:16 / 2:32), so each shape group compares the six engines on
identical work, EACH AT ITS OWN CLOSED CLOCK (400 / 375 / 350 / 325 / 300 / 250 MHz).

LATENCY = run_shapes.py's own latency_per_matrix_us (per-sweep launch-overhead fit), used as
recorded. Decided 2026-09-18: the inverse-variance pooled overhead that the other family
charts use moves these values by at most 0.68%, so it is not applied here. The decision is
CHECKED on every run, not assumed: the pooled value is recomputed with
check_family_measure.steady() and nothing is written if any cell differs by more than 1%.

PADDING IS INCLUDED ON PURPOSE. A MIXED matrix is four quarters of whole laps, so its row
granularity is 4 x lanes: 64 rows on 4x4, 192 on 16x3, 384 on 4x24, 512 on 4x32. A matrix
that does not divide is padded and the engine pays for the extra rows. That is why 4x24 is
slower than 8x8 on 512x512 and 4x32 is slower than 4x24 on 768x768: it is the real time to
finish that workload on that engine. padding_rows sits beside every value for the caption,
and is checked against the 4 x lanes rule.

ENERGY = power x time, the same split as make_energy_csv.py:
    static  = board_idle            x latency        W x us = uJ
    dynamic = (board_load - idle)   x latency
using each family's OWN MIXED soak -- load and idle of that bitstream -- so static energy
differs between families: a bigger design costs more to keep powered even while idle.
The per-element columns divide by the TRUE M x N, so padded rows show up as a cost per real
element rather than disappearing.

THE OUTER CATEGORY LABEL IS ON THE FIRST ROW OF EACH GROUP ONLY (column shape_group).
Excel's multi-level category axis starts a new group at every non-blank outer cell. The
existing 8x8 energy charts put each label on the THIRD row of its six-row group, to centre
it, and every group there is shifted two bars. Never move these labels to "centre" them.
"""

import csv
import io
import math
import os
import statistics

import check_family_measure as cfm

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # repository root; this file is in scripts/analysis/
RES = os.path.join(ROOT, "results")
OUT_LAT = os.path.join(RES, "GEMV_Family_Mixed_Latency.csv")
OUT_EN = os.path.join(RES, "GEMV_Family_Mixed_Energy.csv")

POOLED_LIMIT_PCT = 1.0          # the "close enough" condition for using per-sweep latency


def write_csv(path, rows):
    with io.open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), lineterminator="\r\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main():
    problems, fam = [], {}
    for tag in cfm.ORDER:
        C, B, clk = cfm.CONFIGS[tag]
        L = C * B
        o = cfm.load(tag)
        if o is None:
            raise SystemExit("missing measurements for %s" % tag)
        S = o["shapes"]["MIXED"]
        pw = [r for r in o["power"] if r["label"].split()[-1] == "MIXED"]

        if len(S) != 11:
            problems.append("%s: MIXED sweep has %d shapes, expected 11" % (tag, len(S)))
        if len(pw) != 1:
            problems.append("%s: %d MIXED power rows, expected 1" % (tag, len(pw)))
            continue
        load_w, idle_w = float(pw[0]["board_load_w"]), float(pw[0]["board_idle_w"])
        if not idle_w < load_w:
            problems.append("%s: MIXED soak idle %.2f W >= load %.2f W" % (tag, idle_w, load_w))
        if int(float(pw[0]["clock_mhz"])) != clk:
            problems.append("%s: power CSV clock %s != %d" % (tag, pw[0]["clock_mhz"], clk))

        # the per-sweep latency is used only if the pooled method agrees within the limit
        _, pooled = cfm.steady(o, "MIXED")
        rows = []
        for r, p in zip(S, pooled):
            M, N = int(r["M_rows"]), int(r["N_cols"])
            lat = float(r["latency_per_matrix_us"])
            if r["sparsity"] != "MIXED" or int(float(r["clock_mhz"])) != clk:
                problems.append("%s %s: sparsity/clock %s/%s in the MIXED file"
                                % (tag, r["shape"], r["sparsity"], r["clock_mhz"]))
            if lat <= 0:
                problems.append("%s %s: latency %s" % (tag, r["shape"], lat))
            pooled_lat = float(r["ideal_per_matrix_us"]) / p["occ"]
            dev = 100.0 * abs(lat / pooled_lat - 1.0)
            if dev > POOLED_LIMIT_PCT:
                problems.append("%s %s: per-sweep %.3f us vs pooled %.3f us (%.2f%% > %.1f%%)"
                                % (tag, r["shape"], lat, pooled_lat, dev, POOLED_LIMIT_PCT))
            grain = 4 * L                                   # mixed quarters of whole laps
            pad_expected = int(math.ceil(float(M) / grain)) * grain - M
            pad = int(r["padding_rows"] or 0)
            if pad != pad_expected:
                problems.append("%s %s: padding_rows %d, the 4 x lanes rule gives %d"
                                % (tag, r["shape"], pad, pad_expected))
            rows.append(dict(shape=r["shape"], M=M, N=N, lat=lat, pad=pad, dev=dev))
        fam[tag] = dict(clk=clk, L=L, load=load_w, idle=idle_w, rows=rows)

    # every family must have run the SAME eleven matrices in the same order
    ref = [(x["shape"], x["M"], x["N"]) for x in fam[cfm.ORDER[0]]["rows"]]
    for tag in cfm.ORDER[1:]:
        if [(x["shape"], x["M"], x["N"]) for x in fam[tag]["rows"]] != ref:
            problems.append("%s: its MIXED shapes differ from %s's" % (tag, cfm.ORDER[0]))

    if problems:
        print("CHECKS FAILED -- nothing written:")
        for p in problems:
            print("   ", p)
        raise SystemExit(1)

    # ---- WIDE latency sheet: A = category, B:G = one series per family ----
    lat_rows = []
    for i, (shape, M, N) in enumerate(ref):
        row = dict(dimensions="%dx%d" % (M, N))
        for tag in cfm.ORDER:
            row["latency_us_" + tag] = fam[tag]["rows"][i]["lat"]
        for tag in cfm.ORDER:
            row["padding_rows_" + tag] = fam[tag]["rows"][i]["pad"]
        row.update(shape=shape, shape_order=i + 1, M_rows=M, N_cols=N, matrix_elements=M * N,
                   data_source="shapes_<tag>_<MHz>_MIXED.csv latency_per_matrix_us, "
                               "per-sweep overhead fit, each family at its own clock")
        lat_rows.append(row)

    # ---- LONG energy sheet: A:B = two-level category, C:D and E:F = the two stacks ----
    en_rows = []
    for i, (shape, M, N) in enumerate(ref):
        for j, tag in enumerate(cfm.ORDER):
            f = fam[tag]
            x = f["rows"][i]
            st = f["idle"] * x["lat"]
            dy = (f["load"] - f["idle"]) * x["lat"]
            el = float(M * N)
            en_rows.append(dict(
                shape_group="%dx%d" % (M, N) if j == 0 else "",    # FIRST row only
                family=tag,
                energy_static_uJ=round(st, 3),
                energy_dynamic_uJ=round(dy, 3),
                energy_static_pJ_per_element=round(st / el * 1e6, 3),
                energy_dynamic_pJ_per_element=round(dy / el * 1e6, 3),
                energy_total_uJ=round(st + dy, 3),
                energy_total_pJ_per_element=round((st + dy) / el * 1e6, 3),
                dimensions="%dx%d" % (M, N), shape=shape, shape_order=i + 1,
                family_order=j + 1, clock_mhz=f["clk"], M_rows=M, N_cols=N,
                matrix_elements=M * N, padding_rows=x["pad"],
                latency_per_matrix_us=x["lat"],
                board_load_W=f["load"], board_idle_W=f["idle"],
                board_dynamic_W=round(f["load"] - f["idle"], 3),
                static_pct=round(100.0 * st / (st + dy), 2),
                data_source="latency x the family's own MIXED 60 s soak (board load / idle)"))

    write_csv(OUT_LAT, lat_rows)
    write_csv(OUT_EN, en_rows)
    print("wrote %s : %d rows x %d cols" % (OUT_LAT, len(lat_rows), len(lat_rows[0])))
    print("wrote %s : %d rows x %d cols" % (OUT_EN, len(en_rows), len(en_rows[0])))

    worst = max((x["dev"], tag, x["shape"]) for tag in cfm.ORDER for x in fam[tag]["rows"])
    print("\nper-sweep vs pooled latency: worst %.2f%% (%s %s), limit %.1f%%"
          % (worst[0], worst[1], worst[2], POOLED_LIMIT_PCT))
    print("\n%-11s" % "latency us" + "".join("%11s" % ("%s@%d" % (t, fam[t]["clk"]))
                                             for t in cfm.ORDER))
    for r in lat_rows:
        print("%-11s" % r["dimensions"] + "".join(
            "%10.2f%s" % (r["latency_us_" + t], "*" if r["padding_rows_" + t] else " ")
            for t in cfm.ORDER))
    print("  * = padded rows included (MIXED row granularity is 4 x lanes)")
    print("\nMIXED soak (W)  " + "  ".join("%s %.1f/%.1f" % (t, fam[t]["load"], fam[t]["idle"])
                                         for t in cfm.ORDER) + "   (load/idle)")
    tot = [r["energy_total_uJ"] for r in en_rows]
    pe = [r["energy_total_pJ_per_element"] for r in en_rows]
    print("energy per matrix %.1f .. %.1f uJ ; per element %.1f .. %.1f pJ"
          % (min(tot), max(tot), min(pe), max(pe)))


if __name__ == "__main__":
    main()

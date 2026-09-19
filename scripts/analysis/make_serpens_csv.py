"""Build results/GEMV_vs_Serpens.csv -- the equal-non-zero-count comparison.

WHAT THIS COMPARES, AND WHAT IT DOES NOT
========================================
Serpens (Song, Chi, Guo, Cong, "Serpens: A High Bandwidth Memory Based
Accelerator for General-Purpose Sparse Matrix-Vector Multiplication", DAC'22)
and this thesis both run sparse-matrix x dense-vector on an Alveo U280 with
EXACTLY 128 multiply-accumulate units:

  * Serpens-A16: 8 PEs per HBM channel x 16 channels = 128 PEs, one non-zero
    per PE per cycle (their Sec. 3.1.2; confirmed by the NNZ/(8 x H_A) term of
    their Eq. 4), at 223 MHz (their Table 2).
  * This engine: 8 C Cores x 8 C Blocks x 2 multipliers = 128 MACs, at 300/325.

So the two machines can be compared on ONE well-defined quantity: the rate at
which they retire non-zero MACs. That is what this script computes.

IT DOES NOT SHOW THAT THIS ENGINE "SOLVES SpMV BETTER". We are not running
their matrices -- we cannot: their smallest has 38.1K columns against this
engine's hard 8,192 limit, and their densest is 2:140 against this engine's
densest mode of 2:32. We are running OUR matrices scaled to THEIR non-zero
count. Their memory system absorbs an irregularity ours never sees. Any use of
this table must say so in the same breath.

TWO RATIOS ARE EMITTED, AND THE CONSERVATIVE ONE IS THE HEADLINE
================================================================
  speedup_raw           their total time / our time.
  speedup_compute_only  their total time MINUS their dense-vector streaming
                        term, over our time.

Their Eq. (4) is  #Cycle = (M + K)/16 + NNZ/(8 x H_A).  The first term is real
I/O -- streaming a vector too large to hold on chip -- that this engine does
not do, because its vector fits in the replay buffer. It is 1.8% to 21.7% of
their measured time depending on the matrix. Subtracting it is the strictest
defensible comparison, so speedup_compute_only is the number to quote.

WHERE THE NUMBERS COME FROM
===========================
Serpens side: their Table 3 (#Vertices, #Edges) and their Table 4 (Serpens-A16
execution time, GFLOP/s, MTEPS). All twelve matrices are square, so
#Vertices = M = K = the dense-vector length, and #Edges = NNZ. Verified against
their own Sec. 3.1.1 remark that hollywood is "1.25 GB" of matrix and "4 MB" of
vector: 1.07e6 x 4 B = 4.08 MiB, and 113e6 x 12 B (uncompressed 32-bit row +
32-bit col + 32-bit float) = 1.263 GiB. Both land.

THE NON-ZERO COUNT IS TAKEN FROM TABLE 4, NOT TABLE 3, AND HERE IS WHY.
Their Sec. 4.2.2 defines MTEPS as (NNZ)/(execution time), so MTEPS x time IS
their non-zero count, self-consistently, from a single table. For eleven of the
twelve matrices that product agrees with Table 3's #Edges to within ~1%. For
G8 coPapersCiteseer it does not: Table 3 prints 21.1 M edges, but
15,324 MTEPS x 2.09 ms = 32.0 M -- a 52% disagreement. 32,073,440 is the true
symmetric non-zero count of that matrix in SuiteSparse, so Table 3's G8 entry
is the wrong one.

That error runs in OUR FAVOUR: believing 21.1 M would have this engine finish
G8 in 0.576 ms instead of 0.874 ms and inflate that row from 2.39x to 3.63x.
Using the Table 4 product removes the benefit of their typo. Both values are
emitted (nnz, nnz_table3, nnz_disagree_pct) so the choice is auditable.

Thesis side: MEASURED, read from a run_shapes.py CSV. Never hardcoded here.
The rate used is

    mac_rate = beats_per_matrix x 128 / latency_per_matrix_us

where latency_per_matrix_us is already the steady-state cost -- the batched
span with the launch overhead subtracted, divided by the batch factor.

ESTIMATOR MATCHING MATTERS. Serpens "amortize[s] the execution time by 100
runs" (their Sec. 4.1.1), i.e. launch overhead is divided away. run_shapes.py
subtracts it. Those are the matching conventions. run_avg3.py INCLUDES one
launch per run and reads 5-6% slower on identical hardware -- do not feed its
CSV to this script.

Run:
    python scripts/analysis/make_serpens_csv.py                       # geomean of the 2:32 sweep
    python scripts/analysis/make_serpens_csv.py --headline H1         # the equal-NNZ run alone
    python scripts/analysis/make_serpens_csv.py --shapes-csv results/shapes_2to32_325MHz.csv
"""

import argparse
import csv
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # repository root; this file is in scripts/analysis/
DATA = os.path.join(ROOT, "results")

# ---------------------------------------------------------------------------
# Serpens-A16, as published. Do not "improve" these numbers.
#
#   vertices  their Table 3, #Vertices  = M = K = dense-vector length
#   nnz       their Table 3, #Edges     = number of non-zeros
#   ms        their Table 4, Execution Time, Serpens-A16 row
#   gflops    their Table 4, Throughput GFLOP/s, Serpens-A16 row
#   mteps     their Table 4, Throughput MTEPS,   Serpens-A16 row
#
# Table 3 prints vertices to 3 significant figures ("108 K", "1.07 M"), so the
# vertex counts below carry that precision and no more. They feed only the
# streaming term, which is a correction, not the headline.
# ---------------------------------------------------------------------------
SERPENS = [
    # id    matrix                vertices      nnz      ms   gflops   mteps
    ("G1",  "googleplus",          108000,  13.7e6,  1.870,  14.71,  7300),
    ("G2",  "crankseg_2",           63800,  14.1e6,  0.930,  30.56, 15214),
    ("G3",  "Si41Ge41H72",         186000,  15.0e6,  0.853,  35.62, 17594),
    ("G4",  "TSOPF_RS_b2383",       38100,  16.2e6,  0.730,  44.39, 22144),
    ("G5",  "ML_Laplace",          377000,  27.6e6,  1.370,  40.75, 20099),
    ("G6",  "mouse_gene",           45100,  29.0e6,  1.370,  42.26, 21098),
    ("G7",  "soc_pokec",          1630000,  30.6e6,  4.520,  14.29,  6782),
    ("G8",  "coPapersCiteseer",    434000,  21.1e6,  2.090,  31.06, 15324),
    ("G9",  "PFlow_742",           743000,  37.1e6,  2.050,  37.01, 18142),
    ("G10", "ogbl_ppa",            576000,  42.5e6,  2.040,  42.26, 20847),
    ("G11", "hollywood",          1070000, 113.0e6,  6.200,  36.70, 18176),
    ("G12", "ogbn_products",      2450000, 124.0e6,  6.320,  39.90, 19565),
]

SERPENS_MACS = 128                 # 8 PEs/channel x 16 channels (their Table 1)
SERPENS_CLOCK_HZ = 223e6           # their Table 2
SERPENS_VECTOR_ELEMS_PER_BEAT = 16 # 512-bit beat / FP32 (their Sec. 3.1.2)

THESIS_MACS = 128                  # 8 cores x 8 blocks x 2 multipliers

# H1 is the equal-NNZ shape: 220,672 x 8,192 at 2:32 = 112,984,064 non-zeros,
# which is hollywood's 113M to within 0.014%. See EXTRA_SHAPES in run_shapes.py.
DEFAULT_SHAPES_CSV = os.path.join(DATA, "shapes_2to32_300MHz.csv")


def geomean(xs):
    xs = [x for x in xs if x and x > 0]
    if not xs:
        return None
    return math.exp(sum(math.log(x) for x in xs) / len(xs))


def read_thesis_rate(path, headline):
    """Return (mac_rate_per_s, occupancy, clock_mhz, provenance) from a
    run_shapes.py CSV.

    With --headline NAME, only that shape's row is used and the result is a
    single MEASURED point. Without it, the geometric mean over every row in the
    file is used and the result is a projection from the sweep.
    """
    if not os.path.exists(path):
        raise SystemExit("no such shapes CSV: {}".format(path))
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            if not r.get("latency_per_matrix_us"):
                continue
            rows.append(r)
    if not rows:
        raise SystemExit("{}: no usable rows".format(path))

    if headline:
        rows = [r for r in rows if r["shape"] == headline]
        if not rows:
            raise SystemExit(
                "{}: no row for shape '{}'. Run it first:\n"
                "    python3 run_shapes.py --design sparse --sparsity 11 "
                "--only {} ...".format(path, headline, headline))

    rates, occs = [], []
    for r in rows:
        beats = float(r["beats_per_matrix"])
        per_us = float(r["latency_per_matrix_us"])
        rates.append(beats * THESIS_MACS / (per_us * 1e-6))
        occs.append(float(r["dsp_occupancy"]))

    clock = float(rows[0]["clock_mhz"])
    sparsity = rows[0]["sparsity"]
    if headline:
        prov = "{} row {} ({} @ {:.0f} MHz), MEASURED".format(
            os.path.basename(path), headline, sparsity, clock)
    else:
        prov = "{} geomean of {} shapes ({} @ {:.0f} MHz)".format(
            os.path.basename(path), len(rows), sparsity, clock)
    return geomean(rates), geomean(occs), clock, prov


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shapes-csv", default=DEFAULT_SHAPES_CSV,
                    help="run_shapes.py output to take the measured MAC rate "
                         "from (default: %(default)s)")
    ap.add_argument("--headline", default=None, metavar="SHAPE",
                    help="use ONLY this shape's row, giving a single measured "
                         "point instead of a sweep geomean. Use H1 once the "
                         "equal-NNZ run exists.")
    ap.add_argument("--out", default=os.path.join(DATA, "GEMV_vs_Serpens.csv"))
    a = ap.parse_args()

    rate, occ, clock, prov = read_thesis_rate(a.shapes_csv, a.headline)
    serpens_ceiling = SERPENS_MACS * SERPENS_CLOCK_HZ

    print("thesis MAC rate : {:.3f} G MAC/s   (occupancy {:.4f}, {:.0f} MHz)"
          .format(rate / 1e9, occ, clock))
    print("source          : {}".format(prov))
    print("Serpens ceiling : {:.3f} G MAC/s   (128 MACs @ 223 MHz)\n"
          .format(serpens_ceiling / 1e9))

    rows = []
    raw, comp = [], []
    for i, (sid, name, verts, nnz_t3, ms, gf_paper, mteps) in enumerate(SERPENS):
        their_s = ms * 1e-3

        # NNZ from their own Table 4: MTEPS is defined as NNZ/time (Sec. 4.2.2),
        # so MTEPS x time recovers NNZ without trusting Table 3. See the module
        # docstring for the G8 discrepancy this exists to neutralise.
        nnz = mteps * 1e6 * their_s
        nnz_drift = 100.0 * (nnz_t3 - nnz) / nnz
        if abs(nnz_drift) > 3.0:
            print("  NOTE {} ({}): Table 3 prints {:,.0f} non-zeros but "
                  "Table 4's own MTEPS x time gives {:,.0f} ({:+.1f}%). "
                  "Using Table 4."
                  .format(sid, name, nnz_t3, nnz, nnz_drift))

        their_rate = nnz / their_s
        gf_from_time = 2.0 * nnz / their_s / 1e9

        # Their Eq. (4) streaming term: (M + K)/16 cycles, and M = K = vertices.
        stream_s = (2.0 * verts / SERPENS_VECTOR_ELEMS_PER_BEAT) / SERPENS_CLOCK_HZ
        their_compute_s = their_s - stream_s

        ours_s = nnz / rate

        s_raw = their_s / ours_s
        s_comp = their_compute_s / ours_s
        raw.append(s_raw)
        comp.append(s_comp)

        # Surface the paper's own internal drift rather than hiding it: its
        # GFLOP/s row and its time row do not always agree (worst: G7, where
        # 6,782 MTEPS implies 13.56 GFLOP/s but the table prints 14.29).
        drift = 100.0 * (gf_paper - gf_from_time) / gf_from_time
        if abs(drift) > 3.0:
            print("  NOTE {} ({}): Table 4 prints {:.2f} GFLOP/s but its own "
                  "MTEPS row implies {:.2f} ({:+.1f}%)"
                  .format(sid, name, gf_paper, gf_from_time, drift))

        rows.append(dict(
            row_order=i + 1,
            serpens_id=sid,
            matrix=name,
            vertices=int(verts),
            nnz=int(round(nnz)),
            nnz_table3=int(nnz_t3),
            nnz_disagree_pct=round(nnz_drift, 2),
            serpens_ms=round(ms, 3),
            serpens_gflops_paper=gf_paper,
            serpens_mteps_paper=mteps,
            serpens_gflops_from_time=round(gf_from_time, 2),
            serpens_gflops_drift_pct=round(drift, 2),
            serpens_mac_rate_G=round(their_rate / 1e9, 3),
            serpens_occupancy=round(their_rate / serpens_ceiling, 4),
            serpens_stream_ms=round(stream_s * 1e3, 4),
            serpens_stream_pct=round(100.0 * stream_s / their_s, 2),
            serpens_compute_ms=round(their_compute_s * 1e3, 4),
            thesis_ms=round(ours_s * 1e3, 4),
            thesis_mac_rate_G=round(rate / 1e9, 3),
            thesis_occupancy=round(occ, 4),
            thesis_clock_mhz=clock,
            speedup_raw=round(s_raw, 3),
            speedup_compute_only=round(s_comp, 3),
            data_source=prov))

    # Geomean row. Size-dependent columns are left BLANK: a geometric mean of
    # latencies across a 9x non-zero-count range is not a quantity. Only the
    # ratios and the occupancies are meaningful here.
    rows.append(dict(
        row_order=len(SERPENS) + 1,
        serpens_id="GEOMEAN",
        matrix="",
        serpens_occupancy=round(
            geomean([r["serpens_occupancy"] for r in rows]), 4),
        thesis_mac_rate_G=round(rate / 1e9, 3),
        thesis_occupancy=round(occ, 4),
        thesis_clock_mhz=clock,
        speedup_raw=round(geomean(raw), 3),
        speedup_compute_only=round(geomean(comp), 3),
        data_source=prov))

    cols = ["row_order", "serpens_id", "matrix", "vertices", "nnz",
            "nnz_table3", "nnz_disagree_pct",
            "serpens_ms", "serpens_gflops_paper", "serpens_mteps_paper",
            "serpens_gflops_from_time", "serpens_gflops_drift_pct",
            "serpens_mac_rate_G", "serpens_occupancy",
            "serpens_stream_ms", "serpens_stream_pct", "serpens_compute_ms",
            "thesis_ms", "thesis_mac_rate_G", "thesis_occupancy",
            "thesis_clock_mhz", "speedup_raw", "speedup_compute_only",
            "data_source"]

    outdir = os.path.dirname(os.path.abspath(a.out))
    if not os.path.isdir(outdir):
        os.makedirs(outdir)
    with open(a.out, "w") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n",
                           extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print("\n  %-5s %-18s %10s %9s %9s %8s %8s" %
          ("id", "matrix", "nnz", "their ms", "our ms", "raw", "compute"))
    for r in rows[:-1]:
        print("  %-5s %-18s %10s %9.3f %9.3f %7.2fx %7.2fx" %
              (r["serpens_id"], r["matrix"], "{:,}".format(r["nnz"]),
               r["serpens_ms"], r["thesis_ms"],
               r["speedup_raw"], r["speedup_compute_only"]))
    g = rows[-1]
    print("  %-5s %-18s %10s %9s %9s %7.2fx %7.2fx" %
          ("GMN", "", "", "", "", g["speedup_raw"], g["speedup_compute_only"]))
    print("\nCSV written: {}".format(a.out))
    print("QUOTE speedup_compute_only ({:.2f}x geomean) -- it removes their "
          "vector-streaming\nterm, which is real I/O this engine does not do."
          .format(g["speedup_compute_only"]))


if __name__ == "__main__":
    main()

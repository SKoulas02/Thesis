"""Build the CORES x BLOCKS family study -- two chartable CSVs from 18 OOC points.

    results/GEMV_Family_AllPoints.csv   all 18, with derived efficiency columns
    results/GEMV_Family_Scaling.csv     6 rows, the best point per family --
                                        THE DSP vs HBM-CHANNEL SERIES

WHAT WAS SWEPT. Six block-count families T = CORES x BLOCKS in {16,32,48,64,96,128},
each at CORES in {4,8,16}, on the BARE ENGINE (two2N) out-of-context at a 1.5 ns
constraint. Every derived width moves with T:

    DSPs = 8 x T          W_PCS    = ceil(T x 32 / 256)
    MACs = 2 x T          IND_BITS = 10 x T
    W_IDX = 2 x BLOCKS    IND_PCS  = ceil((IND_BITS + 2) / 256)   <- see below
                          C_PCS    = ceil(T x 16 / 256)
                          A_PCS    = 2 (the 32-element window is 512b at any T)

  WHY 1.5 ns AND NOT THE USUAL 2.222. Fmax = 1000/(PERIOD - WNS) is a
  MEASUREMENT only when the design MISSES the constraint; a design that MEETS it
  reports a lower bound, because Vivado stops optimising the moment it passes.
  At 2.222 ns the small families passed -- 4x4 came back +0.110 with ZERO failing
  endpoints and "473.5 MHz". Re-run at 1.5 ns, the same design measures 619.6.
  146 MHz was hiding behind a satisfied constraint. Every point here misses.

  ⚠️ THESE NUMBERS ARE NOT COMPARABLE WITH THE C x B = 64 DIAGONAL STUDY, which
  used 2.222 ns AND the two2N_axis WRAPPER. T=64 was re-run here for exactly that
  reason. Keep GEMV_Diagonal_CxB64.csv as the separate study it is.

TWO HARD BOUNDS, BOTH FOUND BY FAILED SYNTHESIS RATHER THAN BY ANALYSIS:

  T >= 16   The engine drives c_fifo with T x 16 bits into a C_PCS x 256 port,
            so the output beat must FILL whole pseudo-channels. T=8 gives 128
            bits against a 256-bit port; the whole family was rejected.

  T <= 128  IND_PCS needs (IND_BITS + 2), not IND_BITS, because the sparsity code
            rides ABOVE the index bits: ind_concat(IND_BITS+1 downto IND_BITS).
            At T=128, IND_BITS = 1280 = 5 x 256 EXACTLY, leaving no padding --
            "array index 1281 out of range". A 6th index channel fixes it and
            takes the design to 32 of the U280's 32 channels, one of which
            carries TWO BITS.

  So the architecture's usable range on this device is 16 <= T <= 128, bounded
  below by channel granularity and above by channel count.

Run:  python make_family_csv.py
"""

import csv
import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "results")

SRC = os.path.join(DATA, "family_summary_all18.csv")
OUT_ALL = os.path.join(DATA, "GEMV_Family_AllPoints.csv")
OUT_SCALE = os.path.join(DATA, "GEMV_Family_Scaling.csv")


def main():
    rows = list(csv.DictReader(io.open(SRC, newline="", encoding="utf-8")))

    fam = {}
    out = []
    for r in rows:
        T = int(r["blocks_total"])
        dsp, pc = int(r["dsp_prim"]), int(r["hbm_pcs"])
        f = float(r["fmax_mhz"])
        lut, ff = int(r["lut_prim"]), int(r["ff_prim"])
        # GMAC/s at the achieved clock. MACs/cycle x MHz / 1000.
        g = 2 * T * f / 1000.0
        rec = dict(
            config=r["config"], cores=int(r["cores"]), blocks=int(r["blocks"]),
            blocks_total=T, family="T=%d" % T,
            macs_per_cycle=2 * T, dsps=dsp, hbm_pcs=pc,
            w_pcs=int(r["w_pcs"]), ind_pcs=int(r["ind_pcs"]),
            a_pcs=int(r["a_pcs"]), c_pcs=int(r["c_pcs"]),
            w_idx=int(r["w_idx"]), ind_bits=int(r["ind_bits"]),
            wns_ns=float(r["wns_ns"]), tns_ns=float(r["tns_ns"]),
            failing_endpoints=int(r["failing_endpoints"]),
            fmax_mhz=f, lut_prim=lut, ff_prim=ff,
            muxf7_prim=int(r["muxf7_prim"]), ramb_prim=float(r["ramb_prim"]),
            # ---- the efficiency columns, which are the point of the study ----
            lut_per_dsp=round(float(lut) / dsp, 1),
            ff_per_dsp=round(float(ff) / dsp, 1),
            gmac_per_s=round(g, 1),
            gmac_per_channel=round(g / pc, 2),
            dsps_per_channel=round(float(dsp) / pc, 1))
        out.append(rec)
        fam.setdefault(T, []).append(rec)

    cols = ["config", "family", "cores", "blocks", "blocks_total",
            "macs_per_cycle", "dsps", "hbm_pcs", "w_pcs", "ind_pcs", "a_pcs",
            "c_pcs", "w_idx", "ind_bits", "wns_ns", "tns_ns",
            "failing_endpoints", "fmax_mhz", "lut_prim", "ff_prim",
            "muxf7_prim", "ramb_prim", "lut_per_dsp", "ff_per_dsp",
            "gmac_per_s", "gmac_per_channel", "dsps_per_channel"]
    with io.open(OUT_ALL, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\r\n")
        w.writeheader()
        for r in out:
            w.writerow(r)
    print("wrote %s : %d rows" % (OUT_ALL, len(out)))

    # ---- best point per family = the scaling series ------------------------
    best = []
    for T in sorted(fam):
        b = max(fam[T], key=lambda r: r["fmax_mhz"])
        fs = [r["fmax_mhz"] for r in fam[T]]
        b = dict(b)
        b["spread_mhz"] = round(max(fs) - min(fs), 1)
        b["spread_pct"] = round(100.0 * (max(fs) - min(fs)) / min(fs), 1)
        best.append(b)
    for i, b in enumerate(best):
        if i:
            p = best[i - 1]
            b["dsp_ratio_vs_prev"] = round(float(b["dsps"]) / p["dsps"], 3)
            b["gmac_ratio_vs_prev"] = round(b["gmac_per_s"] / p["gmac_per_s"], 3)
            # < 1 means the extra compute did not pay for itself in full
            b["scaling_efficiency"] = round(
                (b["gmac_per_s"] / p["gmac_per_s"]) /
                (float(b["dsps"]) / p["dsps"]), 3)
        else:
            b["dsp_ratio_vs_prev"] = ""
            b["gmac_ratio_vs_prev"] = ""
            b["scaling_efficiency"] = ""

    scols = cols + ["spread_mhz", "spread_pct", "dsp_ratio_vs_prev",
                    "gmac_ratio_vs_prev", "scaling_efficiency"]
    with io.open(OUT_SCALE, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=scols, lineterminator="\r\n")
        w.writeheader()
        for r in best:
            w.writerow(r)
    print("wrote %s : %d rows\n" % (OUT_SCALE, len(best)))

    print("  WITHIN-FAMILY: which core count wins?\n")
    print("  %-6s %-26s %-14s %s" % ("T", "Fmax at 4 / 8 / 16", "best", "spread"))
    for T in sorted(fam):
        d = {r["cores"]: r["fmax_mhz"] for r in fam[T]}
        s = " / ".join("%.1f" % d[c] if c in d else "  -  " for c in (4, 8, 16))
        b = max(fam[T], key=lambda r: r["fmax_mhz"])
        fs = list(d.values())
        print("  %-6d %-26s %-14s %.1f MHz (%.0f%%)"
              % (T, s, "%s" % b["config"], max(fs) - min(fs),
                 100.0 * (max(fs) - min(fs)) / min(fs)))

    print("\n  SCALING SERIES (best per family)\n")
    print("  %-6s %5s %4s %8s %8s %8s %9s %10s %8s"
          % ("T", "DSP", "PC", "Fmax", "LUT/DSP", "FF/DSP", "GMAC/s",
             "per chan", "scal.eff"))
    for b in best:
        print("  %-6d %5d %4d %8.1f %8.1f %8.1f %9.1f %10.2f %8s"
              % (b["blocks_total"], b["dsps"], b["hbm_pcs"], b["fmax_mhz"],
                 b["lut_per_dsp"], b["ff_per_dsp"], b["gmac_per_s"],
                 b["gmac_per_channel"], b["scaling_efficiency"] or "-"))


if __name__ == "__main__":
    main()

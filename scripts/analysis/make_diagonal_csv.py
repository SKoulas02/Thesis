"""Build results/GEMV_Diagonal_CxB64.csv -- the CORES x BLOCKS = 64 study.

WHAT THIS SWEEP IS. Seven partitionings of the SAME 64 C Blocks, from one core of
64 blocks to 64 cores of one block. Every point is EXTERNALLY IDENTICAL -- 17 HBM
pseudo-channels, 2048/640/1024-bit buses, 128 MACs/cycle, 512 DSPs -- because
every width is a function of the PRODUCT C x B. Only the internal grouping moves.
So throughput is invariant by construction and the only things that can differ
are AREA and FMAX, which is exactly what an OOC sweep measures.

  Fmax = 1000 / (2.222 - WNS)

The 2.222 ns (450 MHz) constraint is deliberately tight enough that every point
MISSES it. A design that MEETS its constraint reports a lower bound, because the
tool stops optimising once it passes; a design that misses reports its true
achieved period. Same method as the ~422 MHz already quoted for 8x8.

WHY failing_endpoints IS REFILLED HERE. The Tcl sweep wrote that column empty --
its regex captured the value but the variable did not survive to the CSV write.
Rather than re-run seven implementations to fix a cosmetic column, it is parsed
back out of the timing reports, which were saved alongside. The reports are the
authority for every timing number in this project anyway.

  VIVADO IMPLEMENTATION IS DETERMINISTIC. 4x16 and 16x4 were each re-run and came
  back BYTE-IDENTICAL -- same WNS, same TNS, same LUT, same FF. So there is no
  run-to-run noise to average out, and every anomaly below is REAL rather than
  bad luck. Getting error bars would need a different seed or directive per
  point, not a repeat.

Run:  python scripts/analysis/make_diagonal_csv.py
"""

import csv
import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # repository root; this file is in scripts/analysis/
DATA = os.path.join(ROOT, "results")
SWEEP = os.path.join(ROOT, "reports", "diagonal_sweep")

SRC = os.path.join(DATA, "diagonal_summary_run1.csv")
OUT = os.path.join(DATA, "GEMV_Diagonal_CxB64.csv")

# The 8x8 reference already in the thesis, from two2N_timing_summary_routed.rpt
# (WNS -0.150 -> 421.6 MHz). Kept here so the discrepancy stays visible.
REF_8x8_FMAX = 421.6

ROW = re.compile(r"^ +(-?[0-9.]+) +(-?[0-9.]+) +([0-9]+) +([0-9]+)")


def failing_endpoints(tag):
    """Recover the column the Tcl sweep left empty, from the saved report."""
    for cand in (os.path.join(SWEEP, "timing_%s.rpt" % tag),
                 os.path.join(SWEEP, "run1", "timing_%s.rpt" % tag)):
        if not os.path.exists(cand):
            continue
        txt = io.open(cand, encoding="utf-8", errors="replace").read()
        i = txt.find("Design Timing Summary")
        if i < 0:
            continue
        for line in txt[i:].split("\n"):
            m = ROW.match(line)
            if m:
                return int(m.group(3))
    return ""


def main():
    rows = list(csv.DictReader(io.open(SRC, newline="", encoding="utf-8")))
    rows.sort(key=lambda r: int(r["cores"]))

    base = {r["cores"]: r for r in rows}
    ref = base["8"]
    ref_f, ref_l, ref_ff = (float(ref["fmax_mhz"]), int(ref["lut_prim"]),
                            int(ref["ff_prim"]))

    out = []
    for r in rows:
        C, B = int(r["cores"]), int(r["blocks"])
        tag = "%dx%d" % (C, B)
        f, lut, ff = float(r["fmax_mhz"]), int(r["lut_prim"]), int(r["ff_prim"])
        out.append(dict(
            config=tag, cores=C, blocks=B,
            # W_IDX is the THIRD generic that has to move with the other two:
            # c_core's W_row port is (W_IDX*EL_SIZE)-1 downto 0 while its
            # generate loop slices ((i+1)*2*EL_SIZE)-1, so they agree only at
            # W_IDX = 2*BLOCKS. Leaving it at 16 gave "array index 287 out of
            # range" at 4x16 and a top-level overrun at 16x4.
            W_IDX=2 * B,
            blocks_total=C * B, macs_per_cycle=2 * C * B,
            wns_ns=float(r["wns_ns"]), tns_ns=float(r["tns_ns"]),
            failing_endpoints=failing_endpoints(tag),
            period_ns=float(r["period_achieved_ns"]),
            fmax_mhz=f,
            fmax_vs_8x8=round(f / ref_f, 4),
            lut_prim=lut, lut_vs_8x8=round(float(lut) / ref_l, 4),
            ff_prim=ff, ff_vs_8x8=round(float(ff) / ref_ff, 4),
            # the model: one 512-bit activation window latched PER CORE, on top
            # of a fixed base. Fitted on the C=1 point.
            ff_model=83131 + 512 * C,
            ff_model_error=ff - (83131 + 512 * C),
            muxf7_prim=int(r["muxf7_prim"]),
            dsp_prim=int(r["dsp_prim"]),
            ramb_prim=int(r["ramb_prim"])))

    cols = ["config", "cores", "blocks", "W_IDX", "blocks_total",
            "macs_per_cycle", "wns_ns", "tns_ns", "failing_endpoints",
            "period_ns", "fmax_mhz", "fmax_vs_8x8", "lut_prim", "lut_vs_8x8",
            "ff_prim", "ff_vs_8x8", "ff_model", "ff_model_error",
            "muxf7_prim", "dsp_prim", "ramb_prim"]

    if not os.path.isdir(DATA):
        os.makedirs(DATA)
    with io.open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\r\n")
        w.writeheader()
        for r in out:
            w.writerow(r)
    print("wrote %s : %d rows x %d cols\n" % (OUT, len(out), len(cols)))

    print("  %-7s %9s %8s %9s %9s %10s %9s" %
          ("config", "Fmax", "vs 8x8", "LUT", "FF", "FF model", "err"))
    for r in out:
        star = "  <-- best" if r["fmax_mhz"] == max(x["fmax_mhz"] for x in out) else ""
        print("  %-7s %8.1f %7.3fx %9s %9s %10s %+9d%s" %
              (r["config"], r["fmax_mhz"], r["fmax_vs_8x8"],
               "{:,}".format(r["lut_prim"]), "{:,}".format(r["ff_prim"]),
               "{:,}".format(r["ff_model"]), r["ff_model_error"], star))

    inv = {c: {r[c] for r in out} for c in ("muxf7_prim", "dsp_prim", "macs_per_cycle")}
    print("\n  invariants across all seven:",
          ", ".join("%s=%s" % (k, v.pop() if len(v) == 1 else "VARIES!")
                    for k, v in inv.items()))
    print("  8x8 here %.1f MHz vs %.1f in the write-up -> %+.1f MHz"
          % (ref_f, REF_8x8_FMAX, ref_f - REF_8x8_FMAX))


if __name__ == "__main__":
    main()

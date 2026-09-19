"""Build results/GEMV_Family_Hardware.csv from the snapshotted in-system reports.

RUN LOCALLY, against reports copied down from the server:

    # server
    cd ~/GEMV_Sparse && tar czf family_hw_reports.tar.gz reports_*
    # local, from the repo root
    scp <server>:/home/skoulas/GEMV_Sparse/family_hw_reports.tar.gz reports/
    mkdir reports/family_hw_reports
    tar xzf reports/family_hw_reports.tar.gz -C reports/family_hw_reports
    python scripts/analysis/make_family_hw_csv.py reports/family_hw_reports
                                         # -> results/GEMV_Family_Hardware.csv

Also runs unchanged on the server (Python 3.6 compatible -- coroni has 3.6.9); with no
results/ at the repository root (two levels above this script), the CSV lands in the
reports base instead.

WHY A PARSER AND NOT NUMBERS TYPED FROM THE CHAT. Two reasons, the second one found
while writing this:

  1. Every number comes from a report file, so every cell is reproducible and none is
     transcribed.

  2. ⚠️ READ THE RIGHT TIMING REPORT. impl_family.cfg uses
     Performance_ExplorePostRoutePhysOpt, which ENABLES post_route_phys_opt_design. In
     that flow, `*_timing_summary_routed.rpt` is the PRE-phys-opt snapshot and the FINAL
     numbers are in `*_timing_summary_postroute_physopted.rpt`. This was established on
     2026-08-26 on this server (INTEGRATION_STEPS.md, S26b: "Comparing the wrong pair
     understates the fix by 346 ps") -- and then every WNS during the family campaign was
     read from `_routed` anyway. Post-route phys-opt only improves or holds slack, so the
     closes still close, but a build recorded as a MISS may in fact have closed.

     This script reads BOTH, uses the post-route-phys-opt report as authoritative, and
     prints the per-build delta so the difference is visible rather than assumed.
     `closed` is decided on the FINAL report only.

FMAX DISCIPLINE, encoded in the `fmax_kind` column:
  * closed  -> achieved_mhz is a LOWER BOUND. The tool stops pushing once it passes.
  * missed  -> achieved_mhz is an ESTIMATE, and the 16x3 series showed it depends on the
               constraint itself (a 345 target achieved a WORSE period than a 350 one).
  Throughput columns are filled ONLY for closed builds: a bitstream that misses timing is
  not a valid operating point, and a blank is honest where a number would not be.
"""

import csv
import glob
import io
import os
import re
import sys

PC_WIDTH = 256

# Every snapshotted report directory that belongs to the family study.
#   role = headline               -> the six-point series
#          floorplan_experiment   -> same design, different die assignment
#          constraint_experiment  -> same design, different --kernel_frequency
RUNS = [
    # tag     C   B  target floorplan report_dir                       role                     note
    ("4x4",   4,  4, 400, "SLR0",  "reports_4x4_400_slr0",          "headline", ""),
    ("8x4",   8,  4, 375, "SLR0",  "reports_8x4_375",               "headline", ""),
    # 16x3's headline is 350, NOT 325. The 350 build was recorded as a miss from the
    # PRE-phys-opt report (-0.002, 1 endpoint); the FINAL report closes it at +0.008.
    # Highest closed frequency is the rule every other row follows. The 325 build is kept
    # below as a constraint experiment. The 350 xclbin ran BIT-EXACT on the card
    # 2026-09-13 (48 rows, 0 mismatches). Its report dir keeps the historical "_missed"
    # name because that is what it is called in the server archive.
    ("16x3", 16,  3, 350, "SLR0",  "reports_16x3_350_missed",       "headline",
     "closed on the FINAL report (+0.008; routed said -0.002); bit-exact on the card"),
    ("8x8",   8,  8, 325, "split", "reports_325",                   "headline",
     "reused pre-campaign build: split floorplan, NO synthesis strategy applied "
     "(run.synth_1 was silently ignored); area comparable -- gemv 66,186 LUT vs 66,183 "
     "with AlternateRoutability"),
    ("4x24",  4, 24, 300, "split", "reports_4x24_300",              "headline", ""),
    ("4x32",  4, 32, 250, "split", "reports_4x32_250",              "headline", ""),

    ("4x4",   4,  4, 400, "split", "reports_4x4_400_split",         "floorplan_experiment",
     "paired with the SLR0 headline: only the die assignment differs"),
    ("8x8",   8,  8, 325, "SLR0",  "reports_8x8_325_slr0_missed",   "floorplan_experiment",
     "the crossover: single-die above T=48"),
    ("16x3", 16,  3, 345, "SLR0",  "reports_16x3_345",              "constraint_experiment",
     "looser than 350 and MISSED, where 350 closed -- achieved period tracks the constraint"),
    ("16x3", 16,  3, 325, "SLR0",  "reports_16x3_325",              "constraint_experiment",
     "rebuilt 'to be sure'; unnecessary once the 350 build was read from the final report"),
]

# OOC Fmax of each family winner: bare engine, 1.5 ns, no synthesis strategy.
OOC_FMAX = {"4x4": 619.6, "8x4": 559.9, "16x3": 501.8, "8x8": 448.6,
            "4x24": 407.3, "4x32": 355.4}

KERNEL_CLK = "clk_out1_pfm_top_clkwiz_kernel_0"
HBM_CLK = "clk_out1_pfm_top_clkwiz_hbm_aclk_0"


def ceildiv(a, b):
    return -(-a // b)


def num(s):
    s = s.strip()
    try:
        f = float(s)
    except ValueError:
        return None
    return int(f) if f.is_integer() else f


def find_one(d, pattern):
    # Top level first, then any depth. The pre-campaign 8x8 snapshot (reports_325) keeps
    # its reports one level down in imp/ -- a flat glob found nothing there and reported
    # the build as NOT CLOSED from missing data, which is a very different claim from a
    # measured miss.
    hits = sorted(glob.glob(os.path.join(d, pattern)))
    if not hits:
        hits = sorted(glob.glob(os.path.join(d, "**", pattern), recursive=True))
    return hits[0] if hits else None


# ---------------------------------------------------------------------------
# timing summary: the Intra Clock Table, scoped so no other section can match
# ---------------------------------------------------------------------------
def parse_intra_clock(path):
    """{clock: dict(wns, tns, fail, total, whs)} from the FIRST Intra Clock Table.

    Reads line by line and stops at the Inter Clock Table, so a 14 MB report costs a
    few hundred lines. A clock name also appears in the Clock Summary (different
    columns) and in the per-path sections -- scoping to this table is what makes the
    positional read safe.
    """
    out = {}
    if not path:
        return out
    inside = False
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not inside:
                if "Intra Clock Table" in line:
                    inside = True
                continue
            if "Inter Clock Table" in line:
                break
            m = re.match(r"^\s*(\S+)\s+(-?[\d.]+\s.*)$", line)
            if not m or m.group(1) not in (KERNEL_CLK, HBM_CLK):
                continue
            vals = [num(v) for v in m.group(2).split()]
            if len(vals) < 5 or any(v is None for v in vals[:5]):
                continue
            out[m.group(1)] = dict(wns=vals[0], tns=vals[1], fail=vals[2],
                                   total=vals[3], whs=vals[4])
    return out


# ---------------------------------------------------------------------------
# kernel utilisation: per-kernel totals
# ---------------------------------------------------------------------------
def parse_kernel_util(path):
    """{kernel: dict(lut, lutram, ff, bram, uram, dsp)}, first table only.

    Column order is POSITIONAL (LUT, LUT-as-memory, REG, BRAM, URAM, DSP). That is
    checked, not trusted: the caller asserts gemv's DSP count equals 8 x T, which a
    shifted column would fail immediately.
    """
    out = {}
    if not path:
        return out
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = re.match(r"^\|\s*(krnl_gemv_sparse|krnl_mm2s|krnl_s2mm)\s*\|(.*)$", line)
            if not m or m.group(1) in out:
                continue
            vals = [num(v) for v in re.findall(r"([\d.]+)\s*\[", m.group(2))]
            if len(vals) >= 6:
                out[m.group(1)] = dict(zip(("lut", "lutram", "ff", "bram", "uram", "dsp"),
                                           vals[:6]))
    return out


# ---------------------------------------------------------------------------
# SLR utilisation: per-die resources and SLL crossings
# ---------------------------------------------------------------------------
def parse_slr_util(path):
    out = {}
    if not path:
        return out
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().split("\n")

    def cells(line):
        return [c.strip() for c in line.strip().strip("|").split("|")]

    order = None            # column index -> SLR name, from section 3's header
    for line in lines:
        if not line.startswith("|"):
            continue
        c = cells(line)
        label = c[0]
        if label == "Total SLLs Used":
            out["sll_total"] = num(c[1])
        elif label == "SLR1 <-> SLR0":
            out["sll_slr0_slr1"] = num(c[1])
        elif label == "SLR2 <-> SLR1":
            out["sll_slr1_slr2"] = num(c[1])
        elif label == "SLR0 -> SLR1":
            out["sll_0to1"] = num(c[1])
        elif label == "SLR1 -> SLR0":
            out["sll_1to0"] = num(c[1])
        elif label == "Site Type" and "SLR0" in c:
            order = c
        elif order and label in ("CLB LUTs", "CLB Registers", "Block RAM Tile", "DSPs"):
            key = {"CLB LUTs": "lut", "CLB Registers": "ff",
                   "Block RAM Tile": "bram", "DSPs": "dsp"}[label]
            for i, name in enumerate(order):
                if i == 0 or i >= len(c):
                    continue
                n = name.replace(" ", "").lower()          # "SLR0" / "SLR0%"
                out["%s_%s" % (n.replace("%", "_pct"), key)] = num(c[i])
    return out


# ---------------------------------------------------------------------------
def main():
    base = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    # In the repo, the CSV belongs in results/ with every other measurement CSV, not
    # inside the reports archive. On the server (no results/ at the repository root, two
    # levels above this script) it falls back to the reports base.
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out_dir = os.path.join(root, "results")
    if not os.path.isdir(out_dir):
        out_dir = base
    out_path = os.path.join(out_dir, "GEMV_Family_Hardware.csv")

    rows, problems = [], []
    for tag, C, B, target, floorplan, rdir, role, note in RUNS:
        d = os.path.join(base, rdir)
        if not os.path.isdir(d):
            problems.append("%-30s MISSING DIRECTORY" % rdir)
            continue

        T = C * B
        w_pcs = ceildiv(T * 32, PC_WIDTH)
        ind_pcs = ceildiv(10 * T + 2, PC_WIDTH)
        c_pcs = ceildiv(T * 16, PC_WIDTH)
        pcs = w_pcs + ind_pcs + 2 + c_pcs
        period = 1000.0 / target

        f_final = find_one(d, "*timing_summary_postroute_physopted.rpt")
        f_routed = find_one(d, "*timing_summary_routed.rpt")
        final = parse_intra_clock(f_final)
        routed = parse_intra_clock(f_routed)
        # Authoritative = post-route phys-opt when it exists (the flow ran it); a build
        # without that stage has only _routed, and then _routed IS final.
        auth = final if KERNEL_CLK in final else routed
        source = "postroute_physopted" if KERNEL_CLK in final else "routed"

        ku = parse_kernel_util(find_one(d, "*kernel_util_routed.rpt"))
        su = parse_slr_util(find_one(d, "*slr_util_routed.rpt"))

        k = auth.get(KERNEL_CLK, {})
        h = auth.get(HBM_CLK, {})
        kr = routed.get(KERNEL_CLK, {})
        wns = k.get("wns")
        closed = (wns is not None and wns >= 0 and k.get("fail") == 0)
        achieved = round(1000.0 / (period - wns), 1) if wns is not None else ""

        g = ku.get("krnl_gemv_sparse", {})
        m = ku.get("krnl_mm2s", {})
        s = ku.get("krnl_s2mm", {})

        # ---- self-checks: a shifted column or a wrong report dir shows up here ----
        if g and g.get("dsp") != 8 * T:
            problems.append("%-30s gemv DSP %s != 8*T = %d  (column order or wrong dir)"
                            % (rdir, g.get("dsp"), 8 * T))
        eng_die = [x for x in ("slr0", "slr1", "slr2") if su.get(x + "_dsp") == 8 * T]
        if su and not eng_die:
            problems.append("%-30s no die holds exactly 8*T = %d DSPs" % (rdir, 8 * T))
        if floorplan == "SLR0" and eng_die and eng_die[0] != "slr0":
            problems.append("%-30s floorplan says SLR0 but engine DSPs are in %s"
                            % (rdir, eng_die[0]))
        if floorplan == "split" and eng_die and eng_die[0] != "slr1":
            problems.append("%-30s floorplan says split but engine DSPs are in %s"
                            % (rdir, eng_die[0]))
        if not ku:
            problems.append("%-30s no kernel_util_routed.rpt -- area columns blank" % rdir)
        if not su:
            problems.append("%-30s no slr_util_routed.rpt -- per-die columns blank" % rdir)

        gmac = round(2 * T * target / 1000.0, 2) if closed else ""
        gpc = round(gmac / pcs, 3) if closed else ""
        mov_lut = (m.get("lut") or 0) + (s.get("lut") or 0) if (m or s) else ""

        rows.append(dict(
            config=tag, family="T=%d" % T, role=role, floorplan=floorplan,
            cores=C, blocks=B, blocks_total=T, macs_per_cycle=2 * T,
            dsps_theory=8 * T, hbm_pcs=pcs, w_pcs=w_pcs, ind_pcs=ind_pcs, a_pcs=2,
            c_pcs=c_pcs, spare_pcs=32 - pcs, w_idx=2 * B, ind_bits=10 * T,
            target_mhz=target, period_ns=round(period, 4),
            timing_source=source,
            kernel_wns_ns=wns, kernel_tns_ns=k.get("tns"),
            kernel_failing_endpoints=k.get("fail"), kernel_total_endpoints=k.get("total"),
            kernel_whs_ns=k.get("whs"),
            kernel_wns_routed_ns=kr.get("wns"), kernel_fail_routed=kr.get("fail"),
            physopt_gain_ps=(round((wns - kr["wns"]) * 1000, 1)
                             if (wns is not None and kr.get("wns") is not None
                                 and source == "postroute_physopted") else ""),
            hbm_wns_ns=h.get("wns"), hbm_failing_endpoints=h.get("fail"),
            closed="yes" if closed else "no",
            achieved_mhz=achieved,
            fmax_kind="lower_bound" if closed else "estimate",
            ooc_fmax_mhz=OOC_FMAX.get(tag, ""),
            target_over_ooc=round(target / OOC_FMAX[tag], 3) if tag in OOC_FMAX else "",
            gemv_lut=g.get("lut"), gemv_lutram=g.get("lutram"), gemv_ff=g.get("ff"),
            gemv_bram=g.get("bram"), gemv_dsp=g.get("dsp"),
            mm2s_count=w_pcs + ind_pcs + 2, mm2s_lut=m.get("lut"), mm2s_ff=m.get("ff"),
            mm2s_bram=m.get("bram"),
            s2mm_count=c_pcs, s2mm_lut=s.get("lut"), s2mm_ff=s.get("ff"),
            s2mm_bram=s.get("bram"),
            movers_lut=mov_lut,
            gemv_lut_per_dsp=(round(float(g["lut"]) / g["dsp"], 1)
                              if g.get("lut") and g.get("dsp") else ""),
            engine_die=eng_die[0].upper() if eng_die else "",
            slr0_lut=su.get("slr0_lut"), slr1_lut=su.get("slr1_lut"),
            slr2_lut=su.get("slr2_lut"),
            slr0_bram=su.get("slr0_bram"), slr1_bram=su.get("slr1_bram"),
            slr2_bram=su.get("slr2_bram"), slr0_bram_pct=su.get("slr0_pct_bram"),
            slr0_dsp=su.get("slr0_dsp"), slr1_dsp=su.get("slr1_dsp"),
            slr2_dsp=su.get("slr2_dsp"),
            sll_total=su.get("sll_total"), sll_slr0_slr1=su.get("sll_slr0_slr1"),
            sll_slr1_slr2=su.get("sll_slr1_slr2"),
            sll_slr0_to_slr1=su.get("sll_0to1"), sll_slr1_to_slr0=su.get("sll_1to0"),
            gmac_per_s=gmac, gmac_per_hbm_pc=gpc,
            dsps_per_hbm_pc=round(8.0 * T / pcs, 2),
            report_dir=rdir, note=note))

    cols = list(rows[0].keys()) if rows else []
    with io.open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\r\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in r.items()})

    print("wrote %s : %d rows x %d cols\n" % (out_path, len(rows), len(cols)))
    print("  %-5s %-22s %-6s %4s  %-19s %8s %8s %6s %7s  %-6s %6s %7s"
          % ("cfg", "role", "floor", "tgt", "source", "WNS", "routed",
             "gain", "fail", "closed", "GMAC/s", "per PC"))
    def b(v):
        return "" if v is None else v

    for r in rows:
        print("  %-5s %-22s %-6s %4d  %-19s %8s %8s %6s %7s  %-6s %6s %7s"
              % (r["config"], r["role"], r["floorplan"], r["target_mhz"],
                 r["timing_source"], b(r["kernel_wns_ns"]), b(r["kernel_wns_routed_ns"]),
                 b(r["physopt_gain_ps"]), b(r["kernel_failing_endpoints"]), r["closed"],
                 b(r["gmac_per_s"]), b(r["gmac_per_hbm_pc"])))

    flips = [r for r in rows
             if r["timing_source"] == "postroute_physopted"
             and r["kernel_wns_routed_ns"] is not None and r["kernel_wns_ns"] is not None
             and (r["kernel_wns_routed_ns"] < 0) != (r["kernel_wns_ns"] < 0)]
    if flips:
        # ASCII only in anything printed: a non-UTF-8 console (Windows cp1252, or the
        # server's Python 3.6 with LANG unset) raises UnicodeEncodeError on an emoji.
        print("\n  !! VERDICT CHANGED by post-route phys-opt (routed said one thing, final another):")
        for r in flips:
            print("     %-5s @ %d MHz  routed %+.3f -> FINAL %+.3f"
                  % (r["config"], r["target_mhz"], r["kernel_wns_routed_ns"],
                     r["kernel_wns_ns"]))

    if problems:
        print("\n  CHECKS:")
        for p in problems:
            print("   ", p)
    else:
        print("\n  all self-checks passed (gemv DSP = 8T, engine on the expected die)")


if __name__ == "__main__":
    main()

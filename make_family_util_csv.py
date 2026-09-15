"""Build results/GEMV_Family_Utilization.csv -- how much of the U280 each family build uses.

    python make_family_util_csv.py [family_hw_reports]

Inputs : family_hw_reports/<report_dir>/   snapshotted in-system reports, headline builds only
         results/GEMV_Family_Hardware.csv   hbm_pcs per family (cross-checked, not trusted)
Output : results/GEMV_Family_Utilization.csv   WIDE, 6 rows (one per family, size order)

TWO CHARTS, ONE SHEET, NO DUPLICATED COLUMN:
    D:G  lut / ff / bram / dsp _util_pct     -> the AREA chart, one bar per resource type
    G:H  dsp_util_pct, hbm_channel_util_pct  -> DSP vs HBM-channel utilisation
Column G (DSP) is shared on purpose, so both chart blocks stay contiguous.

THE NUMERATOR IS THE WHOLE BUILD AND THE DENOMINATOR IS THE WHOLE U280. Every resource
percentage is Used / Available from Vivado's full utilisation report
(`*full_util_routed.rpt`, sections 1, 3 and 4), so it includes the platform shell:
  * The shell is not a constant to subtract: its LUTs grow from ~108k at 6 HBM channels to
    ~149k at 32 (kernel report "Platform" row). What the CARD spends on a configuration is
    the whole build.
  * DSPs therefore include the platform's 4: dsp_used = 8T + 4, asserted below. The same
    definition is used in both charts, so the DSP bar is the same number in each.
  * Block RAM counts a RAMB18 as half a tile, as Vivado does.
  * Available is what Vivado reports (LUTs: 1,303,680 on the device minus 960 prohibited).
HBM channels: pseudo-channels used / 32, all the U280 has (all in SLR0; the shell uses none).

Kernel-only counts (platform vs user kernels, from `*kernel_util_routed.rpt`) are kept as
reference columns for the text, never charted against the whole-build percentages.
"""

import csv
import io
import os
import re
import sys

import make_family_hw_csv as hw

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "results")
OUT = os.path.join(RES, "GEMV_Family_Utilization.csv")
HW_CSV = os.path.join(RES, "GEMV_Family_Hardware.csv")

HBM_PCS_ON_CARD = 32
PLATFORM_DSPS = 4

# Vivado full-utilisation site type -> column key
SITES = {"CLB LUTs": "lut", "CLB Registers": "ff", "Block RAM Tile": "bram", "DSPs": "dsp"}


def parse_full_util(path):
    """{lut|ff|bram|dsp: dict(used, available, pct)} from the FIRST row of each site type.

    First occurrence matters: "CLB Registers" appears again in section 2 (CLB Logic
    Distribution). Columns are found through the table's own header row
    (Site Type | Used | Fixed | Prohibited | Available | Util%), not by position.
    """
    out, header = {}, None
    if not path:
        return out
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if cells[0] == "Site Type":
                header = cells
                continue
            key = SITES.get(cells[0])
            if key is None or key in out or header is None:
                continue
            col = dict(zip(header, cells))
            out[key] = dict(used=hw.num(col.get("Used", "")),
                            available=hw.num(col.get("Available", "")),
                            pct=hw.num(col.get("Util%", "")))
            if len(out) == len(SITES):
                break
    return out


def parse_system_rows(path):
    """LUT counts of the kernel report's "Platform" and "Used Resources" rows."""
    out = {}
    if not path:
        return out
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = re.match(r"^\|\s*(Platform|Used Resources)\s*\|(.*)$", line)
            if not m or m.group(1) in out:
                continue
            vals = re.findall(r"([\d.]+)\s*\[", m.group(2))
            if vals:
                out[m.group(1)] = hw.num(vals[0])
    return out


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "family_hw_reports")
    with io.open(HW_CSV, newline="", encoding="utf-8") as fh:
        hwrows = {r["config"]: r for r in csv.DictReader(fh) if r["role"] == "headline"}

    rows, problems, avail_seen = [], [], {}
    for tag, C, B, target, floorplan, rdir, role, note in hw.RUNS:
        if role != "headline":
            continue
        d = os.path.join(base, rdir)
        T = C * B
        full = parse_full_util(hw.find_one(d, "*full_util_routed.rpt"))
        sysrows = parse_system_rows(hw.find_one(d, "*kernel_util_routed.rpt"))
        if len(full) != len(SITES):
            problems.append("%-26s full_util_routed.rpt missing or incomplete: %s"
                            % (rdir, sorted(full)))
            continue

        # ---- self-checks: a misread column or the wrong report shows up here ----
        for key, v in full.items():
            calc = 100.0 * v["used"] / v["available"]
            if v["pct"] is None or abs(calc - v["pct"]) > 0.006:
                problems.append("%-26s %s: %s/%s = %.3f%% but the report prints %s%%"
                                % (rdir, key, v["used"], v["available"], calc, v["pct"]))
            avail_seen.setdefault(key, set()).add(v["available"])
        if full["dsp"]["used"] != 8 * T + PLATFORM_DSPS:
            problems.append("%-26s DSPs used %s != 8T + %d = %d"
                            % (rdir, full["dsp"]["used"], PLATFORM_DSPS, 8 * T + PLATFORM_DSPS))
        pcs = (hw.ceildiv(32 * T, hw.PC_WIDTH) + hw.ceildiv(10 * T + 2, hw.PC_WIDTH) + 2
               + hw.ceildiv(16 * T, hw.PC_WIDTH))
        h = hwrows.get(tag)
        if h is None or int(h["hbm_pcs"]) != pcs or int(h["target_mhz"]) != target:
            problems.append("%-26s GEMV_Family_Hardware.csv disagrees (hbm_pcs %s vs %d)"
                            % (rdir, h and h["hbm_pcs"], pcs))
        plat, kern = sysrows.get("Platform"), sysrows.get("Used Resources")
        if plat is not None and kern is not None:
            dev = abs(plat + kern - full["lut"]["used"]) / float(full["lut"]["used"])
            if dev > 0.001:
                problems.append("%-26s platform %d + kernels %d LUTs vs whole build %d (%.2f%%)"
                                % (rdir, plat, kern, full["lut"]["used"], 100 * dev))

        pct = {k: round(100.0 * v["used"] / v["available"], 2) for k, v in full.items()}
        rows.append(dict(
            family="%s (T=%d)" % (tag, T), config=tag, blocks_total=T,
            # ---- D:G the area chart, G:H the DSP vs HBM-channel chart ----
            lut_util_pct=pct["lut"], ff_util_pct=pct["ff"], bram_util_pct=pct["bram"],
            dsp_util_pct=pct["dsp"],
            hbm_channel_util_pct=round(100.0 * pcs / HBM_PCS_ON_CARD, 3),
            # ---- reference: the counts behind every percentage ----
            lut_used=full["lut"]["used"], ff_used=full["ff"]["used"],
            bram_used=full["bram"]["used"], dsp_used=full["dsp"]["used"],
            hbm_channels_used=pcs,
            lut_available=full["lut"]["available"], ff_available=full["ff"]["available"],
            bram_available=full["bram"]["available"], dsp_available=full["dsp"]["available"],
            hbm_channels_available=HBM_PCS_ON_CARD,
            platform_lut=plat, kernels_lut=kern, engine_dsp=8 * T,
            report_dir=rdir))

    for key, seen in sorted(avail_seen.items()):
        if len(seen) != 1:
            problems.append("device capacity for %s differs between builds: %s"
                            % (key, sorted(seen)))

    if not rows:
        raise SystemExit("no rows -- is %s the report archive?" % base)
    print("  %-12s %7s %7s %7s %7s | %7s" % ("family", "LUT%", "FF%", "BRAM%", "DSP%", "HBM ch%"))
    for r in rows:
        print("  %-12s %7.2f %7.2f %7.2f %7.2f | %7.2f"
              % (r["family"], r["lut_util_pct"], r["ff_util_pct"], r["bram_util_pct"],
                 r["dsp_util_pct"], r["hbm_channel_util_pct"]))
    # A failed check writes NOTHING: make_chart_xlsx.py picks up whatever CSV is on disk,
    # so a half-right file would reach the workbook silently.
    if problems:
        print("\n  CHECKS FAILED -- %s NOT written:" % os.path.basename(OUT))
        for p in problems:
            print("   ", p)
        raise SystemExit(1)

    with io.open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), lineterminator="\r\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in r.items()})
    print("\nwrote %s : %d rows x %d cols" % (OUT, len(rows), len(rows[0])))
    print("  all self-checks passed (Util%% matches the report, DSPs = 8T + %d, one device"
          " capacity, hbm_pcs agrees with GEMV_Family_Hardware.csv, platform + kernels ="
          " whole build within 0.1%%)" % PLATFORM_DSPS)


if __name__ == "__main__":
    main()

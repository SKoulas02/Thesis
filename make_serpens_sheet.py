"""Add the "Serpens" sheet to results/GEMV_Chart_Data.xlsx -- BY APPENDING.

WHY THIS IS A SEPARATE SCRIPT AND NOT A LINE IN make_chart_xlsx.py
==================================================================
make_chart_xlsx.py starts with Workbook() and rebuilds the file FROM SCRATCH.
Running it over a workbook a human has drawn charts into destroys every chart --
that has already happened once here, to eleven charts. This script instead does
load_workbook() + create_sheet(), so everything already in the file survives.

It still REFUSES to run if the target workbook contains charts, because openpyxl
cannot round-trip them: it does not read chart XML, so any load/save cycle drops
charts even when the code never touches them. The guard below is the rule from
never-regenerate-over-hand-built-artefacts.md made executable.

  results/GEMV_Chart_Data.xlsx   generated, disposable, 0 charts -> safe target
  results/GEMV_Charts_Final.xlsx the user's, 22 charts -> NEVER open with openpyxl

If a rebuild is ever run, this sheet disappears with everything else. Re-run this
script afterwards.

WHAT THE SHEET CONTAINS
=======================
The two per-clock comparison CSVs joined into one WIDE, chart-ready table: one
row per Serpens matrix, with 300 MHz and 325 MHz side by side, so a grouped bar
chart is a plain column selection rather than a join.

    results/GEMV_vs_Serpens.csv          -> the _300 columns
    results/GEMV_vs_Serpens_325MHz.csv   -> the _325 columns

Both are produced by make_serpens_csv.py from MEASURED H1 rows. The last row is
the geometric mean; its size-dependent cells are blank on purpose, exactly as on
the Shapes sheet.

COLUMN ORDER IS APPEND-ONLY. Once a chart points at this sheet, inserting a
column silently re-points every series -- Excel stores absolute references.
`data_source` stays LAST.

Run:  python make_serpens_sheet.py
      python make_serpens_sheet.py --dry-run     # print, write nothing
"""

import argparse
import csv
import io
import os
import zipfile

from openpyxl import load_workbook

from make_chart_xlsx import (OUT, H_FILL, H_FONT, BOX, as_number, _data)
from openpyxl.styles import Alignment
from openpyxl.utils import get_column_letter

SHEET = "Serpens"

SRC_300 = "GEMV_vs_Serpens.csv"
SRC_325 = "GEMV_vs_Serpens_325MHz.csv"

# (output column, source CSV field, which file). None = filled in by hand below.
TEXT_COLS = {"serpens_id", "label", "matrix", "data_source"}


def has_charts(path):
    """True if the workbook holds chart XML. openpyxl cannot preserve it."""
    if not os.path.exists(path):
        return False
    with zipfile.ZipFile(path) as z:
        return any("/charts/" in n for n in z.namelist())


def locked(path):
    """Excel leaves a ~$name.xlsx stub while the file is open."""
    d, b = os.path.split(path)
    return os.path.exists(os.path.join(d, "~$" + b))


def read_rows(name):
    p = _data(name)
    if not os.path.exists(p):
        raise SystemExit(
            "missing {}\nGenerate it first:\n"
            "    python make_serpens_csv.py --headline H1 "
            "--shapes-csv results/shapes_H1_300MHz.csv".format(p))
    with io.open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build():
    a300 = read_rows(SRC_300)
    a325 = read_rows(SRC_325)
    by325 = dict((r["serpens_id"], r) for r in a325)

    cols = ["row_order", "serpens_id", "label", "matrix", "nnz",
            "serpens_ms", "thesis_ms_300", "thesis_ms_325",
            "speedup_raw_300", "speedup_raw_325",
            "speedup_compute_300", "speedup_compute_325",
            "serpens_occupancy", "thesis_occupancy_300", "thesis_occupancy_325",
            "serpens_stream_pct", "data_source"]

    out = []
    for r in a300:
        sid = r["serpens_id"]
        q = by325.get(sid, {})
        out.append({
            "row_order": r["row_order"],
            "serpens_id": sid,
            # The category axis label. The geomean row has no matrix name, and a
            # blank category renders as an empty tick -- so it is named here.
            "label": ("GEOMEAN" if not r["matrix"]
                      else "{} {}".format(sid, r["matrix"])),
            "matrix": r["matrix"],
            "nnz": r["nnz"],
            "serpens_ms": r["serpens_ms"],
            "thesis_ms_300": r["thesis_ms"],
            "thesis_ms_325": q.get("thesis_ms", ""),
            "speedup_raw_300": r["speedup_raw"],
            "speedup_raw_325": q.get("speedup_raw", ""),
            "speedup_compute_300": r["speedup_compute_only"],
            "speedup_compute_325": q.get("speedup_compute_only", ""),
            "serpens_occupancy": r["serpens_occupancy"],
            "thesis_occupancy_300": r["thesis_occupancy"],
            "thesis_occupancy_325": q.get("thesis_occupancy", ""),
            "serpens_stream_pct": r["serpens_stream_pct"],
            # Both estimators in one string: this sheet mixes two clocks, and the
            # estimator must travel with the numbers or it will be lost.
            "data_source": "300: {} | 325: {}".format(
                r["data_source"], q.get("data_source", "")),
        })
    return cols, out


def write(cols, rows, wb):
    if SHEET in wb.sheetnames:
        del wb[SHEET]                       # replace, never duplicate
    ws = wb.create_sheet(SHEET)

    for i, name in enumerate(cols, start=1):
        c = ws.cell(row=1, column=i, value=name)
        c.fill, c.font, c.border = H_FILL, H_FONT, BOX
        c.alignment = Alignment(horizontal="center", vertical="center",
                                wrap_text=True)
    ws.row_dimensions[1].height = 30

    widest = [len(c) for c in cols]
    for r, row in enumerate(rows, start=2):
        for i, key in enumerate(cols, start=1):
            raw = (row.get(key) or "").strip()
            if not raw:
                continue                    # blank stays blank, never 0
            num = None if key in TEXT_COLS else as_number(raw)
            cell = ws.cell(row=r, column=i, value=raw if num is None else num)
            cell.border = BOX
            if isinstance(num, int) and abs(num) >= 1000:
                cell.number_format = "#,##0"
            widest[i - 1] = max(widest[i - 1], len(raw))

    for i, w in enumerate(widest, start=1):
        ws.column_dimensions[get_column_letter(i)].width = min(30, max(10, w + 2))

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = "A1:{}{}".format(
        get_column_letter(len(cols)), len(rows) + 1)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workbook", default=OUT)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cols, rows = build()

    print("sheet '{}': {} rows x {} cols".format(SHEET, len(rows), len(cols)))
    print("  %-5s %-18s %9s %9s %9s %8s %8s" %
          ("id", "matrix", "their ms", "300 ms", "325 ms", "cmp300", "cmp325"))
    for r in rows:
        print("  %-5s %-18s %9s %9s %9s %8s %8s" %
              (r["serpens_id"], r["matrix"][:18], r["serpens_ms"],
               r["thesis_ms_300"], r["thesis_ms_325"],
               r["speedup_compute_300"], r["speedup_compute_325"]))

    if a.dry_run:
        print("\n--dry-run: nothing written")
        return

    if not os.path.exists(a.workbook):
        raise SystemExit("no workbook at {} -- run make_chart_xlsx.py first"
                         .format(a.workbook))
    if locked(a.workbook):
        raise SystemExit(
            "{} is OPEN in Excel (a ~$ stub is present). Close it and re-run; "
            "writing now would fail or leave the stub staged in git."
            .format(a.workbook))
    if has_charts(a.workbook):
        raise SystemExit(
            "REFUSING: {} contains charts.\n"
            "openpyxl does not read chart XML, so loading and saving this file "
            "would DELETE every chart in it. Add the sheet by hand in Excel, or "
            "point --workbook at a workbook with no charts."
            .format(a.workbook))

    wb = load_workbook(a.workbook)
    before = list(wb.sheetnames)
    write(cols, rows, wb)
    wb.save(a.workbook)

    print("\nappended to {}".format(a.workbook))
    print("  sheets before: {}".format(", ".join(before)))
    print("  sheets after : {}".format(", ".join(wb.sheetnames)))


if __name__ == "__main__":
    main()

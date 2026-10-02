"""HBM channels and DSPs of every configuration: single engines, multi-tenant builds with
their own activation channels, and multi-tenant builds that share one activation vector.

    python scripts/analysis/make_hbm_channels_csv.py

Inputs : workload/plan_workload.py               the 13 measured builds and channels_of()
         results/GEMV_Family_Utilization.csv     measured DSPs of the six single engines
         reports/reports_multi_tenant.tar.gz     measured DSPs of the multi-tenant builds
                                                 (impl_1_full_util_routed.rpt); optional
Output : results/GEMV_HBM_Channels.csv           one row per configuration

CHANNELS PER ENGINE (T = cores x blocks lanes, 256-bit pseudo-channels):
  weights  ceil(32 T / 256)        indices  ceil((10 T + 2) / 256)   (+2: the sparsity code)
  vector   2 (the 32-element window is 512 bits)                      outputs  ceil(16 T / 256)
Own vector channels: every engine brings its 2. Shared vector: the whole build has 2, read by
every engine's activation movers (the professor's MoE layout, builds generated 2026-09-28).

DSPs: 8 per lane (2 multipliers, 1 adder, 1 accumulator per C Block) x lanes x engines, plus 4
used by the platform. The data movers use none. The formula is CHECKED against every measured
build and the script stops if one disagrees; the shared builds are not built yet, so their
DSPs are the formula (column dsp_source says which).

MOVERS: one per channel an engine reads or writes, and every mover has its own AXI port on
the HBM subsystem, which has 32 for kernels (33 minus the platform's). v++ does not share a
port between movers reading the same channel, and with a shared vector every engine still
keeps its own two activation movers -- so 6 x 4x4, 7 x 4x4 and 2 x 8x8 shared fit the 32
CHANNELS but not the 32 PORTS (6 x 4x4 failed to link on exactly this, 2026-09-28).
"""

import csv
import io
import os
import re
import sys
import tarfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # repository root; this file is in scripts/analysis/
RESULTS = os.path.join(ROOT, "results")
sys.path.insert(0, os.path.join(ROOT, "workload"))
import plan_workload as pw                      # noqa: E402

OUT = os.path.join(RESULTS, "GEMV_HBM_Channels.csv")
FAMILY_UTIL = os.path.join(RESULTS, "GEMV_Family_Utilization.csv")
MT_REPORTS = os.path.join(ROOT, "reports", "reports_multi_tenant.tar.gz")
HBM_CHANNELS = 32
HBM_KERNEL_PORTS = 32                           # hmss_0: 33 connections, 1 for the platform
STACK = 16                                      # channels 0-15 = HBM stack 0
DSP_TOTAL = 9024                                # XCU280
PLATFORM_DSP = 4

# the report folder of each measured multi-tenant bitstream in reports_multi_tenant.tar.gz
MT_REPORT_DIR = {
    "2x4x4": "reports_multi_x2_400", "3x4x4": "reports_multi_4x4_x3_400",
    "4x4x4": "reports_multi_x4_375", "5x4x4": "reports_multi_x5_375_split",
    "2x8x4": "reports_multi_8x4_x2_375", "3x8x4": "reports_multi_8x4_x3_375",
    "2x16x3": "reports_multi_16x3_x2_375",
}

# the shared-vector builds (decided 2026-09-28): engine shape, engines
SHARED = [("4x4", 3), ("4x4", 6), ("4x4", 7), ("8x4", 3), ("8x8", 2), ("16x3", 2)]

# display order: single engines, then each engine shape's multi-tenant builds with their
# shared twins next to them
ORDER = [("single", "4x4", 1), ("single", "8x4", 1), ("single", "16x3", 1),
         ("single", "8x8", 1), ("single", "4x24", 1), ("single", "4x32", 1),
         ("own", "4x4", 2), ("own", "4x4", 3), ("shared", "4x4", 3), ("own", "4x4", 4),
         ("own", "4x4", 5), ("shared", "4x4", 6), ("shared", "4x4", 7),
         ("own", "8x4", 2), ("own", "8x4", 3), ("shared", "8x4", 3),
         ("own", "16x3", 2), ("shared", "16x3", 2), ("shared", "8x8", 2)]


def measured_dsps():
    """{(shape, engines): DSPs} from the reports; multi-tenant ones only if the archive exists."""
    got = {}
    with io.open(FAMILY_UTIL, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            got[(r["config"], 1)] = int(r["dsp_used"])
    if os.path.exists(MT_REPORTS):
        want = dict((d + "/imp/impl_1_full_util_routed.rpt", a) for a, d in MT_REPORT_DIR.items())
        with tarfile.open(MT_REPORTS, "r:gz") as tar:
            for m in tar:
                if m.name in want:
                    text = tar.extractfile(m).read().decode("utf-8", "replace")
                    row = re.search(r"^\|\s*DSPs\s*\|\s*(\d+)\s*\|", text, re.M)
                    name, shape, tenants = pw.ARCH[want[m.name]][:3]
                    got[(shape, tenants)] = int(row.group(1))
    return got


def main():
    clocks = dict(((a[1], a[2]), (a[0], a[3])) for a in pw.ARCHS)   # (shape, n) -> (arch, MHz)
    measured = measured_dsps()
    rows = []
    for kind, shape, n in ORDER:
        w, ind, a, c = pw.channels_of(shape)
        cores, blocks = pw.SHAPE_OF[shape]
        lanes = cores * blocks
        vec = a if kind == "shared" else n * a
        total = n * (w + ind + c) + vec
        if total > HBM_CHANNELS:
            raise SystemExit("%s x %s (%s): %d channels -- does not fit" % (n, shape, kind, total))
        engine_dsp = 8 * lanes * n
        dsp = engine_dsp + PLATFORM_DSP
        meas = measured.get((shape, n)) if kind != "shared" else None
        if meas is not None and meas != dsp:
            raise SystemExit("%s x %s: measured %d DSPs, the formula gives %d" % (n, shape, meas, dsp))
        if kind != "shared" and meas is None:
            print("note: no measured DSPs for %d x %s (report archive missing?)" % (n, shape))
        label = ("%s" % shape if kind == "single" else "%d x %s" % (n, shape)) \
            + (" shared" if kind == "shared" else "") + " (%d ch)" % total
        twin = ""
        if kind == "shared" and (shape, n) in clocks:
            twin = "%d x %s (%d ch)" % (n, shape, n * (w + ind + c + a))
        built = kind != "shared"
        movers = n * (w + ind + a + c)          # a shared vector still has 2 movers per engine
        if built:
            status = "built and measured"
        elif movers > HBM_KERNEL_PORTS:
            status = "cannot link: %d movers > %d HBM kernel ports" % (movers, HBM_KERNEL_PORTS)
        else:
            status = "to build"
        rows.append(dict(
            config_label=label,
            kind={"single": "single engine", "own": "multi-tenant, own vector channels",
                  "shared": "multi-tenant, shared vector channels"}[kind],
            engine_shape=shape, engines=n, lanes_total=lanes * n,
            weights_ch=n * w, indices_ch=n * ind, activation_ch=vec, output_ch=n * c,
            total_ch=total, unused_ch=HBM_CHANNELS - total, movers=movers,
            hbm_ports_free=HBM_KERNEL_PORTS - movers,
            uses_second_stack="yes" if total > STACK else "no",
            saved_by_sharing=(n - 1) * a if kind == "shared" else 0,
            shared_twin_of=twin,
            engine_dsp=engine_dsp, build_dsp=dsp,
            dsp_pct=round(100.0 * dsp / DSP_TOTAL, 2),
            dsp_source="measured (routed utilization report)" if meas is not None else
                       "formula: 8 per lane x lanes + 4 platform (not built yet)",
            clock_mhz=clocks[(shape, n)][1] if built else "",
            status=status))
    with io.open(OUT, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n")
        wr.writeheader()
        wr.writerows(rows)
    print("%-26s %3s %3s %3s %3s %4s %5s %7s %5s  %s" % ("configuration", "W", "I", "A", "C",
                                                         "sum", "free", "movers", "DSP", "status"))
    for r in rows:
        print("%-26s %3d %3d %3d %3d %4d %5d %7d %5d  %s" % (
            r["config_label"], r["weights_ch"], r["indices_ch"], r["activation_ch"],
            r["output_ch"], r["total_ch"], r["unused_ch"], r["movers"], r["build_dsp"],
            r["status"]))
    print("wrote %s (%d configurations)" % (os.path.relpath(OUT, ROOT), len(rows)))


if __name__ == "__main__":
    main()

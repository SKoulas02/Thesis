#!/bin/bash
# run_all_workloads_bcast.sh -- the MoE workload on every BROADCAST-VECTOR build, one after
# another, stopping at the first failure.
#
#   bash ~/GEMV_Sparse/workload_tools/run_all_workloads_bcast.sh              # every one with a DATA_CLK
#   bash ~/GEMV_Sparse/workload_tools/run_all_workloads_bcast.sh 7x4x4_bcast  # only these
#   bash ~/GEMV_Sparse/workload_tools/run_all_workloads_bcast.sh --check      # preflight only
#   bash ~/GEMV_Sparse/workload_tools/run_all_workloads_bcast.sh --gate-only  # gates only
#
# A COPY OF run_all_workloads.sh (2026-09-28), edited; run_all_workloads.sh is untouched and
# still runs every other build. What differs, and only this: MoE is the only mode (--moe is
# accepted and implied); the builds come from plan_workload_bcast.py (BCAST_ARCHS, those
# whose DATA_CLK is recorded); the host is host_workload_bcast.cpp, the runner
# run_workload_bcast.py; the log is workload_chain_moebcast_<date>.log and the CSVs
# workload_moebcast_*, copied home into results/workload_moe_bcast/.
#
# Everything below is run_all_workloads.sh's description, still true for the MoE mode.
#
# run_all_workloads.sh -- the workload on every architecture, one after another,
# stopping at the first failure.
#
#   bash ~/GEMV_Sparse/workload_tools/run_all_workloads.sh            # all 13
#   bash ~/GEMV_Sparse/workload_tools/run_all_workloads.sh 3x8x4 4x32 # only these
#   bash ~/GEMV_Sparse/workload_tools/run_all_workloads.sh --check    # preflight only
#   bash ~/GEMV_Sparse/workload_tools/run_all_workloads.sh --per-matrix [--check] [names]
#   bash ~/GEMV_Sparse/workload_tools/run_all_workloads.sh --moe [--check] [names]
#   bash ~/GEMV_Sparse/workload_tools/run_all_workloads.sh --moe --gate-only [names]
#
# --gate-only: build the host and run ONLY the correctness gate of each selected build (no
# measurement, no quiet server needed) -- the check for a freshly finished bitstream.
#
# --per-matrix: every matrix its own calculation with its own input vector (the multi-user
# inference case); without it, matrices of one width share a calculation and its vector
# (the 2026-09-25 run). The CSVs are named workload_permatrix_* in that mode.
# --moe: one token through 11 layers, one MIXED expert per engine per layer, engines in
# lockstep; runs on the 13 builds AND every shared-vector build whose DATA_CLK is recorded in
# plan_workload.SHARED_ARCHS (those read ONE vector copy). CSVs workload_moe_*.
#
# PREFLIGHT, before anything runs: every selected architecture's folder and .xclbin must
# exist and the .xclbin must run at the clock plan_workload.py expects (its DATA_CLK), and
# every name given must be one of the 13. Any problem stops the chain before the first run.
#
# For each architecture of plan_workload.py (in its order): go to its build folder under
# $GEMV_ROOT (default ~/GEMV_Sparse), check the .xclbin is there, build host_workload for
# its shape, run the correctness gate (small golden-checked calculations, every tenant
# switching between several of them), then the measurement (3 timed passes and a 60 s
# power soak of whole passes, then 20 s of idle power). A few minutes per architecture,
# most of it the soak and the idle window.
#
# XRT is always sourced and power telemetry must work before anything runs (a missing
# xbutil once cost a whole chain its energy data). Everything is logged to
# $GEMV_ROOT/workload_chain_<date>.log. The CSVs stay in each build folder; the last line
# printed is the scp that brings them all home.
#
# The whole body is one function, read before anything runs: bash otherwise reads a script
# from disk as it goes, and copying a new version over it mid-chain would run garbage.

set -o pipefail
TOOLS=$(cd "$(dirname "$0")" && pwd)
ROOT=${GEMV_ROOT:-$HOME/GEMV_Sparse}

die() {
    echo
    echo "STOPPED: $*"
    echo "If a host run hung, reset the card before anything else:"
    echo "  pkill -u \$USER -f host_workload; sleep 2; xbutil reset --device ${BDF:-0000:af:00.1}"
    echo "Resume from any architecture by naming it (and the ones after it) as arguments${FLAGS:+, after $FLAGS}."
    exit 1
}

main() {
CHECK=""
GATE_ONLY=""
VEC=moe
FLAGS=""
while [ $# -gt 0 ]; do
    case "$1" in
        --check) CHECK=1; shift ;;
        --gate-only) GATE_ONLY=1; FLAGS="--gate-only"; shift ;;
        --moe) shift ;;                                   # the only mode; accepted
        --*) die "unknown option $1 (--check, --gate-only; MoE is the only mode)" ;;
        *) break ;;
    esac
done
TAG=moebcast_
LOG="$ROOT/workload_chain_${TAG}$(date +%Y%m%d_%H%M).log"
exec > >(tee -a "$LOG") 2>&1

[ -f /opt/xilinx/xrt/setup.sh ] && source /opt/xilinx/xrt/setup.sh >/dev/null 2>&1
[ -n "$XILINX_XRT" ] || die "XRT environment not available"
command -v xbutil >/dev/null || die "xbutil is not on PATH -- no power data would be recorded"
export BDF=${BDF:-0000:af:00.1}
export EMU=${EMU:-$ROOT/GEMV_4.0_Source/Emulation}
[ -f "$EMU/gen_timing_stimulus.py" ] || die "no stimulus scripts in $EMU"

# name shape tenants folder xclbin cores blocks, one architecture per line: every broadcast
# build whose DATA_CLK is recorded in plan_workload_bcast.BCAST_ARCHS
LIST=$(cd "$TOOLS" && python3 -c "
import plan_workload_bcast as p
for n, s, t, clk, folder, x in p.archs_for('$VEC'):
    c, b = p.SHAPE_OF[s]
    print(n, s, t, folder, x, c, b)
" | tr -d '\r') || die "cannot read the architecture list from plan_workload_bcast.py"

echo "preflight (build folders under $ROOT, vectors $VEC, broadcast builds):"
python3 - "$TOOLS" "$ROOT" "$VEC" "$@" <<'EOF' || die "preflight -- fix the file, or the name/clock in BCAST_ARCHS of plan_workload_bcast.py"
import os, sys
sys.path.insert(0, sys.argv[1])
import plan_workload_bcast as p
import run_workload_bcast as r
root, vec, names = sys.argv[2], sys.argv[3], sys.argv[4:]
bad = [n for n in names if n not in p.BCAST_NAMES]
for n in bad:
    print("  UNKNOWN   %s  (broadcast builds: %s; every other build: run_all_workloads.sh)"
          % (n, " ".join(sorted(p.BCAST_NAMES))))
selected = [p.ARCH[n] for n in names if n in p.BCAST_NAMES] if names else p.archs_for(vec)
if not selected and not bad:
    print("  NOTHING TO RUN: no broadcast build has its DATA_CLK recorded in BCAST_ARCHS yet")
    bad.append("-")
for n, s, t, clk, folder, x in selected:
    if clk is None:
        print("  NOT READY %-14s no DATA_CLK recorded in BCAST_ARCHS of plan_workload_bcast.py" % n)
        bad.append(n)
        continue
    f = os.path.join(root, folder, x)
    if not os.path.isfile(f):
        print("  MISSING   %-14s %s/%s" % (n, folder, x))
        bad.append(n)
        continue
    try:
        got = r.data_clk(f)
    except SystemExit as e:
        print("  NO CLOCK  %-14s %s/%s  (%s)" % (n, folder, x, e))
        bad.append(n)
        continue
    print("  %s %-14s %s/%s  %d MHz%s" % ("ok       " if got == clk else "CLOCK    ", n, folder,
                                         x, got, "" if got == clk else "  (plan: %d)" % clk))
    if got != clk:
        bad.append(n)
if vec == "moe" and not names:
    waiting = [a[0] for a in p.BCAST_ARCHS if a[3] is None]
    if waiting:
        print("  (not in this run until their DATA_CLK is recorded: %s)" % ", ".join(waiting))
sys.exit(1 if bad else 0)
EOF
if [ -n "$CHECK" ]; then echo "preflight passed"; exit 0; fi

WANT=" $* "
DONE=""
echo "workload chain started $(date), vectors $VEC, log $LOG"
while read -r -u 3 name shape tenants folder xclbin cores blocks; do
    if [ -n "$1" ] && [[ "$WANT" != *" $name "* ]]; then continue; fi
    echo
    echo "################ $name: $tenants x $shape in $folder ($(date +%H:%M:%S))"
    cd "$ROOT/$folder" || die "no folder $ROOT/$folder"
    [ -f "$xclbin" ] || die "no $xclbin in $ROOT/$folder"
    cp "$TOOLS/host_workload_bcast.cpp" . || die "cannot copy the host"
    g++ -Wall -O2 -std=c++1y -I"$XILINX_XRT/include" host_workload_bcast.cpp -L"$XILINX_XRT/lib" \
        -lOpenCL -lrt -lstdc++ -pthread -DCORES="$cores" -DBLOCKS="$blocks" -o host_workload_bcast \
        || die "host_workload_bcast did not compile for $shape"
    if [ ! -f power_scraper.py ]; then
        src=$(find "$ROOT" -name power_scraper.py 2>/dev/null | head -1)
        [ -n "$src" ] && cp "$src" . || die "no power_scraper.py found"
    fi
    python3 "$TOOLS/run_workload_bcast.py" --arch "$name" --vectors "$VEC" --correctness \
        || die "$name: correctness gate"
    if [ -z "$GATE_ONLY" ]; then
        python3 "$TOOLS/run_workload_bcast.py" --arch "$name" --vectors "$VEC" \
            || die "$name: measurement"
    fi
    DONE="$DONE $name"
done 3<<< "$LIST"

echo
if [ -n "$GATE_ONLY" ]; then
    echo "=== CORRECTNESS GATES PASSED ($(date)), vectors $VEC:$DONE"
    exit 0
fi
echo "=== ALL DONE ($(date)), vectors $VEC, broadcast builds:$DONE"
echo "copy home (PowerShell, from the repo root; make the folder first: mkdir results\\workload_moe_bcast):"
echo "scp \"skoulas@coroni:$ROOT/Vitis_multi_*_bcast/workload_moebcast_*.csv\" results/workload_moe_bcast/"
}

main "$@"
exit

#!/bin/bash
# run_all_workloads_xrt.sh -- the workload on every architecture WITH THE NATIVE-XRT HOST
# (host_workload_xrt.cpp), one after another, stopping at the first failure.
#
#   bash ~/GEMV_Sparse/workload_tools/run_all_workloads_xrt.sh --per-matrix [--check|--gate-only] [names]
#   bash ~/GEMV_Sparse/workload_tools/run_all_workloads_xrt.sh --moe [--check|--gate-only] [names]
#   bash ~/GEMV_Sparse/workload_tools/run_all_workloads_xrt.sh [--check|--gate-only] [names]
#
# A COPY OF run_all_workloads.sh (2026-09-29), edited; run_all_workloads.sh (OpenCL host) is
# untouched. What differs, and only this: the host is host_workload_xrt.cpp, compiled with
# -std=c++17 -lxrt_coreutil (XRT 2.13's headers need C++17 without Boost); the runner is
# run_workload_xrt.py; --moe also takes the broadcast builds whose DATA_CLK is recorded in
# plan_workload_bcast.BCAST_ARCHS (the runner gives them --broadcast); the log is
# workload_chain_xrt_<mode>_<date>.log and the CSVs workload_xrt_*, copied home into
# results/workload_xrt*/ -- never mixed with the OpenCL results.
#
# The text below is run_all_workloads.sh's, true of this chain as well.
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
VEC=shared
FLAGS=""
while [ $# -gt 0 ]; do
    case "$1" in
        --check) CHECK=1; shift ;;
        --gate-only) GATE_ONLY=1; shift ;;
        --per-matrix) VEC=per-matrix; FLAGS="--per-matrix"; shift ;;
        --moe) VEC=moe; FLAGS="--moe"; shift ;;
        --*) die "unknown option $1 (--check, --gate-only, --per-matrix, --moe)" ;;
        *) break ;;
    esac
done
case "$VEC" in
    per-matrix) TAG=permatrix_ ;;
    moe) TAG=moe_ ;;
    *) TAG="" ;;
esac
LOG="$ROOT/workload_chain_xrt_${TAG}$(date +%Y%m%d_%H%M).log"
exec > >(tee -a "$LOG") 2>&1

[ -f /opt/xilinx/xrt/setup.sh ] && source /opt/xilinx/xrt/setup.sh >/dev/null 2>&1
[ -n "$XILINX_XRT" ] || die "XRT environment not available"
command -v xbutil >/dev/null || die "xbutil is not on PATH -- no power data would be recorded"
export BDF=${BDF:-0000:af:00.1}
export EMU=${EMU:-$ROOT/GEMV_4.0_Source/Emulation}
[ -f "$EMU/gen_timing_stimulus.py" ] || die "no stimulus scripts in $EMU"

# name shape tenants folder xclbin cores blocks, one architecture per line: the mode's list
# (MoE also takes every shared-vector build whose DATA_CLK is recorded)
LIST=$(cd "$TOOLS" && python3 -c "
import plan_workload as p, plan_workload_bcast as pb
archs = p.archs_for('$VEC') + (pb.archs_for('moe') if '$VEC' == 'moe' else [])
for n, s, t, clk, folder, x in archs:
    c, b = p.SHAPE_OF[s]
    print(n, s, t, folder, x, c, b)
" | tr -d '\r') || die "cannot read the architecture list from plan_workload(_bcast).py"

echo "preflight (build folders under $ROOT, vectors $VEC):"
python3 - "$TOOLS" "$ROOT" "$VEC" "$@" <<'EOF' || die "preflight -- fix the file, or the name/clock in ARCHS / SHARED_ARCHS of plan_workload.py"
import os, sys
sys.path.insert(0, sys.argv[1])
import plan_workload as p
import plan_workload_bcast as pb
import run_workload_xrt as r
root, vec, names = sys.argv[2], sys.argv[3], sys.argv[4:]
known = dict(p.ARCH)
known.update((a[0], a) for a in pb.BCAST_ARCHS)
bad = [n for n in names if n not in known]
for n in bad:
    print("  UNKNOWN   %s  (known: %s)" % (n, " ".join(sorted(known))))
selected = ([known[n] for n in names if n in known] if names else
            p.archs_for(vec) + (pb.archs_for("moe") if vec == "moe" else []))
for n, s, t, clk, folder, x in selected:
    if (p.is_shared(n) or n in pb.BCAST_NAMES) and vec != "moe":
        print("  MoE ONLY  %-14s a shared-vector build -- run it with --moe" % n)
        bad.append(n)
        continue
    if clk is None:
        print("  NOT READY %-14s no DATA_CLK recorded (SHARED_ARCHS of plan_workload.py or "
              "BCAST_ARCHS of plan_workload_bcast.py)" % n)
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
    waiting = [a[0] for a in p.SHARED_ARCHS + pb.BCAST_ARCHS if a[3] is None]
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
    cp "$TOOLS/host_workload_xrt.cpp" . || die "cannot copy the host"
    g++ -Wall -O2 -std=c++17 -I"$XILINX_XRT/include" host_workload_xrt.cpp -L"$XILINX_XRT/lib" \
        -lxrt_coreutil -lrt -pthread -DCORES="$cores" -DBLOCKS="$blocks" -o host_workload_xrt \
        || die "host_workload_xrt did not compile for $shape"
    if [ ! -f power_scraper.py ]; then
        src=$(find "$ROOT" -name power_scraper.py 2>/dev/null | head -1)
        [ -n "$src" ] && cp "$src" . || die "no power_scraper.py found"
    fi
    python3 "$TOOLS/run_workload_xrt.py" --arch "$name" --vectors "$VEC" --correctness \
        || die "$name: correctness gate"
    if [ -z "$GATE_ONLY" ]; then
        python3 "$TOOLS/run_workload_xrt.py" --arch "$name" --vectors "$VEC" \
            || die "$name: measurement"
    fi
    DONE="$DONE $name"
done 3<<< "$LIST"

echo
if [ -n "$GATE_ONLY" ]; then
    echo "=== CORRECTNESS GATES PASSED ($(date)), vectors $VEC:$DONE"
    exit 0
fi
echo "=== ALL DONE ($(date)), vectors $VEC:$DONE"
if [ "$VEC" = "per-matrix" ]; then
    echo "copy home (PowerShell, from the repo root; make the folder first: mkdir results\\workload_xrt_per_matrix):"
    echo "scp \"skoulas@coroni:$ROOT/Vitis_*/workload_xrt_permatrix_*.csv\" results/workload_xrt_per_matrix/"
elif [ "$VEC" = "moe" ]; then
    echo "copy home (PowerShell, from the repo root; make the folder first: mkdir results\\workload_xrt_moe):"
    echo "scp \"skoulas@coroni:$ROOT/Vitis_*/workload_xrt_moe_*.csv\" results/workload_xrt_moe/"
else
    echo "copy home (PowerShell, from the repo root; make the folder first: mkdir results\\workload_xrt):"
    echo "scp \"skoulas@coroni:$ROOT/Vitis_*/workload_xrt_[0-9]*.csv\" results/workload_xrt/"
fi
}

main "$@"
exit

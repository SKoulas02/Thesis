#!/bin/bash
# run_all_qwen.sh -- the Qwen MoE step-1 test on every build WITH THE NATIVE-XRT HOST, one
# after another, stopping at the first failure.
#
#   bash ~/GEMV_Sparse/workload_tools/run_all_qwen.sh --check              # preflight only
#   bash ~/GEMV_Sparse/workload_tools/run_all_qwen.sh --gate-only [names]  # the gates
#   bash ~/GEMV_Sparse/workload_tools/run_all_qwen.sh [names]              # gate + measurement
#
# A COPY OF run_all_workloads_xrt.sh (2026-09-29), edited; that file is untouched. What
# differs, and only this: the builds are plan_qwen.archs() -- the 13, the shared builds and
# the broadcast builds, every one whose DATA_CLK is recorded -- the runner is run_qwen.py
# (which gives each build its host flags), there is no --per-matrix / --moe (one test), the
# log is workload_chain_xrt_qwen_<date>.log and the CSVs workload_xrt_qwen_*, copied home
# into results/workload_xrt_qwen/.
#
# --gate-only: build the host and run ONLY the correctness gate of each selected build (no
# measurement, no quiet server needed) -- run it BEFORE the measurement window.
#
# PREFLIGHT, before anything runs: every selected build's folder and .xclbin must exist and
# the .xclbin must run at the clock the plan expects (its DATA_CLK), and every name given
# must be known. Any problem stops the chain before the first run.
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
    echo "Resume from any build by naming it (and the ones after it) as arguments${FLAGS:+, after $FLAGS}."
    exit 1
}

main() {
CHECK=""
GATE_ONLY=""
FLAGS=""
while [ $# -gt 0 ]; do
    case "$1" in
        --check) CHECK=1; shift ;;
        --gate-only) GATE_ONLY=1; FLAGS="--gate-only"; shift ;;
        --*) die "unknown option $1 (--check, --gate-only)" ;;
        *) break ;;
    esac
done
LOG="$ROOT/workload_chain_xrt_qwen_$(date +%Y%m%d_%H%M).log"
exec > >(tee -a "$LOG") 2>&1

[ -f /opt/xilinx/xrt/setup.sh ] && source /opt/xilinx/xrt/setup.sh >/dev/null 2>&1
[ -n "$XILINX_XRT" ] || die "XRT environment not available"
command -v xbutil >/dev/null || die "xbutil is not on PATH -- no power data would be recorded"
export BDF=${BDF:-0000:af:00.1}
export EMU=${EMU:-$ROOT/GEMV_4.0_Source/Emulation}
for f in gemv4_cosim_gen.py hex_to_bin.py compare_gemv4_py36.py; do
    [ -f "$EMU/$f" ] || die "no $f in $EMU (the gate needs it)"
done
for f in plan_qwen.py gen_qwen_stimulus.py run_qwen.py plan_workload.py plan_workload_bcast.py \
         run_workload_xrt.py host_workload_xrt.cpp; do
    [ -f "$TOOLS/$f" ] || die "no $f in $TOOLS"
done

# name shape engines folder xclbin cores blocks, one build per line
LIST=$(cd "$TOOLS" && python3 -c "
import plan_qwen as q
for n, s, t, clk, folder, x in q.archs():
    c, b = q.SHAPE_OF[s]
    print(n, s, t, folder, x, c, b)
" | tr -d '\r') || die "cannot read the build list from plan_qwen.py"

echo "preflight (build folders under $ROOT, the Qwen step-1 test):"
python3 - "$TOOLS" "$ROOT" "$@" <<'EOF' || die "preflight -- fix the file, or the name/clock in the build tables (plan_workload.py / plan_workload_bcast.py)"
import os, sys
sys.path.insert(0, sys.argv[1])
import plan_qwen as q
import run_workload_xrt as r
root, names = sys.argv[2], sys.argv[3:]
bad = [n for n in names if n not in q.ARCH]
for n in bad:
    print("  UNKNOWN   %s  (known: %s)" % (n, " ".join(sorted(q.ARCH))))
selected = [q.ARCH[n] for n in names if n in q.ARCH] if names else q.archs()
for n, s, t, clk, folder, x in selected:
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
    print("  %s %-14s %s/%s  %d MHz  %s%s" % ("ok       " if got == clk else "CLOCK    ", n,
                                             folder, x, got, " ".join(q.host_flags(n)),
                                             "" if got == clk else "  (plan: %d)" % clk))
    if got != clk:
        bad.append(n)
if not names:
    waiting = [a[0] for a in q.BUILDS if a[3] is None]
    if waiting:
        print("  (not in this run until their DATA_CLK is recorded: %s)" % ", ".join(waiting))
sys.exit(1 if bad else 0)
EOF
if [ -n "$CHECK" ]; then echo "preflight passed"; exit 0; fi

WANT=" $* "
DONE=""
echo "Qwen chain started $(date)${GATE_ONLY:+, gates only}, log $LOG"
while read -r -u 3 name shape engines folder xclbin cores blocks; do
    if [ -n "$1" ] && [[ "$WANT" != *" $name "* ]]; then continue; fi
    echo
    echo "################ $name: $engines x $shape in $folder ($(date +%H:%M:%S))"
    cd "$ROOT/$folder" || die "no folder $ROOT/$folder"
    [ -f "$xclbin" ] || die "no $xclbin in $ROOT/$folder"
    cp "$TOOLS/host_workload_xrt.cpp" . || die "cannot copy the host"
    g++ -Wall -O2 -std=c++17 -I"$XILINX_XRT/include" host_workload_xrt.cpp -L"$XILINX_XRT/lib" \
        -lxrt_coreutil -lrt -pthread -DCORES="$cores" -DBLOCKS="$blocks" -o host_workload_xrt \
        || die "host_workload_xrt did not compile for $shape"
    if [ ! -f power_scraper.py ]; then
        src=$(find "$ROOT" -name power_scraper.py 2>/dev/null < /dev/null | head -1)
        [ -n "$src" ] && cp "$src" . || die "no power_scraper.py found"
    fi
    python3 "$TOOLS/run_qwen.py" --arch "$name" --correctness || die "$name: correctness gate"
    if [ -z "$GATE_ONLY" ]; then
        python3 "$TOOLS/run_qwen.py" --arch "$name" || die "$name: measurement"
    fi
    DONE="$DONE $name"
done 3<<< "$LIST"

echo
if [ -n "$GATE_ONLY" ]; then
    echo "=== QWEN CORRECTNESS GATES PASSED ($(date)):$DONE"
    echo "accuracy of the realistic layer (card - exact, per row), copy home (PowerShell, from the repo root; make the folder first: mkdir results\qwen_accuracy):"
    echo "scp \"skoulas@coroni.microlab.ntua.gr:$ROOT/Vitis_*/qwen_accuracy_*.csv\" results/qwen_accuracy/"
    exit 0
fi
echo "=== QWEN ALL DONE ($(date)):$DONE"
echo "copy home (PowerShell, from the repo root; make the folder first: mkdir results\\workload_xrt_qwen):"
echo "scp \"skoulas@coroni.microlab.ntua.gr:$ROOT/Vitis_*/workload_xrt_qwen_*.csv\" results/workload_xrt_qwen/"
echo "and the realistic-layer accuracy of the gates (make the folder first: mkdir results\qwen_accuracy):"
echo "scp \"skoulas@coroni.microlab.ntua.gr:$ROOT/Vitis_*/qwen_accuracy_*.csv\" results/qwen_accuracy/"
}

main "$@"
exit

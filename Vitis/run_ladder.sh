#!/bin/bash
# ----------------------------------------------------------------------------
# run_ladder.sh -- the sparsity ladder on hardware (INTEGRATION_STEPS.md S25/S26)
#
# For each of 2:4, 2:8, 2:16, 2:32: regenerate stimulus, pack it to per-PC .bin
# images, run on the card, and diff against the golden model. Then the RUNTIME
# RECONFIGURATION case -- all four sparsities inside ONE calculation over one
# replayed activation vector, which is the architecture's headline claim and the
# one result nothing else in the thesis substitutes for.
#
# WHY A SCRIPT AND NOT FIVE PASTED COMMANDS
# Five runs x four steps is where a mistyped sparsity code or a forgotten repack
# silently produces a PASS on the wrong data. Here the generator, the packer, the
# card and the compare always move together, and the summary at the end is the
# table that goes in the thesis.
#
# CORRECTNESS ONLY -- IGNORE ANY THROUGHPUT PRINTED HERE. These cases are far too
# small for the timing to mean anything (a 2-beat run is ~99.99% XRT launch
# overhead). Throughput is a separate exercise: big sizes, best-of-N, and the
# differential method in measure.py.
#
# USAGE
#   ./run_ladder.sh [xclbin] [clock_MHz] [nwin] [nlaps]
#   ./run_ladder.sh ~/GEMV_Sparse/Vitis/gemv_sparse.xclbin 225 4 4
#
# CLOCK_MHZ must be what the xclbin was LINKED at -- 225 for the current sparse
# build, 300 for dense. It only scales the derived figures, but silently.
# ----------------------------------------------------------------------------
set -u

XCLBIN=${1:-$HOME/GEMV_Sparse/Vitis/gemv_sparse.xclbin}
CLOCK=${2:-225}
NWIN=${3:-4}
NLAPS=${4:-4}

EMU=$HOME/GEMV_Sparse/GEMV_4.0_Source/Emulation
HOSTBIN=$HOME/GEMV_Sparse/Vitis/host_sparse
LOGDIR=$HOME/GEMV_Sparse/ladder_logs

for f in "$XCLBIN" "$HOSTBIN" "$EMU/gemv4_cosim_gen.py" "$EMU/hex_to_bin.py" \
         "$EMU/compare_gemv4_py36.py"; do
    if [ ! -e "$f" ]; then echo "MISSING: $f"; exit 1; fi
done

# XRT must be in the environment, and it is PER-SHELL -- it does not survive a new
# terminal, a tmux window, or a reconnect. Without it the host does not fail
# gracefully: XRT throws std::runtime_error, nothing catches it, and the process
# dies with "Aborted (core dumped)" -- which looks like a design fault and is not.
# Caught here once rather than five times over.
if [ -z "${XILINX_XRT:-}" ]; then
    echo "XILINX_XRT is not set -- run this first, then retry:"
    echo "    source /opt/xilinx/xrt/setup.sh"
    exit 1
fi
mkdir -p "$LOGDIR"

unset XCL_EMULATION_MODE          # hardware, not emulation

declare -a NAMES=("2:4" "2:8" "2:16" "2:32")
declare -a CODES=("00" "01" "10" "11")
declare -a RESULT
FAILED=0

run_one () {           # $1 = label, $2 = generator args, $3 = log tag
    local label="$1" genargs="$2" tag="$3"
    echo ""
    echo "=============================================================="
    echo "  $label"
    echo "=============================================================="

    cd "$EMU" || exit 1
    # shellcheck disable=SC2086
    python3 gemv4_cosim_gen.py $genargs > "$LOGDIR/gen_$tag.log" 2>&1 || {
        echo "  GENERATOR FAILED -- see $LOGDIR/gen_$tag.log"; RESULT+=("$label GEN-FAIL"); FAILED=1; return; }
    grep -E "total beats|golden.txt" "$LOGDIR/gen_$tag.log" | sed 's/^/  /'

    python3 hex_to_bin.py pack > "$LOGDIR/pack_$tag.log" 2>&1 || {
        echo "  PACK FAILED -- see $LOGDIR/pack_$tag.log"; RESULT+=("$label PACK-FAIL"); FAILED=1; return; }
    # The schedule the packer reports is read back out of ind_pc2 -- i.e. from
    # the bytes the card will actually see, not from the command line.
    sed -n '/sparsity schedule/,/^$/p' "$LOGDIR/pack_$tag.log" | sed 's/^/  /'

    "$HOSTBIN" "$XCLBIN" "$EMU" "$CLOCK" > "$LOGDIR/run_$tag.log" 2>&1 || {
        echo "  HOST FAILED -- see $LOGDIR/run_$tag.log"; RESULT+=("$label HOST-FAIL"); FAILED=1; return; }
    grep -E "lap\(s\) ->|all CUs done" "$LOGDIR/run_$tag.log" | sed 's/^/  /'

    rm -f "$EMU/tlast.txt"        # host runs cannot observe TLAST; see below
    if python3 compare_gemv4_py36.py > "$LOGDIR/cmp_$tag.log" 2>&1; then :; fi
    grep -E "compared|DATA" "$LOGDIR/cmp_$tag.log" | sed 's/^/  /'

    if grep -q "DATA  : PASS" "$LOGDIR/cmp_$tag.log"; then
        RESULT+=("$label PASS")
    else
        RESULT+=("$label FAIL")
        FAILED=1
    fi
}

echo "sparsity ladder on hardware"
echo "  xclbin : $XCLBIN"
echo "  clock  : $CLOCK MHz   (must match what it was LINKED at)"
echo "  size   : --nwin $NWIN --nlaps $NLAPS"
echo "  logs   : $LOGDIR"

for i in 0 1 2 3; do
    run_one "${NAMES[$i]}" "--sparsity ${CODES[$i]} --nwin $NWIN --nlaps $NLAPS" "sp${CODES[$i]}"
done

# ---- the runtime-reconfiguration case --------------------------------------
# One calculation, one activation vector loaded once, four laps at four
# DIFFERENT sparsities. The engine re-samples the code from index PC2 at every
# window load, so the freeze cadence changes mid-calculation with no reload and
# no host involvement. Nothing else in the thesis demonstrates this.
run_one "RUNTIME RECONFIG 2:4->2:8->2:16->2:32" \
        "--sparsities 00,01,10,11 --nwin $NWIN" "reconfig"

echo ""
echo "=============================================================="
echo "  SUMMARY"
echo "=============================================================="
for r in "${RESULT[@]}"; do echo "  $r"; done
echo ""
if [ $FAILED -eq 0 ]; then
    echo "  ALL PASS -- bit-exact at every sparsity, including runtime reconfiguration."
else
    echo "  SOMETHING FAILED -- see $LOGDIR"
fi
echo ""
echo "  NOTE: compare_gemv4_py36.py reports 'tlast.txt not found' on every host"
echo "        run. That is expected, not a gap: TLAST is an AXIS sideband consumed"
echo "        by krnl_s2mm, so the host cannot observe it. Its correctness is"
echo "        established in RTL simulation, where the testbench sees it directly."
exit $FAILED

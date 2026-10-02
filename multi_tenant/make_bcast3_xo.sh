#!/bin/bash
# make_bcast3_xo.sh -- the 3-output broadcast mover (x3_bcast), compiled: its C simulation,
# then its two .xo files (krnl_mm2s_bcast3 for hw and hw_emu). Needed once, before
# setup_bcast3_build.sh.
#
# A COPY OF make_bcast_xo.sh (2026-09-29), edited to the one new kernel, which lives in its
# own file krnl_mm2s_bcast3.cpp (testbench tb_mm2s_bcast3.cpp); make_bcast_xo.sh and the
# krnl_mm2s_bcast2/6/7 .xo files it made are untouched. Its logs and the C-simulation files
# carry a "3" (make_bcast3_xo.log, csim3.txt, tb_bcast3) so nothing of that run is overwritten.
# (No --hls option: the Final II line of the v++ log is what confirmed bcast2/6/7.)
#
#   tmux new -d -s bcast3_xo 'bash ~/GEMV_Sparse/bcast_staging/make_bcast3_xo.sh'
#   tmux attach -t bcast3_xo         # ~10 min; Ctrl-b d to leave it running
#
# Runs in the staging folder holding krnl_mm2s_bcast3.cpp and tb_mm2s_bcast3.cpp (copied up
# with setup_bcast3_build.sh -- the scp line is at its top). Steps, each a gate for the next:
#   1. C simulation: g++ against the HLS headers; every kernel must reproduce
#      activations.hex (the RTL-verified vector) and synthetic vectors on EVERY output,
#      TLAST/TKEEP/TSTRB correct. Nothing is compiled if it fails.
#   2. v++ -c, one kernel at a time (each in its own --temp_dir); an existing .xo is kept.
#   3. each .xo checked: it holds krnl_mm2s_bcast<N> with exactly N stream outputs; the
#      synthesised loop's II printed (must be 1).
# Everything goes to make_bcast3_xo.log.

cd "$(dirname "$0")" || exit 1
source /opt/Xilinx/Vitis/2021.1/settings64.sh
export PLATFORM=/opt/xilinx/platforms/xilinx_u280_xdma_201920_3/xilinx_u280_xdma_201920_3.xpfm
EMU_DIR=$HOME/GEMV_Sparse/GEMV_4.0_Source/Emulation
COUNTS="3"

fail() { echo "STOPPED: $*"; exec bash; }
exec > >(tee -a make_bcast3_xo.log) 2>&1
echo "=== make_bcast3_xo.sh started $(date) in $(pwd)"

[ -f "$PLATFORM" ] || fail "platform file missing: $PLATFORM"
for f in krnl_mm2s_bcast3.cpp tb_mm2s_bcast3.cpp; do
    [ -f "$f" ] || fail "missing $f in $(pwd)"
done
[ -n "$XILINX_HLS" ] || fail "XILINX_HLS is not set (settings64.sh did not load?)"
[ -f "$EMU_DIR/activations.hex" ] || fail "no activations.hex in $EMU_DIR -- the C simulation \
checks the kernel against it; any gemv4_cosim_gen.py run writes one"

echo
echo "=== 1. C simulation (g++ against $XILINX_HLS/include)"
g++ -std=c++14 -O1 -I"$XILINX_HLS/include" -DEMU_DIR="\"$EMU_DIR\"" \
    tb_mm2s_bcast3.cpp krnl_mm2s_bcast3.cpp -o tb_bcast3 || fail "the testbench did not compile"
./tb_bcast3 | tee csim3.txt
grep -q "=== PASS ===" csim3.txt || fail "C simulation FAILED -- nothing compiled"

echo
echo "=== 2. v++ -c"
for N in $COUNTS; do
    for T in hw hw_emu; do
        XO=krnl_mm2s_bcast$N.$T.xo
        if [ -f "$XO" ]; then
            echo "  $XO exists -- kept"
            continue
        fi
        echo "  $XO  started $(date +%H:%M:%S)"
        v++ -c -t "$T" --platform "$PLATFORM" -k "krnl_mm2s_bcast$N" --temp_dir "_x_bcast$N.$T" \
            -o "$XO" krnl_mm2s_bcast3.cpp > "vpp_bcast$N.$T.log" 2>&1 \
            || { rm -f "$XO"; fail "v++ -c failed for $XO -- see vpp_bcast$N.$T.log"; }
    done
done

echo
echo "=== 3. what is inside each .xo"
bad=0
for N in $COUNTS; do
    for T in hw hw_emu; do
        XO=krnl_mm2s_bcast$N.$T.xo
        [ -f "$XO" ] || { echo "  $XO MISSING"; bad=1; continue; }
        # an .xo is a zip; its kernel.xml lists the kernel and its ports (python3, not unzip:
        # unzip's wildcards do not cross '/' in every build)
        xml=$(python3 -c "import sys, zipfile; z = zipfile.ZipFile(sys.argv[1]); print('\n'.join(z.read(n).decode('utf-8', 'replace') for n in z.namelist() if n.endswith('kernel.xml')))" "$XO" 2>/dev/null)
        if [ -n "$xml" ]; then
            # the kernel's name, and the distinct out<k> names (its args and ports), wherever
            # they sit in the XML
            name=$(echo "$xml" | grep -o "name=\"krnl_mm2s_bcast$N\"" | head -1)
            outs=$(echo "$xml" | grep -o 'name="out[0-9]*"' | sort -u | wc -l)
            if [ -n "$name" ] && [ "$outs" -eq "$N" ]; then
                echo "  $XO  ok: krnl_mm2s_bcast$N, $outs stream outputs out0..out$((N - 1))"
            else
                # the link's stream_connect lines name the ports out0..out<N-1>; anything
                # else here means they would dangle
                echo "  $XO  WRONG: kernel [${name:-not krnl_mm2s_bcast$N}], $outs names out<k>" \
                     "(want $N); the names in its kernel.xml:"
                echo "$xml" | grep -o 'name="[^"]*"' | sort -u | tr '\n' ' ' | sed 's/^/      /'
                echo
                bad=1
            fi
        else
            echo "  $XO  WRONG: no kernel.xml inside -- not a kernel .xo"; bad=1
        fi
    done
    # HLS outlines the pipelined loop into krnl_mm2s_bcast<N>_Pipeline_bcast, whose report
    # v++ does not keep; the top report's instance table shows only '?' (n_beats is a runtime
    # value). The II is in the compile log: "Pipelining result : Target II = 1, Final II = 1".
    # (Measured 2026-09-29 on the server: Final II = 1, Depth = 2 for bcast2, bcast6, bcast7.)
    echo "  the bcast loop of krnl_mm2s_bcast$N (Final II must be 1):"
    grep -h "INFO: \[v++ 200-1470\].*Final II" "vpp_bcast$N.hw.log" < /dev/null 2>/dev/null \
        | sort -u | head -2 | sed 's/^/    /'
done
echo
if [ $bad -eq 0 ]; then
    echo "=== DONE $(date): both krnl_mm2s_bcast3 .xo files ready -- next: bash setup_bcast3_build.sh"
else
    echo "=== PROBLEMS above -- do not run setup_bcast3_build.sh yet"
fi
exec bash

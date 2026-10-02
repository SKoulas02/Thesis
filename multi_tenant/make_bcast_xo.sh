#!/bin/bash
# make_bcast_xo.sh -- the broadcast vector mover, compiled: its C simulation, then the six
# .xo files (krnl_mm2s_bcast2 / 6 / 7, each for hw and hw_emu). Needed once, before
# setup_bcast_builds.sh. NEW 2026-09-28; nothing outside this folder is read-write touched.
#
#   tmux new -d -s bcast_xo 'bash ~/GEMV_Sparse/bcast_staging/make_bcast_xo.sh'
#   tmux attach -t bcast_xo          # ~30 min; Ctrl-b d to leave it running
#
#   ... make_bcast_xo.sh --hls       # also Vitis HLS csim + csynth + cosim first
#                                    # (run_hls_bcast.tcl; optional, adds ~15 min)
#
# Runs in the staging folder holding krnl_mm2s_bcast.cpp and tb_mm2s_bcast.cpp (copied up
# with setup_bcast_builds.sh -- the scp line is at its top). Steps, each a gate for the next:
#   1. C simulation: g++ against the HLS headers; every kernel must reproduce
#      activations.hex (the RTL-verified vector) and synthetic vectors on EVERY output,
#      TLAST/TKEEP/TSTRB correct. Nothing is compiled if it fails.
#   2. v++ -c, one kernel at a time (each in its own --temp_dir); an existing .xo is kept.
#   3. each .xo checked: it holds krnl_mm2s_bcast<N> with exactly N stream outputs; the
#      synthesised loop's II printed (must be 1).
# Everything goes to make_bcast_xo.log.

cd "$(dirname "$0")" || exit 1
source /opt/Xilinx/Vitis/2021.1/settings64.sh
export PLATFORM=/opt/xilinx/platforms/xilinx_u280_xdma_201920_3/xilinx_u280_xdma_201920_3.xpfm
EMU_DIR=$HOME/GEMV_Sparse/GEMV_4.0_Source/Emulation
COUNTS="2 6 7"

fail() { echo "STOPPED: $*"; exec bash; }
exec > >(tee -a make_bcast_xo.log) 2>&1
echo "=== make_bcast_xo.sh started $(date) in $(pwd)"

[ -f "$PLATFORM" ] || fail "platform file missing: $PLATFORM"
for f in krnl_mm2s_bcast.cpp tb_mm2s_bcast.cpp; do
    [ -f "$f" ] || fail "missing $f in $(pwd)"
done
[ -n "$XILINX_HLS" ] || fail "XILINX_HLS is not set (settings64.sh did not load?)"
[ -f "$EMU_DIR/activations.hex" ] || fail "no activations.hex in $EMU_DIR -- the C simulation \
checks the kernel against it; any gemv4_cosim_gen.py run writes one"

echo
echo "=== 1. C simulation (g++ against $XILINX_HLS/include)"
g++ -std=c++14 -O1 -I"$XILINX_HLS/include" -DEMU_DIR="\"$EMU_DIR\"" \
    tb_mm2s_bcast.cpp krnl_mm2s_bcast.cpp -o tb_bcast || fail "the testbench did not compile"
./tb_bcast | tee csim.txt
grep -q "=== PASS ===" csim.txt || fail "C simulation FAILED -- nothing compiled"

if [ "$1" = "--hls" ]; then
    echo
    echo "=== 1b. Vitis HLS: csim + csynth + cosim of each kernel (run_hls_bcast.tcl)"
    [ -f run_hls_bcast.tcl ] || fail "missing run_hls_bcast.tcl"
    sed -i "s|^#define EMU_DIR .*|#define EMU_DIR \"$EMU_DIR\"|" tb_mm2s_bcast.cpp
    vitis_hls -f run_hls_bcast.tcl > vitis_hls_bcast.log 2>&1 || fail "vitis_hls failed -- see vitis_hls_bcast.log"
    grep -E "=== bcast|PASS|FAIL|Pass|Fail" vitis_hls_bcast.log | tail -30
fi

echo
echo "=== 2. v++ -c (one kernel at a time)"
for N in $COUNTS; do
    for T in hw hw_emu; do
        XO=krnl_mm2s_bcast$N.$T.xo
        if [ -f "$XO" ]; then
            echo "  $XO exists -- kept"
            continue
        fi
        echo "  $XO  started $(date +%H:%M:%S)"
        v++ -c -t "$T" --platform "$PLATFORM" -k "krnl_mm2s_bcast$N" --temp_dir "_x_bcast$N.$T" \
            -o "$XO" krnl_mm2s_bcast.cpp > "vpp_bcast$N.$T.log" 2>&1 \
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
    echo "=== DONE $(date): six .xo files ready -- next: bash setup_bcast_builds.sh"
else
    echo "=== PROBLEMS above -- do not run setup_bcast_builds.sh yet"
fi
exec bash

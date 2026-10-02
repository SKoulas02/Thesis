#!/bin/bash
# hw_emu_host_debug.sh -- rerun a broadcast build's hw_emu gate HOST alone, under gdb, to see
# where it crashes. NEW 2026-09-29 (a debugging aid; changes nothing).
#
#   tmux new -d -s x3_dbg 'bash ~/GEMV_Sparse/hw_emu_host_debug.sh ~/GEMV_Sparse/Vitis_multi_x3_bcast'
#   tail -f ~/GEMV_Sparse/Vitis_multi_x3_bcast/host_dbg.log
#
# WHY: the x3_bcast gate died with "Segmentation fault" of python -- but the crash is in the
# host: XRT's hw_emu shim catches SIGSEGV in the host and does kill(0, SIGSEGV)
# (hw_em/generic_pcie_hal2/shim.cxx:259-266), killing its whole process group, python with
# it, and the host's output (held by python) was lost. Here the host runs ALONE, built with -g,
# under gdb, in a session of its own, on the stimulus that gate left in wl_correct/; gdb stops
# at the SIGSEGV and prints the backtrace of every thread into host_dbg.log.
# Needs: run_hw_emu.sh has run once in the folder (hw_emu xclbin, emconfig.json, wl_correct/).

trap '' INT QUIT HUP
B=${1:?usage: hw_emu_host_debug.sh <build folder>}
cd "$B" || exit 1
source /opt/Xilinx/Vitis/2021.1/settings64.sh >/dev/null
source /opt/xilinx/xrt/setup.sh >/dev/null
exec > >(tee host_dbg.log) 2>&1

XCLBIN=$(ls sparse_*.hw_emu.xclbin 2>/dev/null | head -1)
[ -n "$XCLBIN" ] || { echo "STOPPED: no hw_emu xclbin in $B"; exit 1; }
[ -f emconfig.json ] && [ -d wl_correct ] || { echo "STOPPED: run run_hw_emu.sh here first"; exit 1; }
command -v gdb >/dev/null || { echo "STOPPED: gdb is not installed on this server"; exit 1; }
CB=$(sed -n 's/.*-DCORES=\([0-9]*\) -DBLOCKS=\([0-9]*\).*/-DCORES=\1 -DBLOCKS=\2/p' run_hw_emu.sh | head -1)
N=$(ls -d wl_correct/t* | wc -l)
SPECS=""
for k in $(seq 0 $((N - 1))); do
    SPECS="$SPECS $k:$(ls -d wl_correct/t$k/c* | sort | paste -sd, -)"
done

echo "=== host debug run $(date): $XCLBIN, $N engines, $CB"
g++ -g -O0 -std=c++1y -I"$XILINX_XRT/include" host_workload_bcast.cpp -L"$XILINX_XRT/lib" \
    -lOpenCL -lrt -lstdc++ -pthread $CB -o host_dbg || { echo "STOPPED: compile failed"; exit 1; }
export XCL_EMULATION_MODE=hw_emu
echo "--- $ ./host_dbg --lockstep --shared-vector $XCLBIN 400 1 0$SPECS"
setsid -w gdb -batch -q \
    -ex 'handle SIGPIPE nostop noprint pass' -ex 'handle SIGCHLD nostop noprint pass' \
    -ex 'handle SIGUSR1 nostop noprint pass' -ex 'handle SIGUSR2 nostop noprint pass' \
    -ex 'set pagination off' -ex run \
    -ex 'call (int)fflush(0)' -ex 'echo \n=== BACKTRACE (crashing thread) ===\n' -ex bt \
    -ex 'echo \n=== ALL THREADS ===\n' -ex 'thread apply all bt 25' \
    --args ./host_dbg --lockstep --shared-vector "$XCLBIN" 400 1 0 $SPECS < /dev/null
echo "=== host debug run finished $(date) -- send the part from 'BACKTRACE' on"

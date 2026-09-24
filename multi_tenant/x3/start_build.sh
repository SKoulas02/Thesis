#!/bin/bash
# start_build.sh -- ONE-COMMAND launch of the 3 x 4x4 multi-tenant link, target 400 MHz.
#
#   tmux new -d -s x3_4x4 'bash ~/GEMV_Sparse/Vitis_multi_x3/start_build.sh'
#
# Checks everything first and refuses to start if anything is wrong (the reason is
# printed and the tmux window stays open: tmux attach -t x3_4x4). 30 minutes in it
# writes check_30min.txt: stream warnings must be 0, and the synthesis line must
# contain AlternateRoutability.
#
# Why 400 and not 375: a build that CLOSES is capped at its target, while a miss is
# lowered to the highest clock its timing proves. 2 x 4x4 reached 394 when pushed to
# 400 but stopped at 378 when asked for 375. Read DATA_CLK afterwards, as always.

cd "$(dirname "$0")" || exit 1
source /opt/Xilinx/Vitis/2021.1/settings64.sh
source /opt/xilinx/xrt/setup.sh
export PLATFORM=/opt/xilinx/platforms/xilinx_u280_xdma_201920_3/xilinx_u280_xdma_201920_3.xpfm
export BDF=0000:af:00.1

fail() { echo "NOT STARTED: $*"; exec bash; }

[ -f "$PLATFORM" ] || fail "platform file missing: $PLATFORM"
for f in ../krnl_gemv_sparse_4x4.xo krnl_mm2s.hw.xo krnl_s2mm.hw.xo impl_family.cfg \
         sparse_hbm_4x4_x3.cfg slr_floorplan_4x4_x3.cfg; do
    [ -f "$f" ] || fail "missing $f in $(pwd)"
done
if pgrep -u "$USER" -f "v\+\+ -t hw" >/dev/null; then
    fail "another v++ -t hw build is still running (see: tmux ls)"
fi
free_gb=$(df -BG --output=avail ~ | tail -1 | tr -dc 0-9)
[ "${free_gb:-0}" -ge 50 ] || fail "only ${free_gb} GB free in /home -- a full disk kills a link"
[ -e sparse_4x4_x3_400.xclbin ] && fail "sparse_4x4_x3_400.xclbin already exists -- already built"

( sleep 1800
  { echo "checked $(date)"
    echo "stream/connect warnings (must be 0): $(grep -irchE 'warning.*(stream|connect|unconnect)' _x/logs 2>/dev/null | paste -sd+ | bc)"
    echo "synthesis strategy (must contain AlternateRoutability; empty = not started yet):"
    grep -oE 'synth_design [^"]*' _x/logs/link/vivado.log 2>/dev/null | head -2
  } > check_30min.txt ) &

echo "linking 3 x 4x4 at 400 MHz -- started $(date), ${free_gb} GB free"
v++ -t hw --platform "$PLATFORM" --config sparse_hbm_4x4_x3.cfg --config impl_family.cfg \
    --config slr_floorplan_4x4_x3.cfg --kernel_frequency 400 -l -o sparse_4x4_x3_400.xclbin \
    ../krnl_gemv_sparse_4x4.xo krnl_mm2s.hw.xo krnl_s2mm.hw.xo 2>&1 | tee link_hw_x3_400.log
echo "v++ finished $(date). Next: the timing check and DATA_CLK."
exec bash

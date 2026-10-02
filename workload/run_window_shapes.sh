#!/bin/bash
# run_window_shapes.sh -- launcher of run_window_shapes.py, THE SHAPES WINDOW (read its header).
#
#   bash ~/GEMV_Sparse/workload_tools/run_window_shapes.sh --check                   # preflight
#   tmux new -d -s shapes 'bash ~/GEMV_Sparse/workload_tools/run_window_shapes.sh'  # the run
#   tail -f ~/GEMV_Sparse/window_shapes_latest.log            # watch (Ctrl-C stops only tail)
#
# A COPY OF run_window.sh (2026-10-01): the same environment, unbuffered output, no terminal input
# and -- for the run itself, not --check -- Ctrl-C, Ctrl-\ and hang-up ignored. Only the script it
# runs differs: run_window_shapes.py.

case " $* " in
    *" --check "*|*" -h "*|*" --help "*) ;;
    *) trap '' INT QUIT HUP ;;
esac
[ -f /opt/xilinx/xrt/setup.sh ] && source /opt/xilinx/xrt/setup.sh >/dev/null 2>&1
[ -n "$XILINX_XRT" ] || { echo "XRT environment not available (/opt/xilinx/xrt/setup.sh)"; exit 1; }
export PYTHONUNBUFFERED=1
TOOLS=$(cd "$(dirname "$0")" && pwd)
exec python3 "$TOOLS/run_window_shapes.py" "$@" < /dev/null

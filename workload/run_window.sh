#!/bin/bash
# run_window.sh -- launcher of run_window.py, THE MEASUREMENT WINDOW (read its header).
#
#   bash ~/GEMV_Sparse/workload_tools/run_window.sh --check                    # preflight, BEFORE
#   tmux new -d -s window 'bash ~/GEMV_Sparse/workload_tools/run_window.sh'   # THE window
#   tail -f ~/GEMV_Sparse/window_latest.log                                    # watch (Ctrl-C stops only tail)
#
# A NEW FILE (2026-10-01). It sets up the XRT environment, makes the output unbuffered (the log
# shows progress as it happens) and reads nothing from the terminal. For the window itself (not
# --check) it IGNORES Ctrl-C, Ctrl-\ and hang-up, and so does everything it starts (an ignored
# signal is inherited): a dropped SSH session or a stray key cannot stop the measurements. To stop
# the window on purpose, see the pkill line in run_window.py's header.

case " $* " in
    *" --check "*|*" -h "*|*" --help "*) ;;
    *) trap '' INT QUIT HUP ;;
esac
[ -f /opt/xilinx/xrt/setup.sh ] && source /opt/xilinx/xrt/setup.sh >/dev/null 2>&1
[ -n "$XILINX_XRT" ] || { echo "XRT environment not available (/opt/xilinx/xrt/setup.sh)"; exit 1; }
export PYTHONUNBUFFERED=1
TOOLS=$(cd "$(dirname "$0")" && pwd)
exec python3 "$TOOLS/run_window.py" "$@" < /dev/null

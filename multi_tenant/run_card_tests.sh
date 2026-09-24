#!/bin/bash
# run_card_tests.sh -- EVERY card test for one multi-tenant bitstream, stopping at the first failure.
#
#   cd ~/GEMV_Sparse/Vitis_multi_8x4_x2
#   bash ~/GEMV_Sparse/multi_tools/run_card_tests.sh sparse_8x4_x2_375.xclbin 8 4 2
#
# args: <xclbin> <cores> <blocks> <tenants> [--no-timing] [--mixed-only]
#   --no-timing  skip the timing summary and report snapshot. Use it when _x/ belongs to a
#                DIFFERENT link of the same directory (Vitis_multi_x2: its _x/ is the 375
#                retry, not the 400 bitstream being tested).
#   --mixed-only redo ONLY the mixed measurement (stimulus + run_multi_measure), e.g. after
#                a run whose soaks had no power. Skips timing, correctness, endurance,
#                probes and all-2:32; overwrites multi_..._<MHz>MHz.csv.
#
# XRT IS ALWAYS SOURCED, and power telemetry is checked before anything is measured. On
# 2026-09-24 a chain ran with XILINX_XRT set but /opt/xilinx/xrt/bin missing from PATH: the
# host and xclbinutil worked, xbutil did not, and every soak came back without power.
#
# Steps, in order, each one gating the next:
#   DATA_CLK -> timing + per-die summary, report snapshot -> host built for this shape ->
#   packer constants for this shape -> correctness, all tenants together -> 30 s endurance
#   -> measurement stimulus -> probe under load -> mixed measurement -> all-2:32 stimulus
#   -> probe under load -> all-2:32 measurement -> the scp line for both CSVs.
# The latest host/prep/driver are copied in from this script's own directory first, so one
# scp of multi_tools/ keeps every build directory current. Everything is logged to
# card_tests_<xclbin stem>.log.
#
# CSV names follow the consolidation script: multi_x<N>_<MHz>MHz[_all2to32].csv for 4x4,
# multi_<shape>_x<N>_<MHz>MHz[_all2to32].csv otherwise.

set -o pipefail
XCL=$1; C=$2; B=$3; N=$4
NO_TIMING=0; MIXED_ONLY=0
for o in "${@:5}"; do
    case "$o" in
        --no-timing) NO_TIMING=1 ;;
        --mixed-only) MIXED_ONLY=1; NO_TIMING=1 ;;
        *) echo "unknown option $o"; exit 2 ;;
    esac
done
if [ -z "$N" ]; then
    echo "usage: $0 <xclbin> <cores> <blocks> <tenants> [--no-timing] [--mixed-only]"
    exit 2
fi
TOOLS=$(cd "$(dirname "$0")" && pwd)
EMU=${EMU:-$HOME/GEMV_Sparse/GEMV_4.0_Source/Emulation}
STEM=${XCL%.xclbin}
exec > >(tee -a "card_tests_${STEM}.log") 2>&1

say() { echo; echo "=== $* ($(date +%H:%M:%S))"; }
die() {
    echo
    echo "STOPPED: $*"
    echo "If a host run hung, reset the card before anything else:"
    echo "  pkill -u \$USER -f host_sparse_multi; sleep 2; xbutil reset --device ${BDF:-0000:af:00.1}"
    exit 1
}

[ -f /opt/xilinx/xrt/setup.sh ] && source /opt/xilinx/xrt/setup.sh >/dev/null 2>&1
[ -n "$XILINX_XRT" ] || die "XRT environment not available"
command -v xbutil >/dev/null || die "xbutil is not on PATH -- the soaks would have no power"
export BDF=${BDF:-0000:af:00.1}
[ -f "$XCL" ] || die "no $XCL in $(pwd)"
for f in host_sparse_multi.cpp prep_tenants.py run_multi_measure.py; do
    cp "$TOOLS/$f" . || die "cannot copy $f from $TOOLS"
done
if [ ! -f power_scraper.py ]; then
    src=$(find "$HOME/GEMV_Sparse" -name power_scraper.py 2>/dev/null | head -1)
    [ -n "$src" ] && cp "$src" . || die "no power_scraper.py found"
fi
python3 -c "import power_scraper as p; r = p.read_once('$BDF'); print('power check: board %.2f W, VCCINT %.2f W' % (r['board_w'], r['vccint_w'] or 0.0))" \
    || die "power telemetry does not work -- the soaks would have no energy"

T=$((C * B)); W=$(( (32 * T + 255) / 256 )); IND=$(( (10 * T + 2 + 255) / 256 ))
CP=$(( (16 * T + 255) / 256 )); SHAPE=${C}x${B}
if [ "$SHAPE" = "4x4" ]; then TAG=x$N; else TAG=${SHAPE}_x$N; fi
MEMBERS=$(for k in $(seq 0 $((N - 1))); do printf "%d:t%d " "$k" "$k"; done)

CLK=$(xclbinutil --info --input "$XCL" | grep -A3 DATA_CLK | grep -oE "[0-9]+ MHz" | cut -d' ' -f1)
[ -n "$CLK" ] || die "cannot read DATA_CLK from $XCL"
say "$XCL: $N x $SHAPE tenants, the card runs it at $CLK MHz (DATA_CLK)"

if [ "$NO_TIMING" -eq 0 ]; then
    RS=$(ls _x/reports/link/imp/impl_1_*timing_summary_postroute_physopted.rpt 2>/dev/null | head -1)
    if [ -n "$RS" ]; then
        awk '/Intra Clock Table/,/Inter Clock Table/' "$RS" \
            | awk 'NF>5 {print "  " $1, "WNS=" $2, "TNS=" $3, "fail=" $4}' | grep -E "kernel_0|hbm_aclk_0"
        grep -E "Site Type|CLB LUTs  |Block RAM Tile|DSPs  |Total SLLs" \
            _x/reports/link/imp/impl_1_slr_util_routed.rpt
        SNAP=$HOME/GEMV_Sparse/reports_multi_${STEM#sparse_}
        if [ ! -d "$SNAP" ]; then
            mkdir -p "$SNAP" && cp -r _x/reports/link/imp "$SNAP"/ && cp link_hw_*.log "$SNAP"/ 2>/dev/null
            echo "  reports saved to $SNAP"
        fi
    else
        echo "  (no final timing report under _x/ -- skipped)"
    fi
fi

say "host for $SHAPE"
g++ -Wall -O2 -std=c++1y -I"$XILINX_XRT/include" host_sparse_multi.cpp -L"$XILINX_XRT/lib" \
    -lOpenCL -lrt -lstdc++ -pthread -DCORES="$C" -DBLOCKS="$B" -o host_sparse_multi \
    || die "the host did not compile"

if [ "$MIXED_ONLY" -eq 0 ]; then
say "packer constants -> $SHAPE (W_PCS $W, IND_PCS $IND, C_PCS $CP, LANES $T)"
sed -i -E "s/^W_PCS *= *[0-9]+/W_PCS = $W/; s/^IND_PCS *= *[0-9]+/IND_PCS = $IND/; s/^C_PCS *= *[0-9]+/C_PCS = $CP/; s/^LANES *= *[0-9]+/LANES = $T/" \
    "$EMU/hex_to_bin.py" || die "cannot set the packer constants"
grep -nE "^(W_PCS|IND_PCS|A_PCS|C_PCS|LANES) *=" "$EMU/hex_to_bin.py"
fi

compare_all() {                         # $1 = "" or "corr/"; returns 1 if any tenant fails
    local bad=0 k r
    for k in $(seq 0 $((N - 1))); do
        r=$(cd "${1}t$k" && rm -f tlast.txt && python3 compare_gemv4_py36.py 2>&1 | grep -E "compared|^=== ")
        echo "  t$k: $(echo "$r" | tr '\n' ' ')"
        echo "$r" | grep -q "=== PASS ===" || bad=1
    done
    return $bad
}

if [ "$MIXED_ONLY" -eq 0 ]; then
say "correctness stimulus: $N different matrices"
rm -rf corr
python3 prep_tenants.py --emu "$EMU" --out . --tenants "$N" --cores "$C" --blocks "$B" \
    > prep_correctness.log 2>&1 || { tail -15 prep_correctness.log; die "prep_tenants (correctness) failed"; }
grep -E "^   ok:" prep_correctness.log

say "correctness: all $N tenants together"
timeout 120 stdbuf -oL ./host_sparse_multi "$XCL" "$CLK" 0 $MEMBERS > correctness.log 2>&1
rc=$?; [ $rc -eq 0 ] || { tail -5 correctness.log; die "host exit $rc (124 = hung)"; }
grep -E "launched" correctness.log
compare_all "" || die "a tenant is not bit-exact"

say "endurance: 30 s, every tenant in its own thread"
timeout 150 stdbuf -oL ./host_sparse_multi "$XCL" "$CLK" 30 $MEMBERS > endurance.log 2>&1
rc=$?; [ $rc -eq 0 ] || { tail -5 endurance.log; die "endurance host exit $rc (124 = hung)"; }
grep -E "soak:|AGGREGATE" endurance.log
compare_all "" || die "a tenant is not bit-exact after the endurance run"
mkdir corr && for k in $(seq 0 $((N - 1))); do mv "t$k" corr/; done
fi

probe_all() {                           # $1 = label for the log names
    local k j n act ovl r
    for k in $(seq 0 $((N - 1))); do
        n=""; for j in $(seq 0 $((N - 1))); do [ "$j" != "$k" ] && n="$n $j:t$j"; done
        timeout 120 stdbuf -oL ./host_sparse_multi "$XCL" "$CLK" 0 $n "$k:corr/t$k" \
            > "probe_${1}_t$k.log" 2>&1 || die "probe t$k ($1): host run failed"
        act=$(grep -E "^  t$k " "probe_${1}_t$k.log" | awk '{print $8}')
        ovl=$(grep "active overlap" "probe_${1}_t$k.log" | awk '{print $4}')
        r=$(cd "corr/t$k" && rm -f tlast.txt && python3 compare_gemv4_py36.py 2>&1 | grep -E "^=== ")
        echo "  probe t$k ($1): $r   its active time $act us, all-running overlap $ovl us"
        echo "$r" | grep -q PASS || die "probe t$k is not bit-exact with its neighbours running"
    done
}

measure() {                             # $1 = sparsity code or "", $2 = CSV suffix, $3 = label
    say "measurement stimulus ($3)"
    python3 prep_tenants.py --emu "$EMU" --out . --tenants "$N" --cores "$C" --blocks "$B" \
        --mode measure ${1:+--all-sparsity $1} > "prep_measure_$3.log" 2>&1 \
        || { tail -15 "prep_measure_$3.log"; die "prep_tenants (measure) failed"; }
    grep -E "^equal weight beats" "prep_measure_$3.log"
    if [ "$MIXED_ONLY" -eq 0 ]; then
        say "probe under load ($3): each correctness matrix runs while the others stream"
        probe_all "$3"
    fi
    CSV=multi_${TAG}_${CLK}MHz$2.csv
    say "measurement ($3), about 10 minutes -> $CSV"
    python3 run_multi_measure.py --xclbin "$XCL" --clock "$CLK" --tenants "$N" --cores "$C" \
        --blocks "$B" --reps 3 --soak 60 --soak-each --csv "$CSV" > "measure_$3.log" 2>&1 \
        || { tail -20 "measure_$3.log"; die "run_multi_measure ($3) failed"; }
    sed -n '/^set  /,$p' "measure_$3.log"
}

measure "" "" mixed
[ "$MIXED_ONLY" -eq 0 ] && measure 11 _all2to32 all2to32

say "ALL DONE: $N x $SHAPE at $CLK MHz"
echo "copy home (PowerShell, from the repo root):"
if [ "$MIXED_ONLY" -eq 1 ]; then
    echo "scp skoulas@coroni.microlab.ntua.gr:$(pwd)/multi_${TAG}_${CLK}MHz.csv results/multi_tenant/"
else
    echo "scp skoulas@coroni.microlab.ntua.gr:$(pwd)/multi_${TAG}_${CLK}MHz.csv skoulas@coroni.microlab.ntua.gr:$(pwd)/multi_${TAG}_${CLK}MHz_all2to32.csv results/multi_tenant/"
fi

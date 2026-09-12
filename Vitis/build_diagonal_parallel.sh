#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# build_diagonal_parallel.sh -- bitstreams for several CORES x BLOCKS = 64
# points, with the v++ links running IN PARALLEL.
#
# WHY THE DIAGONAL PARALLELISES CLEANLY. Every point has the same external
# interface (17 PCs, 2048/640/1024-bit buses, 512 DSPs), so all of them share
# ONE link config, ONE host and ONE stimulus. Only the two generics differ, and
# they are baked into the .xo at package time. Nothing is shared at LINK time
# except the platform, which is read-only. So N links can run at once.
#
# TWO PHASES, AND ONLY THE SECOND PARALLELISES:
#   1. package one .xo per configuration -- minutes each, SEQUENTIAL because it
#      edits the generic defaults in the shared source tree in place.
#   2. v++ link -- HOURS each, run MAXPAR at a time.
#
#  ⚠️ EACH LINK NEEDS ITS OWN --temp_dir. v++ writes _x into the working
#  directory by default, so two concurrent links in the same folder would
#  silently corrupt each other's build tree. --temp_dir/--log_dir/--report_dir
#  give each one its own space. This is the single most important detail here.
#
#  ⚠️ THIS IS A SHARED MACHINE with about ten users. Parallel Vivado is the
#  fastest way to make it unusable for everybody else. The guards below are not
#  decoration: the script refuses to start a link when free RAM or free disk is
#  below threshold, and it caps concurrency. Raise MAXPAR only after watching
#  one full run with `free -h` and `df -h /home`.
#
# CALIBRATE BEFORE RAISING MAXPAR. Measured on this machine: a full link peaked
# at ~20.3 GB, with ~52 GB free physical while it ran, and took 8.5 HOURS. Disk
# is the tighter constraint -- /home has been sitting near 95% full, and each
# link keeps its own _x tree. MEASURE ONE FIRST:
#
#     du -sh <temp_dir>        after a link finishes, before it is deleted
#
# Usage:
#     bash build_diagonal_parallel.sh              # uses CONFIGS below
#     MAXPAR=2 bash build_diagonal_parallel.sh     # override concurrency
# ----------------------------------------------------------------------------

set -u

# ---- what to build ---------------------------------------------------------
# "CORES BLOCKS" pairs, ORDERED BY INFORMATION VALUE, because the queue drains
# in this order and a run may be stopped early.
#
#   4x16 / 16x4  bracket 8x8 most closely -- they test the fanout hypothesis
#                with the least confounding, so they come first.
#   2x32 / 32x2  fill the curve.
#   1x64 / 64x1  the extremes. Least likely to be useful designs, most likely
#                to make the shape of the tradeoff obvious.
#
# 8x8 IS DELIBERATELY ABSENT: it is already built and measured, and the
# floorplan rebuild reproduced it to within 109 bytes, so it needs no re-run.
CONFIGS=(
    "4 16"
    "16 4"
    "2 32"
    "32 2"
    "1 64"
    "64 1"
)

# ---- resource policy -------------------------------------------------------
MAXPAR=${MAXPAR:-2}          # concurrent v++ links
MIN_RAM_GB=${MIN_RAM_GB:-30} # refuse to START a link below this free RAM
MIN_OUT_GB=${MIN_OUT_GB:-60}

REPO=~/GEMV_Sparse
SRCD="$REPO/GEMV_4.0_Source/Design"
VITIS="$REPO/Vitis"
PLATFORM=/opt/xilinx/platforms/xilinx_u280_xdma_201920_3/xilinx_u280_xdma_201920_3.xpfm
# OUT holds the per-link build trees (~4.8 GB each) and the output xclbins.
# OVERRIDABLE because /home on this machine is shared and has repeatedly filled
# up through no fault of ours -- 2026-09-08 it sat at 100% with 16 GB free while
# this user's entire home was 27 GB. / had 150 GB free at the same moment. So:
#
#     OUT=/var/tmp/$USER-diag bash build_diagonal_parallel.sh
#
# picks a filesystem with room. /var/tmp survives reboots where /tmp may not.
# The guard below then checks THAT filesystem rather than /home.
OUT=${OUT:-$REPO/diagonal_builds}

TOPV="$SRCD/top_module_2N.vhd"
AXISV="$SRCD/two2N_axis.vhd"

mkdir -p "$OUT" || { echo "FATAL: cannot create $OUT"; exit 1; }
echo "--- build trees go to: $OUT  ($(df -BG --output=avail "$OUT" | tail -1 | tr -dc '0-9') GB free)"

if [ ! -f "$PLATFORM" ]; then echo "FATAL: no platform: $PLATFORM"; exit 1; fi
for f in "$TOPV" "$AXISV"; do
    if [ ! -f "$f" ]; then echo "FATAL: missing source: $f"; exit 1; fi
done

# gen_xo_sparse.tcl lives at the REPO ROOT in the git tree, but the server
# layout is not necessarily the same -- so search the likely places and fail
# HERE rather than three minutes into phase 1. Override with GENXO=/path/... .
GENXO=${GENXO:-}
if [ -z "$GENXO" ]; then
    for cand in "$REPO/gen_xo_sparse.tcl" "$VITIS/gen_xo_sparse.tcl" \
                "$REPO/../gen_xo_sparse.tcl" "$HOME/gen_xo_sparse.tcl"; do
        if [ -f "$cand" ]; then GENXO="$cand"; break; fi
    done
fi
if [ ! -f "$GENXO" ]; then
    echo "FATAL: gen_xo_sparse.tcl not found. Locate it with"
    echo "         find ~ -name gen_xo_sparse.tcl"
    echo "       then re-run with  GENXO=/full/path/gen_xo_sparse.tcl ..."
    exit 1
fi
echo "--- packaging script: $GENXO"

# The mover .xo files are inputs to every link; a missing one fails 8.5 hours in
# rather than now, so check them up front too.
for f in "$VITIS/krnl_mm2s.hw.xo" "$VITIS/krnl_s2mm.hw.xo"; do
    if [ ! -f "$f" ]; then echo "FATAL: missing mover .xo: $f"; exit 1; fi
done

free_gb ()      { free -g | awk '/^Mem:/ {print $7}'; }
# Check the filesystem the builds actually land on, not /home by name -- OUT
# may well be somewhere else, and guarding the wrong mount is worse than no
# guard because it reads as reassurance.
out_free_gb () { df -BG --output=avail "$OUT" | tail -1 | tr -dc '0-9'; }

# Restore the pristine generics whatever happens -- an interrupted run must not
# leave the shared source tree set to some sweep point. Every later build in
# this repo would silently be the wrong design.
cp "$TOPV"  "$OUT/top_module_2N.vhd.orig"
cp "$AXISV" "$OUT/two2N_axis.vhd.orig"
restore () {
    cp "$OUT/top_module_2N.vhd.orig" "$TOPV"
    cp "$OUT/two2N_axis.vhd.orig"    "$AXISV"
    echo "--- source generics restored to 8x8"
}
trap restore EXIT INT TERM

# Assert that EVERY occurrence of a generic agrees with the target.
#
# WHY NOT JUST CHECK THE FIRST ONE. `BLOCKS_NUM : integer := N` appears more
# than once per file -- once in the entity generic block, and again in the
# COMPONENT DECLARATIONS inside the architecture. sed rewrites all of them,
# which is what we want: if it updated only the entity, the component defaults
# would silently disagree with it. So the check must be "all occurrences equal
# the target", not "the first occurrence equals the target". An earlier version
# compared a multi-line grep result against a single number and reported a
# FATAL on a sed that had in fact worked perfectly.
check_generic () {   # $1=file  $2=generic name  $3=expected value
    local vals n
    vals=$(grep -oE "$2[[:space:]]*:[[:space:]]*integer[[:space:]]*:=[[:space:]]*[0-9]+" "$1"            | grep -oE "[0-9]+$" | sort -u)
    if [ -z "$vals" ]; then
        echo "FATAL: no $2 declaration found in $(basename "$1")"
        return 1
    fi
    n=$(printf '%s' "$vals" | grep -c .)
    if [ "$n" -ne 1 ] || [ "$vals" != "$3" ]; then
        echo "FATAL: $2 in $(basename "$1") is [$(printf '%s ' $vals)], wanted $3"
        return 1
    fi
    return 0
}

# W_IDX IS THE THIRD GENERIC, AND IT IS NOT OPTIONAL.
#
# c_core's port is  W_row : std_logic_vector((W_IDX*EL_SIZE)-1 downto 0)  while
# its generate loop slices  ((i+1)*2*EL_SIZE)-1 downto (i*2*EL_SIZE)  for i in
# 0..BLOCKS_NUM-1. Those only agree when
#
#     W_IDX = 2 x BLOCKS_NUM          (16 at the 8-block default)
#
# Leaving W_IDX at 16 while changing BLOCKS_NUM produced, at 4x16:
#     [Synth 8-97] array index 287 out of range [c_core_4.0.vhd:186]
# because block 8 asks for bit 287 of a 256-bit port. The mirror case is just
# as broken: at 16x4 the TOP slices W_row_int up to bit 4095 of a 2048-bit bus.
#
# The total stays 2048 bits either way -- CORES x W_IDX x EL_SIZE = C x 2B x 16
# = 2048 for every point on the C x B = 64 diagonal -- which is exactly why the
# external interface is invariant while the INTERNAL partitioning is not.
set_generics () {   # $1=CORES $2=BLOCKS
    local c=$1 b=$2 f
    local w=$((2 * b))
    for f in "$TOPV" "$AXISV"; do
        sed -i -E "s/(CORES_NUM[[:space:]]*:[[:space:]]*integer[[:space:]]*:=[[:space:]]*)[0-9]+/\1$c/" "$f"
        sed -i -E "s/(BLOCKS_NUM[[:space:]]*:[[:space:]]*integer[[:space:]]*:=[[:space:]]*)[0-9]+/\1$b/" "$f"
        sed -i -E "s/(W_IDX[[:space:]]*:[[:space:]]*integer[[:space:]]*:=[[:space:]]*)[0-9]+/\1$w/" "$f"
    done
    # Verify BOTH files, BOTH generics. A silently-unmatched sed would build 8x8
    # N times and the only symptom would be N identical results.
    for f in "$TOPV" "$AXISV"; do
        check_generic "$f" CORES_NUM  "$c" || exit 1
        check_generic "$f" BLOCKS_NUM "$b" || exit 1
        check_generic "$f" W_IDX      "$w" || exit 1
    done
    echo "--- generics verified: CORES_NUM=$c BLOCKS_NUM=$b W_IDX=$w in both files"
}

# ============================ PHASE 1: package .xo ==========================
echo "=============================================================="
echo " PHASE 1 -- packaging one .xo per configuration (sequential)"
echo "=============================================================="

for cfg in "${CONFIGS[@]}"; do
    set -- $cfg; C=$1; B=$2; TAG="${C}x${B}"
    echo ""
    echo "--- .xo for CORES=$C BLOCKS=$B  ($TAG)"
    set_generics "$C" "$B"

    cd "$REPO" || exit 1
    # gen_xo_sparse.tcl reads ::GEN_XO_TAG from the interpreter, and does not
    # parse argv -- so set it in a tiny wrapper and source the real script.
    cat > "$OUT/_genxo_${TAG}.tcl" <<TCLEOF
set ::GEN_XO_TAG _${TAG}
source $GENXO
TCLEOF
    vivado -mode batch -notrace -source "$OUT/_genxo_${TAG}.tcl" \
        2>&1 | tee "$OUT/genxo_${TAG}.log" | tail -5

    if [ ! -s "$REPO/krnl_gemv_sparse_${TAG}.xo" ]; then
        echo "FATAL: no .xo produced for $TAG -- see $OUT/genxo_${TAG}.log"
        exit 1
    fi
    echo "--- ok: krnl_gemv_sparse_${TAG}.xo  ($(du -h "$REPO/krnl_gemv_sparse_${TAG}.xo" | cut -f1))"
    # The packaging project is scaffolding once the .xo exists, and it lives on
    # /home which is the filesystem under pressure. Six of these would be GBs.
    if [ -d "$REPO/xo_build_${TAG}" ]; then
        echo "--- reclaiming $(du -sh "$REPO/xo_build_${TAG}" | cut -f1) from xo_build_${TAG}"
        rm -rf "$REPO/xo_build_${TAG}"
    fi
    echo "--- /home now: $(df -BG --output=avail /home | tail -1 | tr -dc '0-9') GB free"
done

restore
trap - EXIT INT TERM

# ============================ PHASE 2: parallel links =======================
echo ""
echo "=============================================================="
echo " PHASE 2 -- v++ links, up to $MAXPAR at a time"
echo " expect ~8.5 h each; ~20 GB RAM each; own _x tree each"
echo "=============================================================="

pids=()
launch () {   # $1=TAG
    local TAG=$1
    local TD="$OUT/tmp_$TAG"
    mkdir -p "$TD"
    echo "--- launching $TAG at $(date)  (free RAM $(free_gb) GB, $OUT $(out_free_gb) GB)"
    (
        # cd into the per-link temp dir, NOT $VITIS. v++ writes a .Xil scratch
        # tree into its working directory on top of --temp_dir. Every .cfg and
        # .xo below is absolute, so the working directory is free to be here.
        cd "$TD" || exit 1
        v++ -t hw --platform "$PLATFORM"             --config "$VITIS/sparse_hbm.cfg" --config "$VITIS/impl_opt.cfg"             --config "$VITIS/slr_floorplan.cfg"             --kernel_frequency 300             --temp_dir "$TD" --log_dir "$TD/logs" --report_dir "$TD/reports"             -l -o "$OUT/gemv_sparse_${TAG}.xclbin"             "$VITIS/krnl_mm2s.hw.xo" "$REPO/krnl_gemv_sparse_${TAG}.xo"             "$VITIS/krnl_s2mm.hw.xo"             > "$OUT/link_${TAG}.log" 2>&1
        rc=$?

        # RESCUE THE REPORTS BEFORE THE TREE GOES. This is the mistake made on
        # the floorplan builds: _x was deleted and the timing/utilization
        # reports went with it, leaving only the numbers that happened to be on
        # screen. They are a few MB against a 4.8 GB tree.
        mkdir -p "$OUT/reports_$TAG"
        cp -r "$TD/reports/." "$OUT/reports_$TAG/" 2>/dev/null
        find "$TD" -path "*impl_1*" -name "*.rpt" -exec cp {} "$OUT/reports_$TAG/" \; 2>/dev/null

        # DELETE THE TREE AS SOON AS THIS LINK IS DONE, not at the end of the
        # whole run. Without this, peak disk is ALL SIX trees (28.8 GB); with
        # it, peak is only the concurrent ones (MAXPAR x 4.8 GB). On a /home
        # that is full, that difference decides whether the batch fits at all.
        # A FAILED link keeps its tree -- that is when you need to inspect it.
        if [ $rc -eq 0 ]; then
            rm -rf "$TD"
            echo "--- $TAG done $(date), tree reclaimed, $OUT now $(df -BG --output=avail "$OUT" | tail -1 | tr -dc '0-9') GB free"
        else
            echo "--- $TAG FAILED $(date) -- tree KEPT at $TD for inspection"
        fi
        exit $rc
    ) &
    pids+=($!)
}

for cfg in "${CONFIGS[@]}"; do
    set -- $cfg; C=$1; B=$2; TAG="${C}x${B}"

    while [ "$(jobs -rp | wc -l)" -ge "$MAXPAR" ]; do sleep 60; done

    have_ram=$(free_gb); have_home=$(out_free_gb)
    if [ "$have_ram" -lt "$MIN_RAM_GB" ] || [ "$have_home" -lt "$MIN_OUT_GB" ]; then
        echo "WAITING: RAM ${have_ram}GB (need $MIN_RAM_GB), /home ${have_home}GB (need $MIN_OUT_GB)"
        while [ "$(free_gb)" -lt "$MIN_RAM_GB" ] || [ "$(out_free_gb)" -lt "$MIN_OUT_GB" ]; do
            sleep 300
        done
    fi
    launch "$TAG"
    sleep 120     # stagger: the first minutes of a link are the I/O-heaviest
done

echo ""
echo "--- all launched; waiting. Monitor with:"
echo "      tail -f $OUT/link_*.log"
echo "      watch -n 60 'free -h; df -h /home'"
wait

echo ""
echo "=============================================================="
echo " DONE $(date)"
for cfg in "${CONFIGS[@]}"; do
    set -- $cfg; TAG="${1}x${2}"
    if [ -s "$OUT/gemv_sparse_${TAG}.xclbin" ]; then
        echo "  OK      $TAG  $(du -h "$OUT/gemv_sparse_${TAG}.xclbin" | cut -f1)"
    else
        echo "  FAILED  $TAG  -- see $OUT/link_${TAG}.log (timing failure emits NO xclbin)"
    fi
done
echo "=============================================================="
echo " Reclaim space when done:  rm -rf $OUT/tmp_*"

#!/usr/bin/env bash
# Rebuild both designs with --save-temps so the ROUTED CHECKPOINT survives, then
# extract it and clean up. Vitis deletes that checkpoint by default, which is why
# no device-view screenshot has been possible: every existing build kept only
# per-IP checkpoints, never the placed-and-routed system.
#
# WHY THIS IS A SCRIPT AND NOT A ONE-LINER. /home is 96% full (73 GB). A
# --save-temps link keeps the whole _x tree, 30-50 GB per design, so two of them
# do not fit. This builds ONE design, copies out the ~1 GB checkpoint, deletes
# the 30-50 GB tree, and only then starts the second. Filling /home on a machine
# with ten users is a bigger problem than a failed build, so every step is
# guarded and the script stops rather than pushing on.
#
# WHICH BUILDS THESE REPRODUCE. The ones that were actually MEASURED, not
# idealised ones:
#   sparse @300 WITH slr_floorplan.cfg   -> matches gemv_sparse_slr.xclbin
#   dense  @300 WITHOUT any floorplan    -> matches gemv_dense.xclbin
# That asymmetry is deliberate and is itself the result: dense closes 300 MHz
# with its engine in SLR0 alongside the movers; sparse could not, and needed its
# own die. The two pictures should show different placements.
#
# Run it detached:  tmux new -s floorplan   then   bash build_floorplan.sh
# Expect 3-5 hours per design.

set -u

PLATFORM=/opt/xilinx/platforms/xilinx_u280_xdma_201920_3/xilinx_u280_xdma_201920_3.xpfm
DCPDIR=~/floorplan_dcp
MIN_GB=60          # refuse to start a link with less than this free

# The OTHER u280 platform on this machine (gen3x16_..._202211_1) is the poison
# one recorded in the project notes -- always pass the absolute .xpfm.
if [ ! -f "$PLATFORM" ]; then
    echo "FATAL: platform not found: $PLATFORM"; exit 1
fi

free_gb () { df -BG --output=avail /home | tail -1 | tr -dc '0-9'; }

check_space () {
    local have; have=$(free_gb)
    echo "--- /home free: ${have} GB (need >= ${MIN_GB})"
    if [ "$have" -lt "$MIN_GB" ]; then
        echo "FATAL: not enough free space to start this link. Stopping so that"
        echo "       /home is not filled on a shared machine."
        exit 1
    fi
}

# $1=tag  $2=work dir  $3..=v++ args
build () {
    local tag=$1; shift
    local dir=$1; shift
    echo "==================================================================="
    echo "  $tag  --  started $(date)"
    echo "==================================================================="
    check_space
    cd "$dir" || exit 1
    rm -rf _x                                  # stale tree from a previous link
    if ! v++ "$@"; then
        echo "FATAL: v++ failed for $tag"; exit 1
    fi
    mkdir -p "$DCPDIR"
    local dcp
    dcp=$(find _x -name "*routed*.dcp" -printf '%s %p\n' 2>/dev/null \
          | sort -rn | head -1 | cut -d' ' -f2-)
    if [ -z "$dcp" ]; then
        echo "FATAL: no routed .dcp under _x for $tag."
        echo "       NOT deleting _x -- inspect it by hand:"
        find _x -name "*.dcp" | head -20
        exit 1
    fi
    cp "$dcp" "$DCPDIR/${tag}_routed.dcp" || exit 1
    if [ ! -s "$DCPDIR/${tag}_routed.dcp" ]; then
        echo "FATAL: copied checkpoint is empty; keeping _x"; exit 1
    fi
    echo "--- saved $(du -h "$DCPDIR/${tag}_routed.dcp" | cut -f1) -> $DCPDIR/${tag}_routed.dcp"
    echo "--- reclaiming $(du -sh _x | cut -f1) from _x"
    rm -rf _x
    echo "--- $tag done $(date), /home free now $(free_gb) GB"
}

build sparse ~/GEMV_Sparse/Vitis \
    -t hw --platform "$PLATFORM" \
    --config sparse_hbm.cfg --config impl_opt.cfg --config slr_floorplan.cfg \
    --kernel_frequency 300 --save-temps -l -o gemv_sparse_fp.xclbin \
    krnl_mm2s.hw.xo ~/GEMV_Sparse/krnl_gemv_sparse_fix.xo krnl_s2mm.hw.xo

# DENSE TAKES dense_hbm.cfg AND NOTHING ELSE. Not a simplification -- the build
# that was measured (INTEGRATION_STEPS S19) used exactly one config file. An
# earlier version of this script also passed impl_opt.cfg here, which failed
# outright because that file lives only in the SPARSE working directory. That
# failure was lucky: copying the file across would have linked cleanly and
# produced a floorplan of a design that was never measured. Dense closes 300 MHz
# without any phys-opt help; sparse needs it. That asymmetry IS the result.
build dense ~/GEMV_Dense/Vitis \
    -t hw --platform "$PLATFORM" \
    --config dense_hbm.cfg \
    --kernel_frequency 300 --save-temps -l -o gemv_dense_fp.xclbin \
    krnl_mm2s.hw.xo ~/GEMV_Dense/krnl_gemv_dense.xo krnl_s2mm.hw.xo

echo
echo "==================================================================="
echo "  BOTH DONE $(date)"
ls -la "$DCPDIR"
echo
echo "  Open in Vivado:   open_checkpoint $DCPDIR/sparse_routed.dcp"
echo "  then Window > Device for the floorplan view."
echo "==================================================================="

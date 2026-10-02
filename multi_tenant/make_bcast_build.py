"""Generate the link config, floorplan, build guide and scripts of a BROADCAST-VECTOR build:
N engines fed ONE activation vector by ONE broadcast mover pair.

    python make_bcast_build.py --tenants 6 --clock 375          # -> multi_tenant/x6_bcast/
    python make_bcast_build.py --tenants 7 --clock 375          # -> multi_tenant/x7_bcast/
    python make_bcast_build.py --tenants 2 --config 8x8         # -> multi_tenant/8x8_x2_bcast/
    python make_bcast_build.py --tenants 3                      # -> multi_tenant/x3_bcast/
    python make_bcast_build.py --tenants 3 --config 8x4 --clock 375 --floorplan split
                                                                # -> multi_tenant/8x4_x3_bcast/
    python make_bcast_build.py --tenants 2 --config 16x3 --clock 375 --floorplan split
                                                                # -> multi_tenant/16x3_x2_bcast/

8x4_x3_bcast and 16x3_x2_bcast (added 2026-09-30): the broadcast versions of the shared twins
3x8x4_shared and 2x16x3_shared, with the twins' target (375) and floorplan (split). No new
kernel: bcast3 / bcast2 are the .xo already built for x3_bcast / 8x8_x2_bcast, which
setup_bcast_twins.sh copies from those folders (see TWIN_SETUP). Same day, START_BUILD's
30-minute check was fixed: it grepped _x/logs/link/vivado.log, which Vitis writes only when the
link ENDS (so its synthesis line was always empty), and summed with bc; it now reads the logs
under _x/link while the link runs and counts with grep -c. Folders generated before
(x3/x6/x7/8x8_x2_bcast) keep the old check -- they are kept exactly as they were linked; apart
from those lines they regenerate byte for byte.

--layout writes-last (added 2026-10-01, 2x8x8_bcast relink -> multi_tenant/8x8_x2_bcast_wl/):
    python make_bcast_build.py --tenants 2 --config 8x8 --layout writes-last
The first 2x8x8_bcast (per-engine layout) READS BACK WRONG from the host: on every one of its 32 HBM
banks, bits 16, 17, 134, 219 of every even 32-byte beat come back 0 (workload/hbm_dma_test.cpp, no
kernel involved; the single 8x8 xclbin on the same card is clean). The only other 32-port build,
the single 4x32, is clean, and its layout puts every read port low and all 8 WRITE ports on
HBM[24..31] -- the per-engine layout puts engine 0's write ports on HBM[13..16], straddling the two
HBM stacks. (The straddle ALONE is not the cause: the single 8x8, Vitis/sparse_hbm.cfg, also writes
on HBM[13..16] at 325 MHz and reads back clean. Only the faulty build has all 32 ports AND writes in
13..16 -- and a relink is also a fresh place-and-route, so a clean relink cannot tell the two apart.)
writes-last copies the known-good layout: the vector pair on HBM[0..1], every engine's
weights + indices next, ALL output (write) ports on the highest channels. Same movers, same
bandwidth per mover; the host finds every bank by name (kernel.group_id), so it needs no change.

x3_bcast (added 2026-09-29, the first broadcast build to bring up): its kernel
krnl_mm2s_bcast3 lives in its own file (Vitis/krnl_mm2s_bcast3.cpp), compiled by
make_bcast3_xo.sh, set up by setup_bcast3_build.sh -- see KERNEL_FILES. Adding it changed
nothing for 2/6/7: their folders regenerate byte for byte.

A COPY OF make_multi_build.py (2026-09-28), edited; make_multi_build.py is untouched and still
generates every other multi-tenant build (x2..x5, 8x4_x2/x3, 16x3_x2 and the *_shared twins).

WHY. On the U280 every mover's m_axi takes one of the HBM subsystem's 32 kernel ports, and v++
never shares one -- not even between movers that read the same channel. The shared-vector
layout (make_multi_build.py --shared-vector) keeps an activation-mover pair per tenant, so
6 x 4x4 needs 36 ports, 7 x 4x4 42 and 2 x 8x8 34, and 6 x 4x4 failed to link ("All 33
connections are used", 2026-09-28). Here the tenants have NO activation movers: ONE pair of
broadcast movers (Vitis/krnl_mm2s_bcast.cpp, kernel krnl_mm2s_bcast<N>: one m_axi, N AXIS
outputs) reads the vector from HBM[0] / HBM[1] and streams every beat to every engine at once.

    movers = n x (weights + indices + outputs) + 2        HBM channels: the same number
        6 x 4x4 -> 26      7 x 4x4 -> 30      2 x 8x8 -> 32     (of 32 ports, 32 channels)

LAYOUT -- the shared-vector layout, unchanged: the vector in HBM[0] (elements 0-15 of each
32-element window) and HBM[1] (16-31); tenant k owns HBM[2 + k*m .. 2 + k*m + m-1],
m = its weight + index + output channels, in that order.

WHAT IS NEW, AND ONLY THIS: the kernel krnl_mm2s_bcast<N> (one .xo per engine count, made
once by make_bcast_xo.sh) and its stream_connect lines mm2s_bcast_a<i>.out<k> ->
gemv_t<k>.s_axis_a<i>. The engine .xo and the weight / index / output movers are the ones every
build used. The host is workload/host_workload_bcast.cpp (lockstep, one vector copy), the
gate and measurement workload/run_workload_bcast.py -- MoE only: with ONE vector for all
engines, independent tenants (prep_tenants.py + host_sparse_multi) cannot run on this build.

CU NAMES: mm2s_w<i>_t<k>, mm2s_i<i>_t<k>, gemv_t<k>, s2mm_c<i>_t<k> as in every multi-tenant
build, and mm2s_bcast_a0, mm2s_bcast_a1 -- one pair for the whole card.

Each folder gets: sparse_hbm_<stem>.cfg, both floorplans, BUILD.md, run_hw_emu.sh (step 3,
the hw_emu gate, one command) and start_build.sh (step 4, the hardware link, one command).
"""

import argparse
import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- the tenant's engine shape. configure() sets these; 4x4 is the default ----
TAG = "4x4"
CORES, BLOCKS = 4, 4
W_PCS, IND_PCS, A_PCS, C_PCS = 2, 1, 2, 1
PCS_PER_TENANT = W_PCS + IND_PCS + C_PCS              # 4: a tenant has no vector movers here
HBM_PCS = 32
DEFAULT_CLOCK = 400                                   # what 4x4 closed at, alone
LAYOUT = "per-engine"                                 # or "writes-last" (main() sets it)

# Measured on the single-tenant 4x4 build (reports_4x4_400_slr0, kernel util):
GEMV_LUT, GEMV_BRAM = 17977, 31
SLR0_BRAM_TILES = 672

# ANY FAMILY SHAPE CAN BE THE TENANT (as in make_multi_build.py): cores, blocks, engine LUT
# and BRAM, and the clock it closed at as a SINGLE tenant.
CONFIGS = {
    "4x4":  (4, 4, 17977, 31, 400),
    "8x4":  (8, 4, 35438, 45, 375),
    "16x3": (16, 3, 54733, 60, 350),
    "8x8":  (8, 8, 66176, 74, 325),
    "4x24": (4, 24, 94823, 103, 300),
    "4x32": (4, 32, 125614, 133, 250),
}
# Per mover CU, as in make_multi_build.py. The broadcast mover has ONE m_axi with the same
# burst settings as krnl_mm2s, so it is costed as one mover (its extra AXIS outputs are
# registers, not BRAM).
MOVER_LUT_EACH, MOVER_BRAM_EACH = 1614.33, 14
A_PCS_ALWAYS = 2                          # one 32-element window = 512 bits, every shape
SPLIT_ABOVE = 0.85                        # single-die BRAM share that forces the split
HBM_KERNEL_PORTS = 32                     # 33 hmss_0 connections minus the platform's
# The engine counts that have a broadcast kernel, and for each: the file the kernel lives in,
# the script that compiles its .xo, the script that sets its build folder up. 2/6/7 are the
# first three builds (2026-09-28); 3 (x3_bcast, 2026-09-29) got files of its own so the
# verified 2/6/7 source and .xo stay untouched. Another count: a new kernel file first.
KERNEL_FILES = {
    2: ("krnl_mm2s_bcast.cpp", "make_bcast_xo.sh", "setup_bcast_builds.sh"),
    3: ("krnl_mm2s_bcast3.cpp", "make_bcast3_xo.sh", "setup_bcast3_build.sh"),
    6: ("krnl_mm2s_bcast.cpp", "make_bcast_xo.sh", "setup_bcast_builds.sh"),
    7: ("krnl_mm2s_bcast.cpp", "make_bcast_xo.sh", "setup_bcast_builds.sh"),
}
BCAST_KERNELS = tuple(sorted(KERNEL_FILES))
# The broadcast versions of the shared twins (2026-09-30) compile nothing: their kernel's .xo is
# the one already built for (and passed in) another broadcast build, copied from that build's
# folder by setup_bcast_twins.sh. build dir -> the folder its bcast .xo comes from.
TWIN_SETUP = {"8x4_x3_bcast": ("x3_bcast", "setup_bcast_twins.sh", "for both broadcast twins"),
              "16x3_x2_bcast": ("8x8_x2_bcast", "setup_bcast_twins.sh", "for both broadcast twins"),
              "8x8_x2_bcast_wl": ("8x8_x2_bcast", "setup_bcast_8x8_wl.sh", "for this relink")}


def ceildiv(a, b):
    return -(-a // b)


def configure(tag):
    """Every width of one engine shape, exactly as the family generator derives them."""
    global TAG, CORES, BLOCKS, W_PCS, IND_PCS, A_PCS, C_PCS, PCS_PER_TENANT
    global GEMV_LUT, GEMV_BRAM, DEFAULT_CLOCK
    TAG = tag
    CORES, BLOCKS, GEMV_LUT, GEMV_BRAM, DEFAULT_CLOCK = CONFIGS[tag]
    T = CORES * BLOCKS
    W_PCS = ceildiv(32 * T, 256)
    IND_PCS = ceildiv(10 * T + 2, 256)
    A_PCS = A_PCS_ALWAYS
    C_PCS = ceildiv(16 * T, 256)
    PCS_PER_TENANT = W_PCS + IND_PCS + C_PCS


def movers(n):
    """Every mover CU = every HBM kernel port: each tenant's weight, index and output movers,
    and the broadcast pair."""
    return n * PCS_PER_TENANT + A_PCS


def single_die_bram(n):
    """SLR0 BRAM if everything goes in one die: movers + engines + ~30 for the platform."""
    return movers(n) * MOVER_BRAM_EACH + n * GEMV_BRAM + 30


def split_slr0_bram(n):
    """SLR0 BRAM with the engines in SLR1: the movers + ~30 for the platform."""
    return movers(n) * MOVER_BRAM_EACH + 30


def own_pcs():
    """HBM channels one tenant owns: weights, indices, outputs (the vector is shared)."""
    return W_PCS + IND_PCS + C_PCS


def total_pcs(n):
    return n * own_pcs() + A_PCS


def tenant_base(k):
    """First HBM channel of tenant k (after the vector)."""
    return A_PCS + k * own_pcs()


def layout_suffix():
    return "_wl" if LAYOUT == "writes-last" else ""


def stem(n):
    """<shape>_x<n>_bcast[_wl] -- the part every file name of this build carries."""
    return "%s_x%d_bcast%s" % (TAG, n, layout_suffix())


def build_dir(n):
    """x6_bcast for 4x4 (as x2..x5 for the original study), <shape>_x<n>_bcast otherwise;
    + _wl for the writes-last layout."""
    return ("x%d" % n if TAG == "4x4" else "%s_x%d" % (TAG, n)) + "_bcast" + layout_suffix()


def writes_last_map(n):
    """writes-last: {cu: (port, HBM index)} -- every engine's weights + indices from HBM[2] up,
    engine by engine, then every engine's outputs on the highest channels."""
    w, ind, g, c, b = cu_names(n)
    m, pc = {}, A_PCS
    for k in range(n):
        for name in w[k] + ind[k]:
            m[name] = ("in", pc)
            pc += 1
    for k in range(n):
        for name in c[k]:
            m[name] = ("out", pc)
            pc += 1
    return m


def arch_name(n):
    """The name in workload/plan_workload_bcast.py BCAST_ARCHS."""
    return "%dx%s_bcast" % (n, TAG)


def bcast_kernel(n):
    return "krnl_mm2s_bcast%d" % n


def cu_names(n):
    """Every CU name, grouped, in the order the link config declares them."""
    w = [["mm2s_w%d_t%d" % (i, k) for i in range(W_PCS)] for k in range(n)]
    ind = [["mm2s_i%d_t%d" % (i, k) for i in range(IND_PCS)] for k in range(n)]
    g = ["gemv_t%d" % k for k in range(n)]
    c = [["s2mm_c%d_t%d" % (i, k) for i in range(C_PCS)] for k in range(n)]
    b = ["mm2s_bcast_a%d" % i for i in range(A_PCS)]
    return w, ind, g, c, b


def link_cfg(n, clock):
    w, ind, g, c, b = cu_names(n)
    fwd = []
    for k in range(n):
        fwd += w[k] + ind[k]
    L = []
    L.append("# " + "-" * 74)
    L.append("# sparse_hbm_%s.cfg -- %d %s tenants on one U280, fed ONE activation"
             % (stem(n), n, TAG))
    L.append("# vector by ONE BROADCAST MOVER PAIR.")
    L.append("#")
    L.append("# GENERATED by multi_tenant/make_bcast_build.py. Tenant k = one complete")
    L.append("# %s engine (%d cores x %d blocks, %d DSPs) with its own matrix, its own"
             % (TAG, CORES, BLOCKS, 8 * CORES * BLOCKS))
    if LAYOUT == "writes-last":
        L.append("# sparsity mode and its own %d HBM pseudo-channels -- LAYOUT writes-last: the"
                 % own_pcs())
        L.append("# weights + indices of every engine from HBM[2] up, ALL outputs on the highest")
        L.append("# channels (the layout of the single 4x32, the other 32-port build that reads")
        L.append("# back correctly). The vector: ONE copy in HBM[0] and HBM[1].")
    else:
        L.append("# sparsity mode and its own %d HBM pseudo-channels HBM[2+%d*k .. 2+%d*k+%d]"
                 % (own_pcs(), own_pcs(), own_pcs(), own_pcs() - 1))
        L.append("# (weights, indices, outputs). The vector: ONE copy in HBM[0] and HBM[1].")
    L.append("#")
    L.append("# %d tenants  ->  %d of 32 HBM channels, %d mover CUs (= %d of the %d HBM kernel"
             % (n, total_pcs(n), movers(n), movers(n), HBM_KERNEL_PORTS))
    L.append("# ports) + %d engines = %d CUs. (A mover pair per tenant would need %d ports.)"
             % (n, movers(n) + n, n * (PCS_PER_TENANT + A_PCS)))
    L.append("#")
    L.append("# THE ENGINE .xo IS UNCHANGED: krnl_gemv_sparse_%s.xo, %d MHz as a single"
             % (TAG, DEFAULT_CLOCK))
    L.append("# tenant, instantiated %d times; so are krnl_mm2s and krnl_s2mm." % n)
    L.append("#")
    L.append("# NEW: %s (Vitis/%s) -- one m_axi, %d AXIS outputs."
             % (bcast_kernel(n), KERNEL_FILES[n][0], n))
    L.append("# mm2s_bcast_a0 reads HBM[0] and sends every beat to every engine's s_axis_a0;")
    L.append("# mm2s_bcast_a1 reads HBM[1] -> every s_axis_a1. The tenants have NO activation")
    L.append("# movers, so every engine gets the SAME vector, always: the host must run every")
    L.append("# engine on every calculation, in lockstep (workload/host_workload_bcast.cpp).")
    L.append("#")
    L.append("# Used as:  v++ -l --config sparse_hbm_%s.cfg --config impl_family.cfg \\"
             % stem(n))
    L.append("#                  --config slr_floorplan_%s[_split].cfg --kernel_frequency %d ..."
             % (stem(n), clock))
    L.append("# " + "-" * 74)
    L.append("")
    L.append("[connectivity]")
    L.append("")
    L.append("# ---- compute units ----------------------------------------------------")
    L.append("# Names are explicit. Positional naming across %d movers would make one"
             % len(fwd))
    L.append("# transposed digit bind a tenant's index channel to another's weight")
    L.append("# stream -- wrong answers on both, no error.")
    L.append("nk=krnl_mm2s:%d:%s" % (len(fwd), ".".join(fwd)))
    L.append("nk=%s:%d:%s" % (bcast_kernel(n), len(b), ".".join(b)))
    L.append("nk=krnl_gemv_sparse:%d:%s" % (n, ".".join(g)))
    flat_c = [name for k in range(n) for name in c[k]]
    L.append("nk=krnl_s2mm:%d:%s" % (len(flat_c), ".".join(flat_c)))
    L.append("")
    L.append("# ---- memory binding: THE VECTOR, ONE copy in HBM[0] and HBM[1] ---------")
    L.append("# Read by the broadcast pair only: one master per channel, as everywhere else.")
    for i, name in enumerate(b):
        L.append("sp=%s.in:HBM[%d]" % (name, i))
    L.append("")
    if LAYOUT == "writes-last":
        m = writes_last_map(n)
        L.append("# ---- memory binding, writes-last: inputs from HBM[2] up, outputs last ----")
        for k in range(n):
            L.append("")
            L.append("# tenant %d: weights + indices" % k)
            for name in w[k] + ind[k]:
                L.append("sp=%s.in:HBM[%d]" % (name, m[name][1]))
        for k in range(n):
            L.append("")
            L.append("# tenant %d: outputs" % k)
            for name in c[k]:
                L.append("sp=%s.out:HBM[%d]" % (name, m[name][1]))
    L.append("# ---- memory binding: tenant k owns HBM[2+%d*k .. 2+%d*k+%d] -------------"
             % (own_pcs(), own_pcs(), own_pcs() - 1) if LAYOUT != "writes-last" else "")
    for k in range(n if LAYOUT != "writes-last" else 0):
        base = tenant_base(k)
        last = base + own_pcs() - 1
        L.append("")
        L.append("# tenant %d -> HBM[%d..%d]%s"
                 % (k, base, last,
                    "   (straddles the two HBM stacks at PC 16)"
                    if base < 16 <= last else ""))
        pc = base
        for name in w[k] + ind[k]:
            L.append("sp=%s.in:HBM[%d]" % (name, pc))
            pc += 1
        for name in c[k]:
            L.append("sp=%s.out:HBM[%d]" % (name, pc))
            pc += 1
    L.append("")
    L.append("# ---- kernel-to-kernel streams, WITHIN each tenant ---------------------")
    L.append("# A mistyped port name here is a link WARNING, not an error: the stream is")
    L.append("# left dangling and that tenant's barrier join waits forever for a channel")
    L.append("# that never arrives. If one tenant hangs and the others finish, look here")
    L.append("# first -- its join waits on ALL %d weight+index PCs." % (W_PCS + IND_PCS))
    for k in range(n):
        L.append("")
        L.append("# tenant %d" % k)
        for i, name in enumerate(w[k]):
            L.append("stream_connect=%s.out:gemv_t%d.s_axis_w%d" % (name, k, i))
        for i, name in enumerate(ind[k]):
            L.append("stream_connect=%s.out:gemv_t%d.s_axis_ind%d" % (name, k, i))
        for i, name in enumerate(c[k]):
            L.append("stream_connect=gemv_t%d.m_axis_c%d:%s.in" % (k, i, name))
    L.append("")
    L.append("# ---- the broadcast: output k of each broadcast mover -> engine k --------")
    L.append("# A missing or mistyped line here is a link WARNING, not an error, and it")
    L.append("# hangs EVERYTHING: that engine never gets its vector, and the broadcast mover")
    L.append("# stalls on the dangling output (every output must accept every beat).")
    for i, name in enumerate(b):
        L.append("")
        L.append("# %s (HBM[%d], elements %d-%d of each window) -> every s_axis_a%d"
                 % (name, i, 16 * i, 16 * i + 15, i))
        for k in range(n):
            L.append("stream_connect=%s.out%d:gemv_t%d.s_axis_a%d" % (name, k, k, i))
    L.append("")
    L.append("# ---- kernel clock -----------------------------------------------------")
    L.append("# NOT SET HERE -- a [clock] section maps to v++ --clock, which this")
    L.append("# platform rejects. Use  v++ -l ... --kernel_frequency %d" % clock)
    L.append("# ONE clock for every tenant: tenants share the card, so they share the")
    L.append("# frequency. A tenant cannot be clocked independently of its neighbours.")
    return "\n".join(L) + "\n"


def floorplan_cfg(n, split, why=""):
    """All CUs in SLR0, or the engines in SLR1 with every mover (and the pair) in SLR0.

    why = "" (single die is the choice), "bram" (the BRAM rule forced the split) or
    "explicit" (split chosen by hand) -- it decides what each file says about itself.
    """
    w, ind, g, c, b = cu_names(n)
    L = []
    L.append("# " + "-" * 74)
    if split:
        total = single_die_bram(n)
        pct = 100.0 * total / SLR0_BRAM_TILES
        s0 = split_slr0_bram(n)
        L.append("# slr_floorplan_%s_split.cfg -- the engines get SLR1." % stem(n))
        L.append("#")
        if why == "explicit":
            L.append("# USE THIS ONE (chosen explicitly: --floorplan split).")
        elif pct > 100.0 * SPLIT_ABOVE:
            L.append("# USE THIS ONE AT THIS SIZE. Everything in SLR0 would need ~%d of %d"
                     % (total, SLR0_BRAM_TILES))
            L.append("# BRAM (%d%%), and that die stops working above ~%d%%: 5 x 4x4 at 92%% fell"
                     % (round(pct), round(100.0 * SPLIT_ABOVE)))
            L.append("# to 260 MHz AND hung one tenant on the card, while the same design split")
            L.append("# (SLR0 at 69%) closed 329 MHz with all five tenants bit-exact.")
        else:
            L.append("# NOT THE DEFAULT at this size. Use slr_floorplan_%s.cfg (everything"
                     % stem(n))
            L.append("# in SLR0) first and switch only if the build misses its frequency.")
        L.append("#")
        L.append("# The %d engines are %s LUT and %d BRAM together -- they fit one die"
                 % (n, format(n * GEMV_LUT, ","), n * GEMV_BRAM))
        L.append("# easily. The movers stay in SLR0 beside the HBM they master, the broadcast")
        L.append("# pair with them; its %d outputs cross to SLR1 like every mover's stream." % n)
        L.append("#")
        L.append("# SLR0 then holds the %d movers and the platform: ~%d of %d BRAM (%d%%)."
                 % (movers(n), s0, SLR0_BRAM_TILES, round(100.0 * s0 / SLR0_BRAM_TILES)))
        if s0 > SPLIT_ABOVE * SLR0_BRAM_TILES:
            L.append("#   ^^ ABOVE %d%% EVEN SPLIT: the level at which 5 x 4x4 on one die fell"
                     % round(100 * SPLIT_ABOVE))
            L.append("#      to 260 MHz and hung a tenant. Expect this build to fail.")
    else:
        L.append("# slr_floorplan_%s.cfg -- every compute unit in SLR0." % stem(n))
        L.append("#")
        if why:
            L.append("# NOT USED BY THIS BUILD -- it uses slr_floorplan_%s_split.cfg."
                     % stem(n))
            L.append("# Kept for reference; the reasons are in that file.")
            L.append("#")
        L.append("# ---- BRAM BUDGET IN SLR0 ------------------------------------------")
        L.append("#   %2d movers    %4d BRAM  (%d per tenant + the broadcast pair, 14 each)"
                 % (movers(n), movers(n) * MOVER_BRAM_EACH, PCS_PER_TENANT))
        L.append("#   %d x engine   %4d BRAM  (measured)" % (n, n * GEMV_BRAM))
        L.append("#   platform       ~30 BRAM in SLR0")
        total = single_die_bram(n)
        L.append("#   TOTAL         ~%4d of SLR0's %d tiles = %d%%"
                 % (total, SLR0_BRAM_TILES, round(100.0 * total / SLR0_BRAM_TILES)))
        if total > SPLIT_ABOVE * SLR0_BRAM_TILES:
            L.append("#   ^^ ABOVE 85%: expect placement pressure. Use the _split variant.")
        L.append("#   (SLR0 has 672 BRAM tiles, not 720 -- measured, not from the")
        L.append("#    datasheet's device total.)")
    L.append("# " + "-" * 74)
    L.append("")
    L.append("[connectivity]")
    L.append("")
    L.append("# the broadcast pair: beside the HBM it reads")
    for name in b:
        L.append("slr=%s:SLR0" % name)
    for k in range(n):
        L.append("")
        L.append("# tenant %d" % k)
        L.append("slr=%s:%s" % (g[k], "SLR1" if split else "SLR0"))
        for name in w[k] + ind[k] + c[k]:
            L.append("slr=%s:SLR0" % name)
    return "\n".join(L) + "\n"


def build_md(n, clock, split, why=""):
    pcs = total_pcs(n)
    mv = movers(n)
    cus = mv + n
    d = build_dir(n)
    s = stem(n)
    arch = arch_name(n)
    fp = "slr_floorplan_%s%s.cfg" % (s, "_split" if split else "")
    other_fp = "slr_floorplan_%s%s.cfg" % (s, "" if split else "_split")
    L = []
    L.append("# %d x %s multi-tenant build, BROADCAST vector" % (n, TAG))
    L.append("")
    L.append("%d engines fed ONE activation vector by ONE broadcast mover pair: **%d of 32 HBM "
             "channels**, **%d of the 32 HBM kernel ports** (a mover pair per engine would need "
             "%d), %d mover CUs + %d engines = **%d compute units**, target **%d MHz**. "
             "MoE workload only." % (n, pcs, mv, n * (PCS_PER_TENANT + A_PCS), mv, n, cus,
                                      clock))
    L.append("")
    L.append("Channels: HBM[0] and HBM[1] hold the vector, one copy, read only by")
    if LAYOUT == "writes-last":
        L.append("`mm2s_bcast_a0` / `mm2s_bcast_a1`; LAYOUT writes-last: every engine's weights +")
        L.append("indices from HBM[2] up, ALL output (write) ports on HBM[%d..%d] -- the layout of the"
                 % (total_pcs(n) - n * C_PCS, total_pcs(n) - 1))
        L.append("single 4x32 (see the generator's header for why).")
    else:
        L.append("`mm2s_bcast_a0` / `mm2s_bcast_a1`; engine k owns HBM[%d + %d*k .. %d + %d*k + %d]"
                 % (A_PCS, own_pcs(), A_PCS, own_pcs(), own_pcs() - 1))
        L.append("(weights, indices, outputs).")
    L.append("")
    L.append("Floorplan: **%s** (%s). Everything in SLR0 would take ~%d of %d BRAM (%d%%)%s."
             % (fp, "engines in SLR1, movers in SLR0 beside the HBM" if split
                else "every CU in SLR0", single_die_bram(n), SLR0_BRAM_TILES,
                round(100.0 * single_die_bram(n) / SLR0_BRAM_TILES),
                "; split, SLR0 holds ~%d (%d%%)" % (split_slr0_bram(n), round(
                    100.0 * split_slr0_bram(n) / SLR0_BRAM_TILES)) if split else ""))
    L.append("")
    L.append("**What is new, and only this:** the kernel `%s` (`Vitis/%s`: "
             "one m_axi, %d AXIS outputs) and its %d `stream_connect` lines. The engine `.xo`"
             % (bcast_kernel(n), KERNEL_FILES[n][0], n, A_PCS * n))
    L.append("(`krnl_gemv_sparse_%s.xo`), `krnl_mm2s` and `krnl_s2mm` are the ones every build "
             "used." % TAG)
    L.append("The host is `workload/host_workload_bcast.cpp`, the gate and the measurement")
    L.append("`workload/run_workload_bcast.py --arch %s` (MoE only: with one vector for every"
             % arch)
    L.append("engine, the independent-tenant test of the other builds cannot run here).")
    L.append("")
    _src, xo_sh, setup_sh = KERNEL_FILES[n]
    first_three = xo_sh == "make_bcast_xo.sh"          # the 2/6/7 builds share one kernel file
    if d in TWIN_SETUP:
        src_build, setup_script, heading = TWIN_SETUP[d]
        L.append("## 1. Set up (once, %s)" % heading)
        L.append("")
        L.append("Nothing to compile: `%s` is the `.xo` already built for `%s`, whose gates it"
                 % (bcast_kernel(n), src_build))
        L.append("passed. `%s` creates this folder with everything in it and" % setup_script)
        L.append("copies that `.xo` (hw and hw_emu) from `~/GEMV_Sparse/Vitis_multi_%s/`. It and"
                 % src_build)
        L.append("this folder's files go to `~/GEMV_Sparse/bcast_staging/`, the updated")
        L.append("`plan_workload_bcast.py` (which knows `%s`) straight to `workload_tools/`;" % arch)
        L.append("the scp line is at the top of `multi_tenant/%s`." % setup_script)
        L.append("")
        L.append("```bash")
        L.append("bash ~/GEMV_Sparse/bcast_staging/%s" % setup_script)
        L.append("```")
        L.append("")
    else:
        L.append("## 1. Set up (once%s)"
                 % (", for all three broadcast builds" if first_three else ""))
        L.append("")
        L.append("The broadcast kernel `.xo` files come first -- `%s` runs the C" % xo_sh)
        L.append("simulation and the %s `v++ -c` compiles; then `%s` creates"
                 % ("six" if first_three else "two", setup_sh))
        L.append("this folder with everything in it and installs the `*_bcast` workload tools")
        L.append("(`*_bcast` files only; nothing else is touched). Both are in")
        L.append("`~/GEMV_Sparse/bcast_staging/`; the scp line that puts them there is at the top "
                 "of")
        L.append("`multi_tenant/%s`." % setup_sh)
        L.append("")
        L.append("```bash")
        L.append("tmux new -d -s %s 'bash ~/GEMV_Sparse/bcast_staging/%s'"
                 % ("bcast_xo" if first_three else "bcast%d_xo" % n, xo_sh))
        L.append("# ~%d min; then:" % (30 if first_three else 10))
        L.append("bash ~/GEMV_Sparse/bcast_staging/%s" % setup_sh)
        L.append("```")
        L.append("")
    L.append("## 1b. Environment -- EVERY fresh shell")
    L.append("")
    L.append("```bash")
    L.append("source /opt/Xilinx/Vitis/2021.1/settings64.sh")
    L.append("source /opt/xilinx/xrt/setup.sh")
    L.append("export PLATFORM=/opt/xilinx/platforms/xilinx_u280_xdma_201920_3/xilinx_u280_xdma_201920_3.xpfm")
    L.append("export BDF=0000:af:00.1")
    L.append("echo \"PLATFORM=[$PLATFORM]\"   # must NOT be empty")
    L.append("```")
    L.append("")
    L.append("## 2. Stimulus")
    L.append("")
    L.append("Nothing to prepare: the gate generates its own (per MoE test layer ONE tall")
    L.append("matrix with ONE vector and its golden, cut into one stack per engine and proven")
    L.append("byte for byte), exactly as on every other build.")
    L.append("")
    L.append("## 3. hw_emu first")
    L.append("")
    L.append("The broadcast kernel and this topology have never been linked. hw_emu shows every")
    L.append("engine computing from the ONE broadcast vector before a bitstream is built; a")
    L.append("dangling broadcast `stream_connect` would hang it instead. One command (own tmux")
    L.append("session; the link ~30-60 min, the run up to a few hours):")
    L.append("")
    L.append("```bash")
    L.append("tmux new -d -s %s_emu 'bash ~/GEMV_Sparse/Vitis_multi_%s/run_hw_emu.sh'" % (d, d))
    L.append("tail -f ~/GEMV_Sparse/Vitis_multi_%s/hw_emu_gate.log   # to watch; Ctrl-C stops only tail"
             % d)
    L.append("```")
    L.append("")
    L.append("Nothing typed in the tmux window can kill the run (it ignores Ctrl-C and hang-up and")
    L.append("reads nothing from the terminal), but watching the log with `tail -f` is the safe habit.")
    L.append("")
    L.append("It links `sparse_%s.hw_emu.xclbin`, builds `host_workload_bcast`, and runs the" % s)
    L.append("MoE gate against it with a PRIVATE copy of the Emulation scripts (`emu_hw_emu/`),")
    L.append("so it can run beside anything else:")
    L.append("")
    L.append("```bash")
    L.append("python3 ~/GEMV_Sparse/workload_tools/run_workload_bcast.py --arch %s --correctness \\"
             % arch)
    L.append("    --emu emu_hw_emu --xclbin sparse_%s.hw_emu.xclbin --clock %d --host-timeout 14400"
             % (s, clock))
    L.append("```")
    L.append("")
    L.append("**Gate: `CORRECTNESS PASSED` -- every engine bit-exact on all 3 layers** (the last")
    L.append("lines of `hw_emu_gate.log`). hw_emu timings are meaningless; correctness only.")
    L.append("")
    L.append("## 4. Link for hardware")
    L.append("")
    L.append("After the hw_emu gate passed. One command, which checks everything first and runs")
    L.append("the link in its own tmux session (several builds can run side by side):")
    L.append("")
    L.append("```bash")
    L.append("tmux new -d -s %s 'bash ~/GEMV_Sparse/Vitis_multi_%s/start_build.sh'" % (d, d))
    L.append("tmux attach -t %s     # to watch; Ctrl-b d to leave it running" % d)
    L.append("```")
    L.append("")
    L.append("By hand instead: in `tmux`, after `df -h ~` (a full `/home` killed a link once):")
    L.append("")
    L.append("```bash")
    L.append("v++ -t hw --platform $PLATFORM \\")
    L.append("    --config sparse_hbm_%s.cfg \\" % s)
    L.append("    --config impl_family.cfg \\")
    L.append("    --config %s \\" % fp)
    L.append("    --kernel_frequency %d \\" % clock)
    L.append("    -l -o sparse_%s_%d.xclbin \\" % (s, clock))
    L.append("    ../krnl_gemv_sparse_%s.xo krnl_mm2s.hw.xo krnl_s2mm.hw.xo %s.hw.xo"
             % (TAG, bcast_kernel(n)))
    L.append("```")
    L.append("")
    L.append("Same frozen strategy as all six family builds -- do not vary it. A miss on")
    L.append("the KERNEL clock is not fatal: v++ writes the xclbin at the highest whole MHz")
    L.append("its own timing proves. **Read DATA_CLK and use THAT as the clock everywhere.**")
    L.append("If it lands far below the target, try `%s`; never change the" % other_fp)
    L.append("strategy.")
    L.append("")
    L.append("**Read the FINAL report, not `_routed`:**")
    L.append("")
    L.append("```bash")
    L.append("RS=_x/reports/link/imp/impl_1_*_timing_summary_postroute_physopted.rpt")
    L.append("awk '/Intra Clock Table/,/Inter Clock Table/' $RS | awk 'NF>5 {print $1, $2, $4}' \\")
    L.append("  | grep -E 'kernel_0|hbm_aclk_0'")
    L.append("xclbinutil --info --input sparse_%s_%d.xclbin | grep -A2 DATA_CLK" % (s, clock))
    L.append("mkdir -p ~/GEMV_Sparse/reports_multi_%s && cp -r _x/reports/link/imp ~/GEMV_Sparse/reports_multi_%s/"
             % (d, d))
    L.append("```")
    L.append("")
    L.append("## 5. On the card")
    L.append("")
    L.append("Record the DATA_CLK in `BCAST_ARCHS` of `workload/plan_workload_bcast.py` (the")
    L.append("`None` of `%s`), copy that file up to `~/GEMV_Sparse/workload_tools/`, then:" % arch)
    L.append("")
    L.append("```bash")
    L.append("bash ~/GEMV_Sparse/workload_tools/run_all_workloads_bcast.sh --gate-only %s" % arch)
    L.append("```")
    L.append("")
    L.append("The measurement (`run_all_workloads_bcast.sh %s`, no `--gate-only`) belongs in the"
             % arch)
    L.append("isolated window with the others.")
    L.append("")
    L.append("## Pass criteria")
    L.append("")
    L.append("- `xclbinutil --info` lists **%d** HBM channels bound and **%d** CUs" % (pcs, cus))
    L.append("- 30 minutes into the link, `check_30min.txt`: **0** stream/connect warnings")
    L.append("- the link reports **WNS >= 0** at %d MHz (and note what it closed at)" % clock)
    L.append("- **every engine bit-exact on all 3 MoE layers**, in hw_emu and on the card")
    return "\n".join(L) + "\n"


RUN_HW_EMU = r'''#!/bin/bash
# run_hw_emu.sh -- ONE-COMMAND hw_emu gate of @TITLE@ (step 3 of BUILD.md).
# GENERATED by multi_tenant/make_bcast_build.py.
#
#   tmux new -d -s @DIR@_emu 'bash ~/GEMV_Sparse/Vitis_multi_@DIR@/run_hw_emu.sh'
#
# Links the hw_emu bitstream (unless it exists), builds host_workload_bcast, and runs the MoE
# correctness gate against it: every engine must be bit-exact on all 3 layers, computing from
# the ONE vector the broadcast pair sends. Uses a PRIVATE copy of the Emulation scripts
# (emu_hw_emu/), so it never touches the shared Emulation directory and can run beside a
# hardware link or a card run. Everything goes to hw_emu_gate.log; the tmux window stays open.
#
# NOTHING TYPED IN THE WINDOW CAN KILL THE RUN (2026-09-29: the first x3_bcast gate died
# silently mid-simulation when its window closed, most likely from a Ctrl-C on attach -- it
# reached every process there, the log writer too). So: Ctrl-C, Ctrl-\ and hang-up are ignored
# by this script and by everything it starts (an ignored signal is inherited), the gate runs in
# a session of its own when setsid can wait for it, reads nothing from the terminal, and its
# progress reaches the log line by line (PYTHONUNBUFFERED). WATCH IT WITH
#     tail -f hw_emu_gate.log          (Ctrl-C there stops only tail)
# STOP IT, if ever needed, with
#     pkill -u $USER -f "run_workload_bcast.py --arch @ARCH@"; pkill -u $USER -f host_workload_bcast

trap '' INT QUIT HUP
cd "$(dirname "$0")" || exit 1
exec 3>&1 4>&2                         # the terminal, given back to the shell left open at the end
source /opt/Xilinx/Vitis/2021.1/settings64.sh
source /opt/xilinx/xrt/setup.sh
export PLATFORM=/opt/xilinx/platforms/xilinx_u280_xdma_201920_3/xilinx_u280_xdma_201920_3.xpfm
EMU_XCLBIN=sparse_@STEM@.hw_emu.xclbin
TOOLS=$HOME/GEMV_Sparse/workload_tools
EMU_SRC=$HOME/GEMV_Sparse/GEMV_4.0_Source/Emulation

# the window's shell: the terminal back, Ctrl-C working again
end_shell() { exec 1>&3 2>&4 3>&- 4>&-; trap - INT QUIT HUP; exec bash; }
fail() { echo "STOPPED: $*"; end_shell; }
exec > >(tee -a hw_emu_gate.log) 2>&1

[ -f "$PLATFORM" ] || fail "platform file missing: $PLATFORM"
for f in ../krnl_gemv_sparse_@TAG@.xo krnl_mm2s.hw_emu.xo krnl_s2mm.hw_emu.xo @BKERNEL@.hw_emu.xo \
         sparse_hbm_@STEM@.cfg "$TOOLS/run_workload_bcast.py" "$TOOLS/plan_workload_bcast.py" \
         "$TOOLS/host_workload_bcast.cpp" "$EMU_SRC/gemv4_cosim_gen.py"; do
    [ -f "$f" ] || fail "missing $f"
done
if pgrep -u "$USER" -f -- "-o $EMU_XCLBIN" >/dev/null; then
    fail "the hw_emu link of this build is already running (see: tmux ls)"
fi
if pgrep -u "$USER" -f -- "run_workload_bcast.py --arch @ARCH@ " >/dev/null; then
    fail "a gate of this build is already running (it survives its window closing) -- see the pkill line at the top of this script"
fi

echo "=== hw_emu gate of @TITLE@ -- started $(date)"
if [ ! -f "$EMU_XCLBIN" ]; then
    echo "--- linking $EMU_XCLBIN"
    v++ -t hw_emu --platform "$PLATFORM" --config sparse_hbm_@STEM@.cfg \
        --kernel_frequency @CLOCK@ -l -o "$EMU_XCLBIN" --temp_dir _x_hw_emu \
        ../krnl_gemv_sparse_@TAG@.xo krnl_mm2s.hw_emu.xo krnl_s2mm.hw_emu.xo @BKERNEL@.hw_emu.xo \
        || fail "the hw_emu link failed (see above)"
    echo "--- linked $(date)"
else
    echo "--- $EMU_XCLBIN exists, not relinked"
fi
emconfigutil --platform "$PLATFORM" --nd 1 >/dev/null || fail "emconfigutil failed"
cp "$TOOLS/host_workload_bcast.cpp" . || fail "cannot copy the host"
g++ -Wall -O2 -std=c++1y -I"$XILINX_XRT/include" host_workload_bcast.cpp -L"$XILINX_XRT/lib" \
    -lOpenCL -lrt -lstdc++ -pthread -DCORES=@CORES@ -DBLOCKS=@BLOCKS@ -o host_workload_bcast \
    || fail "host_workload_bcast did not compile"
rm -rf emu_hw_emu && mkdir -p emu_hw_emu && cp "$EMU_SRC"/*.py emu_hw_emu/ \
    || fail "cannot copy the Emulation scripts"
export XCL_EMULATION_MODE=hw_emu
export PYTHONUNBUFFERED=1              # the gate's progress reaches the log as it happens
DETACH=""                              # a session of its own, when setsid can wait for it
setsid -w true </dev/null >/dev/null 2>&1 && DETACH="setsid -w"
echo "--- MoE gate on the emulated card $(date)${DETACH:+ (own session)}"
$DETACH python3 "$TOOLS/run_workload_bcast.py" --arch @ARCH@ --correctness --emu emu_hw_emu \
    --xclbin "$EMU_XCLBIN" --clock @CLOCK@ --host-timeout 14400 < /dev/null
rc=$?
unset XCL_EMULATION_MODE
echo
if [ $rc -eq 0 ]; then
    echo "=== hw_emu GATE PASSED $(date) -- start the hardware link:"
    echo "    tmux new -d -s @DIR@ 'bash ~/GEMV_Sparse/Vitis_multi_@DIR@/start_build.sh'"
else
    echo "=== hw_emu GATE FAILED (exit $rc) $(date) -- do NOT start the hardware link"
fi
end_shell
'''


START_BUILD = r'''#!/bin/bash
# start_build.sh -- ONE-COMMAND hardware link of @TITLE@, target @CLOCK@ MHz.
# GENERATED by multi_tenant/make_bcast_build.py.
#
#   tmux new -d -s @DIR@ 'bash ~/GEMV_Sparse/Vitis_multi_@DIR@/start_build.sh'
#
# Checks everything first and refuses to start if anything is wrong (the reason is
# printed and the tmux window stays open: tmux attach -t @DIR@). SEVERAL builds may run
# side by side: it refuses only if THIS build is already running, or if the server is
# short of memory or disk. 30 minutes in it writes check_30min.txt: stream warnings must
# be 0 and the synthesis line must contain AlternateRoutability. At the end it prints
# the DATA_CLK the card will run at and copies the reports to
# ~/GEMV_Sparse/reports_multi_@DIR@/.

cd "$(dirname "$0")" || exit 1
source /opt/Xilinx/Vitis/2021.1/settings64.sh
source /opt/xilinx/xrt/setup.sh
export PLATFORM=/opt/xilinx/platforms/xilinx_u280_xdma_201920_3/xilinx_u280_xdma_201920_3.xpfm
export BDF=0000:af:00.1
XCLBIN=@XCLBIN@

fail() { echo "NOT STARTED: $*"; exec bash; }

[ -f "$PLATFORM" ] || fail "platform file missing: $PLATFORM"
for f in ../krnl_gemv_sparse_@TAG@.xo krnl_mm2s.hw.xo krnl_s2mm.hw.xo @BKERNEL@.hw.xo \
         impl_family.cfg sparse_hbm_@STEM@.cfg @FP@; do
    [ -f "$f" ] || fail "missing $f in $(pwd)"
done
if pgrep -u "$USER" -f -- "-o $XCLBIN" >/dev/null; then
    fail "this build ($XCLBIN) is already running (see: tmux ls)"
fi
[ -e "$XCLBIN" ] && fail "$XCLBIN already exists -- already built"
grep -q "GATE PASSED" hw_emu_gate.log 2>/dev/null \
    || echo "NOTE: no passed hw_emu gate in hw_emu_gate.log (BUILD.md step 3) -- linking anyway"
free_gb=$(df -BG --output=avail ~ | tail -1 | tr -dc 0-9)
[ "${free_gb:-0}" -ge 40 ] || fail "only ${free_gb} GB free in /home -- a full disk kills a link"
mem_gb=$(free -g | awk '/^Mem:/ {print $7}')
[ "${mem_gb:-0}" -ge 24 ] || fail "only ${mem_gb} GB of RAM available -- a link peaks near 20 GB"

( sleep 1800
  { echo "checked $(date)"
    echo "stream/connect warnings from v++ (must be 0): $(grep -ciE 'warning.*(stream|connect)' link_hw_@STEM@.log 2>/dev/null)"
    echo "critical warnings / errors from v++ (must be 0): $(grep -cE '^(CRITICAL WARNING|ERROR)' link_hw_@STEM@.log 2>/dev/null)"
    echo "synthesis of the dynamic region (must contain AlternateRoutability; empty = not started yet):"
    grep -rhoE --include='*.log' 'synth_design -top pfm_dynamic -part[^"]*' _x/link 2>/dev/null | sort -u | head -2
  } > check_30min.txt ) < /dev/null &

echo "linking @TITLE@ at @CLOCK@ MHz -- started $(date), ${free_gb} GB disk, ${mem_gb} GB RAM free"
v++ -t hw --platform "$PLATFORM" --config sparse_hbm_@STEM@.cfg --config impl_family.cfg \
    --config @FP@ --kernel_frequency @CLOCK@ -l -o "$XCLBIN" \
    ../krnl_gemv_sparse_@TAG@.xo krnl_mm2s.hw.xo krnl_s2mm.hw.xo @BKERNEL@.hw.xo 2>&1 \
    | tee link_hw_@STEM@.log
echo "v++ finished $(date)."
if [ -f "$XCLBIN" ]; then
    echo "DATA_CLK (the clock the card runs this bitstream at):"
    xclbinutil --info --input "$XCLBIN" | grep -A2 DATA_CLK
    RS=$(ls _x/reports/link/imp/impl_1_*_timing_summary_postroute_physopted.rpt 2>/dev/null | head -1)
    [ -n "$RS" ] && awk '/Intra Clock Table/,/Inter Clock Table/' "$RS" \
        | awk 'NF>5 {print $1, $2, $4}' | grep -E 'kernel_0|hbm_aclk_0'
    mkdir -p ~/GEMV_Sparse/reports_multi_@DIR@ && cp -r _x/reports/link/imp ~/GEMV_Sparse/reports_multi_@DIR@/
    echo "reports copied to ~/GEMV_Sparse/reports_multi_@DIR@/"
else
    echo "NO XCLBIN -- the link failed; the reason is near the end of link_hw_@STEM@.log"
fi
exec bash
'''


def fill(template, n, clock, split):
    s = stem(n)
    fp = "slr_floorplan_%s%s.cfg" % (s, "_split" if split else "")
    text = template
    for key, val in (("@TITLE@", "%d x %s (broadcast vector)" % (n, TAG)),
                     ("@CLOCK@", str(clock)), ("@DIR@", build_dir(n)),
                     ("@XCLBIN@", "sparse_%s_%d.xclbin" % (s, clock)), ("@TAG@", TAG),
                     ("@STEM@", s), ("@FP@", fp), ("@BKERNEL@", bcast_kernel(n)),
                     ("@ARCH@", arch_name(n)), ("@CORES@", str(CORES)),
                     ("@BLOCKS@", str(BLOCKS))):
        text = text.replace(key, val)
    return text


def main():
    ap = argparse.ArgumentParser(description="generate an N-engine broadcast-vector build")
    ap.add_argument("--tenants", type=int, required=True)
    ap.add_argument("--config", default="4x4", choices=sorted(CONFIGS),
                    help="the engine shape of ONE tenant (default 4x4)")
    ap.add_argument("--clock", type=int, default=None,
                    help="--kernel_frequency for the link (default: the clock this shape "
                         "closed at as a single tenant)")
    ap.add_argument("--floorplan", default="auto", choices=["auto", "single", "split"],
                    help="auto = split once the single-die BRAM passes %d%%"
                         % round(100 * SPLIT_ABOVE))
    ap.add_argument("--layout", default="per-engine", choices=["per-engine", "writes-last"],
                    help="per-engine (default): engine k's weights, indices, outputs together; "
                         "writes-last: every engine's inputs first, ALL outputs on the highest "
                         "channels (the 2x8x8_bcast relink, 2026-10-01)")
    a = ap.parse_args()

    global LAYOUT
    LAYOUT = a.layout
    configure(a.config)
    clock = a.clock if a.clock else DEFAULT_CLOCK
    n = a.tenants
    if n < 2:
        raise SystemExit("--tenants must be >= 2 -- one engine needs no broadcast")
    if n not in BCAST_KERNELS:
        raise SystemExit("there is a broadcast kernel for %s engines only (KERNEL_FILES) -- "
                         "krnl_mm2s_bcast%d needs a kernel file of its own first (copy "
                         "Vitis/krnl_mm2s_bcast3.cpp, add/remove the outN lines), its "
                         "testbench, its .xo script, and a KERNEL_FILES entry"
                         % ("/".join(str(x) for x in BCAST_KERNELS), n))
    if movers(n) > HBM_KERNEL_PORTS:
        raise SystemExit("%d tenants of %s need %d movers even with the broadcast pair; the HBM "
                         "subsystem has %d kernel ports, one per mover. The ceiling for %s is %d "
                         "tenants." % (n, TAG, movers(n), HBM_KERNEL_PORTS, TAG,
                                       (HBM_KERNEL_PORTS - A_PCS) // PCS_PER_TENANT))
    if total_pcs(n) > HBM_PCS:
        raise SystemExit("%d tenants need %d HBM channels; the U280 has %d."
                         % (n, total_pcs(n), HBM_PCS))

    bram_forces = single_die_bram(n) > SPLIT_ABOVE * SLR0_BRAM_TILES
    if a.floorplan == "auto":
        split, why = bram_forces, ("bram" if bram_forces else "")
    else:
        split = a.floorplan == "split"
        why = ("bram" if bram_forces else "explicit") if split else ""
    d = os.path.join(HERE, build_dir(n))
    if not os.path.isdir(d):
        os.makedirs(d)
    files = [("sparse_hbm_%s.cfg" % stem(n), link_cfg(n, clock)),
             ("slr_floorplan_%s.cfg" % stem(n), floorplan_cfg(n, False, why)),
             ("slr_floorplan_%s_split.cfg" % stem(n), floorplan_cfg(n, True, why)),
             ("BUILD.md", build_md(n, clock, split, why)),
             ("run_hw_emu.sh", fill(RUN_HW_EMU, n, clock, split)),
             ("start_build.sh", fill(START_BUILD, n, clock, split))]
    for name, text in files:
        with io.open(os.path.join(d, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print("  wrote %s" % os.path.join(build_dir(n), name))

    print("\n%d engines, broadcast vector: %d HBM channels (%d spare), %d movers of %d HBM "
          "kernel ports, + %d engines = %d CUs"
          % (n, total_pcs(n), HBM_PCS - total_pcs(n), movers(n), HBM_KERNEL_PORTS, n,
             movers(n) + n))
    print("  (a mover pair per engine would need %d movers)" % (n * (PCS_PER_TENANT + A_PCS)))
    print("  kernel    %s (Vitis/%s), 2 CUs" % (bcast_kernel(n), KERNEL_FILES[n][0]))
    print("  DSPs      %d of 9024 (%.1f%%)" % (n * 8 * CORES * BLOCKS,
                                               100.0 * n * 8 * CORES * BLOCKS / 9024))
    print("  engine LUT %s, mover LUT ~%s"
          % (format(n * GEMV_LUT, ","), format(int(round(movers(n) * MOVER_LUT_EACH)), ",")))
    print("  SLR0 BRAM if single-die: ~%d of %d (%d%%)"
          % (single_die_bram(n), SLR0_BRAM_TILES,
             round(100.0 * single_die_bram(n) / SLR0_BRAM_TILES)))
    print("  aggregate theory at %d MHz: %.1f GMAC/s (%d MACs/cycle)"
          % (clock, n * 2 * CORES * BLOCKS * clock / 1000.0, n * 2 * CORES * BLOCKS))
    print("  floorplan: %s"
          % ("SPLIT -- engines to SLR1, movers in SLR0" if split
             else "single die, every CU in SLR0"))
    if split:
        s0 = split_slr0_bram(n)
        print("  SLR0 BRAM when split: ~%d of %d (%d%%)%s"
              % (s0, SLR0_BRAM_TILES, round(100.0 * s0 / SLR0_BRAM_TILES),
                 "   <-- above %d%%: expect this build to fail" % round(100 * SPLIT_ABOVE)
                 if s0 > SPLIT_ABOVE * SLR0_BRAM_TILES else ""))
    print("  workload name: %s (workload/plan_workload_bcast.py)" % arch_name(n))
    print("\nnext: read %s/BUILD.md" % build_dir(n))


if __name__ == "__main__":
    main()

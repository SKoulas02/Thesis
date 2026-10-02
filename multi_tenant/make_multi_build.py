"""Generate the link config, floorplan and build guide for an N-TENANT 4x4 build.

    python make_multi_build.py --tenants 2            # -> multi_tenant/x2/
    python make_multi_build.py --tenants 4 --clock 375
    python make_multi_build.py --tenants 3 --config 8x4   # -> multi_tenant/8x4_x3/

THE EXPERIMENT. The professor's proposal: run several INDEPENDENT GEMV jobs on
one U280 at the same time -- multi-tenancy. Each tenant is a complete 4x4 engine
with its own matrix, its own sparsity mode, its own HBM channels and its own
movers. Nothing is shared except the card, its HBM and the kernel clock.

NO RTL CHANGES, AND NO NEW .xo. The same `krnl_gemv_sparse_4x4.xo` that closed
at 400 MHz is instantiated N times by `nk=`. Only this link config, the
floorplan, the host and the stimulus layout differ from the single-tenant build.

CHANNEL ARITHMETIC -- 6 PCs PER TENANT, AND THAT IS THE CEILING.
A 4x4 tenant needs 2 weight + 1 index + 2 activation + 1 output = 6 of the 32
HBM pseudo-channels. Tenants do NOT share activation channels here: independent
matrices mean independent vectors. So the card holds floor(32/6) = 5 tenants.

    tenants   PCs   mover CUs   engines   total CUs
        2      12       12          2        14
        3      18       18          3        21
        4      24       24          4        28
        5      30       30          5        35

For reference the largest single-engine build so far (4x32) used 32 PCs and 33
CUs and closed at 250 MHz; 8x8 used 17 PCs and 18 CUs at 325. The whole point of
this experiment is which of those two numbers -- channels or engine size -- set
the clock.

CONTIGUOUS, ASCENDING CHANNELS PER TENANT. Tenant k gets HBM[6k .. 6k+5]. The
U280's HBM AXI crossbar is built from small switches with lateral links between
them, so a master reaching a distant pseudo-channel pays for it; keeping each
tenant's six masters adjacent keeps that traffic local. NOTE that a tenant whose
block straddles PC 16 spans the two HBM STACKS (tenant 2 at 12..17 is the first).
Whether that costs anything measurable is one of the things to look for.

THE CU NAMING CONTRACT. Tenant k's units are suffixed `_tk`:

    mm2s_w0_tk mm2s_w1_tk  mm2s_i0_tk  mm2s_a0_tk mm2s_a1_tk  gemv_tk  s2mm_c0_tk

`host_sparse_multi.cpp` builds exactly these names from the tenant index. Rename
here and the host fails to find its CU -- a clean error, never a wrong answer.

--shared-vector (added 2026-09-28, the professor's MoE case). The activation vector
lives ONCE in HBM[0] (elements 0-15 of each 32-element window) and HBM[1] (16-31), and
EVERY tenant's two activation movers read those two channels. The tenants follow,
packed: tenant k owns HBM[2 + k*m .. 2 + k*m + m-1], m = its weight + index + output
channels, in that order. So a build needs n*m + 2 channels instead of n*(m + 2):
    3 x 4x4 14 (not 18)   6 x 4x4 26   7 x 4x4 30   3 x 8x4 26 (not 30)
    2 x 8x8 32            2 x 16x3 24 (not 26)
Nothing else changes: the same .xo files, the same CU names, the same movers (each
tenant keeps its own two activation movers -- so the mover BRAM does NOT shrink), the
same floorplan rule. The host decides whether the tenants read ONE buffer in those
channels (MoE) or each its own copy there (any existing host: a buffer takes the HBM
bank of the argument it is first bound to, so it lands in HBM[0]/[1] by itself).
Folders and files carry a _shared suffix: x3_shared/, sparse_hbm_4x4_x3_shared.cfg.
"""

import argparse
import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- the tenant's engine shape. configure() sets these; 4x4 is the default ----
TAG = "4x4"
CORES, BLOCKS = 4, 4
W_PCS, IND_PCS, A_PCS, C_PCS = 2, 1, 2, 1
PCS_PER_TENANT = W_PCS + IND_PCS + A_PCS + C_PCS      # 6
HBM_PCS = 32
DEFAULT_CLOCK = 400                                   # what 4x4 closed at, alone

# Measured on the single-tenant 4x4 build (reports_4x4_400_slr0, kernel util):
GEMV_LUT, GEMV_BRAM = 17977, 31
MOVER_LUT, MOVER_BRAM = 9686, 84                      # 5 x mm2s + 1 x s2mm, per tenant
SLR0_BRAM_TILES = 672

# ANY FAMILY SHAPE CAN BE THE TENANT. cores, blocks, engine LUT and BRAM (from the
# routed kernel-utilisation report of that family build) and the clock it closed at
# as a SINGLE tenant. [[gemv-family-bitstream-campaign]]
CONFIGS = {
    "4x4":  (4, 4, 17977, 31, 400),
    "8x4":  (8, 4, 35438, 45, 375),
    "16x3": (16, 3, 54733, 60, 350),
    "8x8":  (8, 8, 66176, 74, 325),
    "4x24": (4, 24, 94823, 103, 300),
    "4x32": (4, 32, 125614, 133, 250),
}
# The movers are the SAME two HLS kernels at every shape, so they cost per CU, not
# per shape: 4x4's measured 9,686 LUT / 84 BRAM over its 6 movers. The x5 split
# measured 432 BRAM for 30 movers (14.4 each), so the BRAM figure runs ~3% low.
MOVER_LUT_EACH, MOVER_BRAM_EACH = 1614.33, 14
A_PCS_ALWAYS = 2                          # one 32-element window = 512 bits, every shape
SPLIT_ABOVE = 0.85                        # single-die BRAM share that forces the split
SHARED = False                            # --shared-vector: one vector in HBM[0..1] for all
# The U280 HBM memory subsystem (hmss_0) has 33 connections; the platform takes one, so at
# most 32 kernel AXI masters -- one per mover, since every mover has an m_axi port. v++ does
# NOT share a connection between movers that read the same channel: 6 x 4x4 shared (36
# movers) failed in create_bd with "You have run out of port connections on /hmss_0. All 33
# connections are used" (2026-09-28). The 4x32 single engine used exactly 32.
HBM_KERNEL_PORTS = 32


def ceildiv(a, b):
    return -(-a // b)


def configure(tag):
    """Set every width for one engine shape, exactly as the family generator derives it.

    T = cores x blocks blocks, each with 2 weights (16 bit), one 10-bit index pair and
    one 16-bit result; the 2 sparsity bits ride above the indices; 2 activation PCs at
    every shape. ONE MOVER PER PSEUDO-CHANNEL, which is why the mover cost scales with
    the channel count and not with the engine.
    """
    global TAG, CORES, BLOCKS, W_PCS, IND_PCS, A_PCS, C_PCS, PCS_PER_TENANT
    global GEMV_LUT, GEMV_BRAM, MOVER_LUT, MOVER_BRAM, DEFAULT_CLOCK
    TAG = tag
    CORES, BLOCKS, GEMV_LUT, GEMV_BRAM, DEFAULT_CLOCK = CONFIGS[tag]
    T = CORES * BLOCKS
    W_PCS = ceildiv(32 * T, 256)
    IND_PCS = ceildiv(10 * T + 2, 256)
    A_PCS = A_PCS_ALWAYS
    C_PCS = ceildiv(16 * T, 256)
    PCS_PER_TENANT = W_PCS + IND_PCS + A_PCS + C_PCS
    MOVER_LUT = int(round(PCS_PER_TENANT * MOVER_LUT_EACH))
    MOVER_BRAM = PCS_PER_TENANT * MOVER_BRAM_EACH


def single_die_bram(n):
    """SLR0 BRAM if everything goes in one die: movers + engines + ~30 for the platform."""
    return n * MOVER_BRAM + n * GEMV_BRAM + 30


def split_slr0_bram(n):
    """SLR0 BRAM with the engines in SLR1: the movers + ~30 for the platform."""
    return n * MOVER_BRAM + 30


def own_pcs():
    """HBM channels one tenant owns: weights, indices, outputs, and its vector unless shared."""
    return W_PCS + IND_PCS + C_PCS + (0 if SHARED else A_PCS)


def total_pcs(n):
    return n * own_pcs() + (A_PCS if SHARED else 0)


def tenant_base(k):
    """First HBM channel of tenant k (after the shared vector, if there is one)."""
    return (A_PCS if SHARED else 0) + k * own_pcs()


def stem(n):
    """<shape>_x<n>[_shared] -- the part every file name of this build carries."""
    return "%s_x%d%s" % (TAG, n, "_shared" if SHARED else "")


def build_dir(n):
    """x2/x4/x5 keep their names (4x4 is the original study); other shapes are tagged."""
    return ("x%d" % n if TAG == "4x4" else "%s_x%d" % (TAG, n)) + ("_shared" if SHARED else "")


def cu_names(n):
    """Every CU name, grouped, in the order the link config declares them."""
    w = [["mm2s_w%d_t%d" % (i, k) for i in range(W_PCS)] for k in range(n)]
    ind = [["mm2s_i%d_t%d" % (i, k) for i in range(IND_PCS)] for k in range(n)]
    a = [["mm2s_a%d_t%d" % (i, k) for i in range(A_PCS)] for k in range(n)]
    g = ["gemv_t%d" % k for k in range(n)]
    c = [["s2mm_c%d_t%d" % (i, k) for i in range(C_PCS)] for k in range(n)]
    return w, ind, a, g, c


def link_cfg(n, clock):
    w, ind, a, g, c = cu_names(n)
    movers = []
    for k in range(n):
        movers += w[k] + ind[k] + a[k]
    L = []
    L.append("# " + "-" * 74)
    if SHARED:
        L.append("# sparse_hbm_%s.cfg -- %d %s tenants on one U280 that SHARE ONE"
                 % (stem(n), n, TAG))
        L.append("# ACTIVATION VECTOR in HBM[0..1].")
    else:
        L.append("# sparse_hbm_%s.cfg -- %d INDEPENDENT %s tenants on one U280."
                 % (stem(n), n, TAG))
    L.append("#")
    L.append("# GENERATED by multi_tenant/make_multi_build.py. Tenant k = one complete")
    L.append("# %s engine (%d cores x %d blocks, %d DSPs) with its own matrix, its own"
             % (TAG, CORES, BLOCKS, 8 * CORES * BLOCKS))
    if SHARED:
        L.append("# sparsity mode and its own %d HBM pseudo-channels HBM[2+%d*k .. 2+%d*k+%d]"
                 % (own_pcs(), own_pcs(), own_pcs(), own_pcs() - 1))
        L.append("# (weights, indices, outputs). The vector is in HBM[0] and HBM[1] for all.")
    else:
        L.append("# sparsity mode and its own %d HBM pseudo-channels HBM[%d*k .. %d*k+%d]."
                 % (PCS_PER_TENANT, PCS_PER_TENANT, PCS_PER_TENANT, PCS_PER_TENANT - 1))
    L.append("#")
    L.append("# %d tenants  ->  %d of 32 HBM channels, %d mover CUs + %d engines = %d CUs."
             % (n, total_pcs(n), n * PCS_PER_TENANT, n, n * PCS_PER_TENANT + n))
    if SHARED:
        L.append("# (%d channels with a vector per tenant; sharing frees %d.)"
                 % (n * PCS_PER_TENANT, n * PCS_PER_TENANT - total_pcs(n)))
    L.append("#")
    L.append("# THE ENGINE .xo IS UNCHANGED. This is the same krnl_gemv_sparse_%s.xo that"
             % TAG)
    L.append("# closed at %d MHz as a single tenant, instantiated %d times." % (DEFAULT_CLOCK, n))
    L.append("#")
    if SHARED:
        L.append("# SHARED: ONLY THE TWO VECTOR CHANNELS. Every tenant keeps its own two")
        L.append("# activation movers; all of them read HBM[0] (elements 0-15 of each")
        L.append("# window) and HBM[1] (elements 16-31). Whether the tenants read ONE buffer")
        L.append("# there (MoE: one token, many experts) or each its own copy is the host's")
        L.append("# choice. No stream crosses from one tenant to another.")
    else:
        L.append("# NOTHING IS SHARED BETWEEN TENANTS except the card, HBM and the kernel")
        L.append("# clock. No stream crosses from one tenant to another; a cross-wired")
        L.append("# stream_connect would show up immediately because every tenant runs a")
        L.append("# DIFFERENT matrix and is compared against its own golden.")
    L.append("#")
    L.append("# Used as:  v++ -l --config sparse_hbm_%s.cfg --config impl_family.cfg \\"
             % stem(n))
    L.append("#                  --config slr_floorplan_%s.cfg --kernel_frequency %d ..."
             % (stem(n), clock))
    L.append("# " + "-" * 74)
    L.append("")
    L.append("[connectivity]")
    L.append("")
    L.append("# ---- compute units ----------------------------------------------------")
    L.append("# Names are explicit. Positional naming across %d movers would make one"
             % len(movers))
    L.append("# transposed digit bind a tenant's index channel to another's weight")
    L.append("# stream -- wrong answers on both, no error.")
    L.append("nk=krnl_mm2s:%d:%s" % (len(movers), ".".join(movers)))
    L.append("nk=krnl_gemv_sparse:%d:%s" % (n, ".".join(g)))
    flat_c = [name for k in range(n) for name in c[k]]
    L.append("nk=krnl_s2mm:%d:%s" % (len(flat_c), ".".join(flat_c)))
    L.append("")
    if SHARED:
        L.append("# ---- memory binding: THE SHARED VECTOR, HBM[0] and HBM[1] ---------------")
        L.append("# Every tenant's activation movers. Several masters on one channel is new")
        L.append("# in this project: v++ puts an interconnect in front of the channel when")
        L.append("# there are more masters than HBM ports. The vector is read once per")
        L.append("# calculation (at most 256 beats per channel), so the bandwidth is trivial.")
        for k in range(n):
            for i, name in enumerate(a[k]):
                L.append("sp=%s.in:HBM[%d]" % (name, i))
        L.append("")
        L.append("# ---- memory binding: tenant k owns HBM[2+%d*k .. 2+%d*k+%d] -------------"
                 % (own_pcs(), own_pcs(), own_pcs() - 1))
    else:
        L.append("# ---- memory binding: tenant k owns HBM[%d*k .. %d*k+%d] ----------------"
                 % (PCS_PER_TENANT, PCS_PER_TENANT, PCS_PER_TENANT - 1))
    for k in range(n):
        base = tenant_base(k)
        last = base + own_pcs() - 1
        L.append("")
        L.append("# tenant %d -> HBM[%d..%d]%s"
                 % (k, base, last,
                    "   (straddles the two HBM stacks at PC 16)"
                    if base < 16 <= last else ""))
        pc = base
        for name in w[k] + ind[k] + ([] if SHARED else a[k]):
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
        for i, name in enumerate(a[k]):
            L.append("stream_connect=%s.out:gemv_t%d.s_axis_a%d" % (name, k, i))
        for i, name in enumerate(c[k]):
            L.append("stream_connect=gemv_t%d.m_axis_c%d:%s.in" % (k, i, name))
    L.append("")
    L.append("# ---- kernel clock -----------------------------------------------------")
    L.append("# NOT SET HERE -- a [clock] section maps to v++ --clock, which this")
    L.append("# platform rejects. Use  v++ -l ... --kernel_frequency %d" % clock)
    L.append("# ONE clock for every tenant: tenants share the card, so they share the")
    L.append("# frequency. A tenant cannot be clocked independently of its neighbours.")
    return "\n".join(L) + "\n"


def explicit_split_lines(n):
    """Why the split was chosen by hand when the BRAM rule would allow one die."""
    pct = round(100.0 * single_die_bram(n) / SLR0_BRAM_TILES)
    return [
        "Chosen explicitly (--floorplan split). The BRAM rule would allow one die",
        "(~%d%% of SLR0), but one die would also hold %d engines of %s LUT beside"
        % (pct, n, format(GEMV_LUT, ",")),
        "the HBM together with %d movers -- the most logic ever placed in that die."
        % (n * PCS_PER_TENANT),
        "On one die, 16x3 alone only just closed 350 MHz (+0.008 ns) and 8x8 alone",
        "failed 325 on congestion; every split build has closed (3 x 8x4 at 354 MHz).",
    ]


def floorplan_cfg(n, split, why=""):
    """All CUs in SLR0 (default) or engines in SLR1 with the movers in SLR0.

    why = "" (single die is the choice), "bram" (the BRAM rule forced the split) or
    "explicit" (split chosen by hand) -- it decides what each file says about itself.
    """
    w, ind, a, g, c = cu_names(n)
    mover_bram = n * MOVER_BRAM
    eng_bram = n * GEMV_BRAM
    L = []
    L.append("# " + "-" * 74)
    if split:
        total = single_die_bram(n)
        pct = 100.0 * total / SLR0_BRAM_TILES
        L.append("# slr_floorplan_%s_split.cfg -- the engines get SLR1." % stem(n))
        L.append("#")
        if why == "explicit":
            L.append("# USE THIS ONE.")
            for ln in explicit_split_lines(n):
                L.append("# " + ln)
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
            L.append("# in SLR0) first and switch only if the build misses its frequency, which")
            L.append("# is the rule the family study followed: single die until a build fails.")
        L.append("#")
        L.append("# The %d engines are %s LUT and %d BRAM together -- they fit one die"
                 % (n, format(n * GEMV_LUT, ","), eng_bram))
        L.append("# easily. The movers stay in SLR0 beside the HBM they master.")
        if SHARED:
            s0 = split_slr0_bram(n)
            L.append("#")
            L.append("# SLR0 then holds the %d movers and the platform: ~%d of %d BRAM (%d%%)."
                     % (n * PCS_PER_TENANT, s0, SLR0_BRAM_TILES,
                        round(100.0 * s0 / SLR0_BRAM_TILES)))
            L.append("# Sharing the vector does not remove movers (each tenant keeps its two")
            L.append("# activation movers), so it does not lower this number.")
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
        L.append("# THE DEFAULT, for the same reason it is the default at 4x4: a single")
        L.append("# tenant measured WORSE when split (WNS -0.048 with the engine in SLR1,")
        L.append("# +0.022 with everything in SLR0 -- the SLR crossing cost 0.222 ns on a")
        L.append("# ONE-LUT path). Keep every tenant whole, beside its own movers.")
        L.append("#")
        L.append("# ---- BRAM BUDGET IN SLR0 ------------------------------------------")
        L.append("#   %d x movers   %4d BRAM  (5 mm2s + 1 s2mm per tenant, measured)"
                 % (n, mover_bram))
        L.append("#   %d x engine   %4d BRAM  (measured)" % (n, eng_bram))
        L.append("#   platform       ~30 BRAM in SLR0")
        total = mover_bram + eng_bram + 30
        L.append("#   TOTAL         ~%4d of SLR0's %d tiles = %d%%"
                 % (total, SLR0_BRAM_TILES, round(100.0 * total / SLR0_BRAM_TILES)))
        if total > 0.85 * SLR0_BRAM_TILES:
            L.append("#   ^^ ABOVE 85%: expect placement pressure. Try the _split variant.")
        L.append("#   (SLR0 has 672 BRAM tiles, not 720 -- measured, not from the")
        L.append("#    datasheet's device total.)")
    L.append("# " + "-" * 74)
    L.append("")
    L.append("[connectivity]")
    for k in range(n):
        L.append("")
        L.append("# tenant %d" % k)
        L.append("slr=%s:%s" % (g[k], "SLR1" if split else "SLR0"))
        for name in w[k] + ind[k] + a[k] + c[k]:
            L.append("slr=%s:SLR0" % name)
    return "\n".join(L) + "\n"


def build_md(n, clock, split, why=""):
    pcs = total_pcs(n)
    movers = n * PCS_PER_TENANT
    cus = movers + n
    d = build_dir(n)
    s = stem(n)
    fp = "slr_floorplan_%s%s.cfg" % (s, "_split" if split else "")
    other_fp = "slr_floorplan_%s%s.cfg" % (s, "" if split else "_split")
    shape = "" if TAG == "4x4" else " --cores %d --blocks %d" % (CORES, BLOCKS)
    dflags = "" if TAG == "4x4" else " -DCORES=%d -DBLOCKS=%d" % (CORES, BLOCKS)
    L = []
    L.append("# %d x %s multi-tenant build%s" % (n, TAG, ", shared vector" if SHARED else ""))
    L.append("")
    if SHARED:
        L.append("%d tenants that share ONE activation vector in HBM[0..1], **%d of 32 HBM "
                 "channels** (%d with a vector per tenant), %d mover CUs + %d engines = "
                 "**%d compute units**, target **%d MHz**."
                 % (n, pcs, movers, movers, n, cus, clock))
        L.append("")
        L.append("Channels: HBM[0] and HBM[1] hold the vector for every tenant; tenant k owns")
        L.append("HBM[%d + %d*k .. %d + %d*k + %d] (weights, indices, outputs)."
                 % (A_PCS, own_pcs(), A_PCS, own_pcs(), own_pcs() - 1))
    else:
        L.append("%d independent tenants, %d of 32 HBM channels, %d mover CUs + %d engines "
                 "= **%d compute units**, target **%d MHz**."
                 % (n, pcs, movers, n, cus, clock))
    L.append("")
    L.append("Floorplan: **%s** (%s). Everything in SLR0 would take ~%d of %d BRAM (%d%%)."
             % (fp, "engines in SLR1, movers in SLR0 beside the HBM" if split
                else "every CU in SLR0", single_die_bram(n), SLR0_BRAM_TILES,
                round(100.0 * single_die_bram(n) / SLR0_BRAM_TILES)))
    if split and why == "explicit":
        L.append("")
        L.extend(explicit_split_lines(n))
    elif split:
        L.append("")
        L.append("The single-die variant is generated too, but do not start with it: at 92%")
        L.append("of SLR0's BRAM the 5 x 4x4 build fell to 260 MHz and one tenant hung on the")
        L.append("card, while the split closed 329 MHz with every tenant bit-exact.")
    if split and SHARED:
        s0 = split_slr0_bram(n)
        L.append("")
        L.append("Split, SLR0 still holds all %d movers: ~%d of %d BRAM (%d%%)%s"
                 % (movers, s0, SLR0_BRAM_TILES, round(100.0 * s0 / SLR0_BRAM_TILES),
                    " -- ABOVE the ~85% at which 5 x 4x4 on one die failed. Expect this build "
                    "to fail; if it does, it is dropped." if s0 > SPLIT_ABOVE * SLR0_BRAM_TILES
                    else "."))
    L.append("")
    if TAG == "8x8":
        L.append("The engine `.xo` is the family-generated `krnl_gemv_sparse_8x8.xo`, the one")
        L.append("linked in `Vitis_8x8` (hw_emu, and the single-die link that missed 325). The")
        L.append("MEASURED single 8x8 bitstream (`Vitis_325`) came from an earlier packaging of")
        L.append("the same RTL, `krnl_gemv_sparse_fix.xo`. Nothing in the RTL changes.")
    else:
        L.append("The engine `.xo` is the one the single-tenant %s build already used and" % TAG)
        L.append("verified. Nothing in the RTL changes, so nothing in the RTL needs re-verifying.")
    if SHARED:
        L.append("")
        L.append("**What is new here is only the channel binding:** several activation movers")
        L.append("(%d) read the same two channels. With more movers than the HBM's 32 ports, v++"
                 % (2 * n))
        L.append("puts interconnects in front of the shared channels; that is the part to watch")
        L.append("in timing. Every existing host works unchanged: a buffer takes the HBM bank of")
        L.append("the argument it is first bound to, so each tenant's vector lands in HBM[0..1].")
    L.append("")
    L.append("## 1. Copy to the server")
    L.append("")
    L.append("Create the build directory, with the mover `.xo` files and `impl_family.cfg`")
    L.append("beside it, exactly like every other `Vitis_<tag>` build directory:")
    L.append("")
    L.append("```bash")
    L.append("mkdir -p ~/GEMV_Sparse/Vitis_multi_%s && cd ~/GEMV_Sparse/Vitis_multi_%s" % (d, d))
    L.append("cp ~/GEMV_Sparse/Vitis_4x4/krnl_mm2s.hw.xo ~/GEMV_Sparse/Vitis_4x4/krnl_s2mm.hw.xo .")
    L.append("cp ~/GEMV_Sparse/Vitis_4x4/krnl_mm2s.hw_emu.xo ~/GEMV_Sparse/Vitis_4x4/krnl_s2mm.hw_emu.xo .")
    L.append("# impl_family.cfg lives in each Vitis_<tag> build dir on the server,")
    L.append("# NOT in ~/GEMV_Sparse/Vitis. Copy it from a family build, or scp the")
    L.append("# repo copy (Vitis/impl_family.cfg) up -- they are the same file.")
    L.append("cp ~/GEMV_Sparse/Vitis_4x4/impl_family.cfg .   # or: find ~/GEMV_Sparse -name impl_family.cfg")
    L.append("ls ../krnl_gemv_sparse_%s.xo   # the engine; must exist" % TAG)
    L.append("```")
    L.append("")
    L.append("Then, from the repo root on the local machine:")
    L.append("")
    L.append("```bash")
    L.append("scp multi_tenant/%s/*.cfg multi_tenant/host_sparse_multi.cpp \\" % d)
    L.append("    multi_tenant/prep_tenants.py multi_tenant/run_multi_measure.py \\")
    L.append("    skoulas@coroni.microlab.ntua.gr:/home/skoulas/GEMV_Sparse/Vitis_multi_%s/" % d)
    L.append("```")
    L.append("")
    L.append("## 1b. Environment -- EVERY fresh shell")
    L.append("")
    L.append("None of this is in `.bashrc`, and `setup_vitis.sh` must be SOURCED, not")
    L.append("executed -- running it in a child shell loses the exports and the next")
    L.append("command fails with a confusing error (an empty `$PLATFORM` makes")
    L.append("`emconfigutil` report that a platform named `--nd` was not found).")
    L.append("")
    L.append("```bash")
    L.append("source /opt/Xilinx/Vitis/2021.1/settings64.sh")
    L.append("source /opt/xilinx/xrt/setup.sh")
    L.append("export PLATFORM=/opt/xilinx/platforms/xilinx_u280_xdma_201920_3/xilinx_u280_xdma_201920_3.xpfm")
    L.append("export BDF=0000:af:00.1")
    L.append("echo \"PLATFORM=[$PLATFORM]\"   # must NOT be empty")
    L.append("```")
    L.append("")
    L.append("Use `xilinx_u280_xdma_201920_3`. The `gen3x16_xdma_1_202211_1` entry in the")
    L.append("same directory is a 2022.2 platform that Vitis 2021.1 cannot parse, and every")
    L.append("build in this project used 201920_3.")
    L.append("")
    L.append("## 2. Stimulus: one directory per tenant")
    L.append("")
    L.append("```bash")
    L.append("cd ~/GEMV_Sparse/Vitis_multi_%s" % d)
    L.append("python3 prep_tenants.py --emu ~/GEMV_Sparse/GEMV_4.0_Source/Emulation \\")
    L.append("                        --out . --tenants %d%s" % (n, shape))
    L.append("```")
    L.append("")
    L.append("Writes `t0/ .. t%d/`, each with `bin/`, its own `golden.txt` and its own copy"
             % (n - 1))
    L.append("of the compare script. **Every tenant gets a different matrix** -- different")
    L.append("shape, seed and sparsity mode -- which is what makes a cross-wired")
    L.append("`stream_connect` fail the compare instead of passing silently.")
    if SHARED:
        L.append("Each tenant also has its OWN vector, placed in the shared HBM[0..1]: a tenant")
        L.append("that read another tenant's vector from the shared channels would fail its")
        L.append("compare. (One buffer read by every tenant -- MoE -- is the workload host's job.)")
    L.append("")
    L.append("## 3. hw_emu%s" % (" (optional, x3_shared only)" if SHARED else " first"))
    L.append("")
    if SHARED:
        L.append("Several movers on the same HBM channels have never been linked in this")
        L.append("project. A wrong binding makes the hardware link stop within its first")
        L.append("minutes (connectivity is checked before implementation), so the hardware")
        L.append("builds do not wait for this. If wanted, run hw_emu on 3 x 4x4 shared, the")
        L.append("smallest, IN PARALLEL with the hardware links: it shows every tenant reading")
        L.append("its own vector from HBM[0..1] before any bitstream is finished.")
    else:
        L.append("This channel topology has never been linked. hw_emu catches a dangling")
        L.append("`stream_connect` in under an hour; in `-t hw` the same mistake costs a whole")
        L.append("build and its only symptom is one tenant hanging.")
    L.append("")
    L.append("```bash")
    L.append("emconfigutil --platform $PLATFORM --nd 1")
    L.append("export XCL_EMULATION_MODE=hw_emu")
    L.append("v++ -t hw_emu --platform $PLATFORM --config sparse_hbm_%s.cfg \\" % s)
    L.append("    --kernel_frequency %d -l -o sparse_%s.hw_emu.xclbin \\" % (clock, s))
    L.append("    ../krnl_gemv_sparse_%s.xo krnl_mm2s.hw_emu.xo krnl_s2mm.hw_emu.xo" % TAG)
    L.append("```")
    L.append("")
    L.append("Then build the host and run it against the emulated bitstream:")
    L.append("")
    L.append("```bash")
    L.append("g++ -Wall -O2 -std=c++1y -I$XILINX_XRT/include host_sparse_multi.cpp \\")
    L.append("    -L$XILINX_XRT/lib -lOpenCL -lrt -lstdc++ -pthread%s -o host_sparse_multi"
             % dflags)
    L.append("./host_sparse_multi sparse_%s.hw_emu.xclbin %d 0 %s"
             % (s, clock, " ".join("%d:t%d" % (k, k) for k in range(n))))
    L.append("for k in $(seq 0 %d); do (cd t$k && python3 compare_gemv4_py36.py); done" % (n - 1))
    L.append("unset XCL_EMULATION_MODE")
    L.append("```")
    L.append("")
    L.append("**Gate: every tenant bit-exact against its own golden.** hw_emu timings are")
    L.append("meaningless (simulation-time timestamps); correctness only.")
    if SHARED:
        L.append("(The host's printed HBM ranges assume a vector per tenant -- ignore them.)")
    L.append("")
    L.append("## 4. Link for hardware")
    L.append("")
    L.append("One command, which checks everything first and runs the link below in its own")
    L.append("tmux session (several builds can run side by side):")
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
    L.append("    ../krnl_gemv_sparse_%s.xo krnl_mm2s.hw.xo krnl_s2mm.hw.xo" % TAG)
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
    L.append("```bash")
    L.append("# all tenants together (use the DATA_CLK, not the target, as the clock)")
    L.append("./host_sparse_multi sparse_%s_%d.xclbin <DATA_CLK> 0 %s"
             % (s, clock, " ".join("%d:t%d" % (k, k) for k in range(n))))
    L.append("for k in $(seq 0 %d); do (cd t$k && python3 compare_gemv4_py36.py); done" % (n - 1))
    L.append("```")
    L.append("")
    L.append("## Pass criteria")
    L.append("")
    L.append("- `xclbinutil --info` lists **%d** HBM channels bound and **%d** CUs" % (pcs, cus))
    L.append("- the link reports **WNS >= 0** at %d MHz (and note what it closed at)" % clock)
    L.append("- **every tenant bit-exact against its own golden**, in hw_emu%s and on the card"
             % (" (x3_shared)" if SHARED else ""))
    return "\n".join(L) + "\n"


START_BUILD = r'''#!/bin/bash
# start_build.sh -- ONE-COMMAND hardware link of @TITLE@, target @CLOCK@ MHz.
# GENERATED by multi_tenant/make_multi_build.py.
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
for f in ../krnl_gemv_sparse_@TAG@.xo krnl_mm2s.hw.xo krnl_s2mm.hw.xo impl_family.cfg \
         sparse_hbm_@STEM@.cfg @FP@; do
    [ -f "$f" ] || fail "missing $f in $(pwd)"
done
if pgrep -u "$USER" -f -- "-o $XCLBIN" >/dev/null; then
    fail "this build ($XCLBIN) is already running (see: tmux ls)"
fi
[ -e "$XCLBIN" ] && fail "$XCLBIN already exists -- already built"
free_gb=$(df -BG --output=avail ~ | tail -1 | tr -dc 0-9)
[ "${free_gb:-0}" -ge 40 ] || fail "only ${free_gb} GB free in /home -- a full disk kills a link"
mem_gb=$(free -g | awk '/^Mem:/ {print $7}')
[ "${mem_gb:-0}" -ge 24 ] || fail "only ${mem_gb} GB of RAM available -- a link peaks near 20 GB"

( sleep 1800
  { echo "checked $(date)"
    echo "stream/connect warnings (must be 0): $(grep -irchE 'warning.*(stream|connect|unconnect)' _x/logs 2>/dev/null | paste -sd+ | bc)"
    echo "synthesis strategy (must contain AlternateRoutability; empty = not started yet):"
    grep -oE 'synth_design [^"]*' _x/logs/link/vivado.log 2>/dev/null | head -2
  } > check_30min.txt ) &

echo "linking @TITLE@ at @CLOCK@ MHz -- started $(date), ${free_gb} GB disk, ${mem_gb} GB RAM free"
v++ -t hw --platform "$PLATFORM" --config sparse_hbm_@STEM@.cfg --config impl_family.cfg \
    --config @FP@ --kernel_frequency @CLOCK@ -l -o "$XCLBIN" \
    ../krnl_gemv_sparse_@TAG@.xo krnl_mm2s.hw.xo krnl_s2mm.hw.xo 2>&1 | tee link_hw_@STEM@.log
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


def start_build_sh(n, clock, split):
    s = stem(n)
    title = "%d x %s%s" % (n, TAG, " (shared vector)" if SHARED else "")
    fp = "slr_floorplan_%s%s.cfg" % (s, "_split" if split else "")
    text = START_BUILD
    for key, val in (("@TITLE@", title), ("@CLOCK@", str(clock)), ("@DIR@", build_dir(n)),
                     ("@XCLBIN@", "sparse_%s_%d.xclbin" % (s, clock)), ("@TAG@", TAG),
                     ("@STEM@", s), ("@FP@", fp)):
        text = text.replace(key, val)
    return text


def main():
    ap = argparse.ArgumentParser(description="generate an N-tenant multi-instance build")
    ap.add_argument("--tenants", type=int, required=True)
    ap.add_argument("--config", default="4x4", choices=sorted(CONFIGS),
                    help="the engine shape of ONE tenant (default 4x4)")
    ap.add_argument("--clock", type=int, default=None,
                    help="--kernel_frequency for the link (default: the clock this shape "
                         "closed at as a single tenant)")
    ap.add_argument("--floorplan", default="auto", choices=["auto", "single", "split"],
                    help="auto = split once the single-die BRAM passes %d%%"
                         % round(100 * SPLIT_ABOVE))
    ap.add_argument("--shared-vector", action="store_true",
                    help="every tenant's activation movers read ONE vector in HBM[0..1] "
                         "(the MoE layout); folder and files get a _shared suffix")
    a = ap.parse_args()

    global SHARED
    SHARED = a.shared_vector
    configure(a.config)
    clock = a.clock if a.clock else DEFAULT_CLOCK
    n = a.tenants
    if n < 1:
        raise SystemExit("--tenants must be >= 1")
    need = total_pcs(n)
    if n * PCS_PER_TENANT > HBM_KERNEL_PORTS:
        raise SystemExit("%d tenants of %s need %d movers; the HBM subsystem has %d kernel ports "
                         "(33 minus the platform's), one per mover, and v++ does not share them "
                         "-- even with a shared vector, every tenant keeps its own activation "
                         "movers. The ceiling for %s is %d tenants."
                         % (n, TAG, n * PCS_PER_TENANT, HBM_KERNEL_PORTS, TAG,
                            HBM_KERNEL_PORTS // PCS_PER_TENANT))
    if need > HBM_PCS:
        raise SystemExit("%d tenants need %d HBM channels; the U280 has %d. "
                         "The ceiling for %s%s is %d tenants."
                         % (n, need, HBM_PCS, TAG, " with a shared vector" if SHARED else "",
                            (HBM_PCS - (A_PCS if SHARED else 0)) // own_pcs()))

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
             ("start_build.sh", start_build_sh(n, clock, split))]
    for name, text in files:
        with io.open(os.path.join(d, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print("  wrote %s" % os.path.join(build_dir(n), name))

    mover_bram = n * MOVER_BRAM + n * GEMV_BRAM + 30
    movers = n * PCS_PER_TENANT
    print("\n%d tenants%s: %d HBM channels (%d spare), %d mover CUs + %d engines = %d CUs"
          % (n, " sharing one vector" if SHARED else "", need, HBM_PCS - need, movers, n,
             movers + n))
    if SHARED:
        print("  sharing frees %d channels (%d with a vector per tenant)"
              % (movers - need, movers))
    print("  DSPs      %d of 9024 (%.1f%%)" % (n * 8 * CORES * BLOCKS,
                                               100.0 * n * 8 * CORES * BLOCKS / 9024))
    print("  engine LUT %s, mover LUT %s (measured at 4x4)"
          % (format(n * GEMV_LUT, ","), format(n * MOVER_LUT, ",")))
    print("  SLR0 BRAM if single-die: ~%d of %d (%d%%)"
          % (mover_bram, SLR0_BRAM_TILES, round(100.0 * mover_bram / SLR0_BRAM_TILES)))
    print("  aggregate theory at %d MHz: %.1f GMAC/s (%d MACs/cycle)"
          % (clock, n * 2 * CORES * BLOCKS * clock / 1000.0, n * 2 * CORES * BLOCKS))
    print("  floorplan: %s"
          % ("SPLIT -- engines to SLR1, movers in SLR0 (single die would be %d%% of its "
             "BRAM)" % round(100.0 * single_die_bram(n) / SLR0_BRAM_TILES) if split
             else "single die, every CU in SLR0"))
    if split:
        s0 = split_slr0_bram(n)
        print("  SLR0 BRAM when split: ~%d of %d (%d%%)%s"
              % (s0, SLR0_BRAM_TILES, round(100.0 * s0 / SLR0_BRAM_TILES),
                 "   <-- above %d%%: expect this build to fail" % round(100 * SPLIT_ABOVE)
                 if s0 > SPLIT_ABOVE * SLR0_BRAM_TILES else ""))
    print("\nnext: read %s/BUILD.md" % build_dir(n))


if __name__ == "__main__":
    main()

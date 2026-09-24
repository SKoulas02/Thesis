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


def build_dir(n):
    """x2/x4/x5 keep their names (4x4 is the original study); other shapes are tagged."""
    return "x%d" % n if TAG == "4x4" else "%s_x%d" % (TAG, n)


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
    L.append("# sparse_hbm_%s_x%d.cfg -- %d INDEPENDENT %s tenants on one U280."
             % (TAG, n, n, TAG))
    L.append("#")
    L.append("# GENERATED by multi_tenant/make_multi_build.py. Tenant k = one complete")
    L.append("# %s engine (%d cores x %d blocks, %d DSPs) with its own matrix, its own"
             % (TAG, CORES, BLOCKS, 8 * CORES * BLOCKS))
    L.append("# sparsity mode and its own %d HBM pseudo-channels HBM[%d*k .. %d*k+%d]."
             % (PCS_PER_TENANT, PCS_PER_TENANT, PCS_PER_TENANT, PCS_PER_TENANT - 1))
    L.append("#")
    L.append("# %d tenants  ->  %d of 32 HBM channels, %d mover CUs + %d engines = %d CUs."
             % (n, n * PCS_PER_TENANT, n * PCS_PER_TENANT, n, n * PCS_PER_TENANT + n))
    L.append("#")
    L.append("# THE ENGINE .xo IS UNCHANGED. This is the same krnl_gemv_sparse_%s.xo that"
             % TAG)
    L.append("# closed at %d MHz as a single tenant, instantiated %d times." % (DEFAULT_CLOCK, n))
    L.append("#")
    L.append("# NOTHING IS SHARED BETWEEN TENANTS except the card, HBM and the kernel")
    L.append("# clock. No stream crosses from one tenant to another; a cross-wired")
    L.append("# stream_connect would show up immediately because every tenant runs a")
    L.append("# DIFFERENT matrix and is compared against its own golden.")
    L.append("#")
    L.append("# Used as:  v++ -l --config sparse_hbm_%s_x%d.cfg --config impl_family.cfg \\"
             % (TAG, n))
    L.append("#                  --config slr_floorplan_%s_x%d.cfg --kernel_frequency %d ..."
             % (TAG, n, clock))
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
    L.append("# ---- memory binding: tenant k owns HBM[%d*k .. %d*k+%d] ----------------"
             % (PCS_PER_TENANT, PCS_PER_TENANT, PCS_PER_TENANT - 1))
    for k in range(n):
        base = k * PCS_PER_TENANT
        L.append("")
        L.append("# tenant %d -> HBM[%d..%d]%s"
                 % (k, base, base + PCS_PER_TENANT - 1,
                    "   (straddles the two HBM stacks at PC 16)"
                    if base < 16 < base + PCS_PER_TENANT - 1 else ""))
        pc = base
        for name in w[k] + ind[k] + a[k]:
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
        L.append("# slr_floorplan_%s_x%d_split.cfg -- the engines get SLR1." % (TAG, n))
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
            L.append("# NOT THE DEFAULT at this size. Use slr_floorplan_%s_x%d.cfg (everything"
                     % (TAG, n))
            L.append("# in SLR0) first and switch only if the build misses its frequency, which")
            L.append("# is the rule the family study followed: single die until a build fails.")
        L.append("#")
        L.append("# The %d engines are %s LUT and %d BRAM together -- they fit one die"
                 % (n, format(n * GEMV_LUT, ","), eng_bram))
        L.append("# easily. The movers stay in SLR0 beside the HBM they master.")
    else:
        L.append("# slr_floorplan_%s_x%d.cfg -- every compute unit in SLR0." % (TAG, n))
        L.append("#")
        if why:
            L.append("# NOT USED BY THIS BUILD -- it uses slr_floorplan_%s_x%d_split.cfg."
                     % (TAG, n))
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
    pcs = n * PCS_PER_TENANT
    cus = pcs + n
    d = build_dir(n)
    fp = "slr_floorplan_%s_x%d%s.cfg" % (TAG, n, "_split" if split else "")
    other_fp = "slr_floorplan_%s_x%d%s.cfg" % (TAG, n, "" if split else "_split")
    shape = "" if TAG == "4x4" else " --cores %d --blocks %d" % (CORES, BLOCKS)
    dflags = "" if TAG == "4x4" else " -DCORES=%d -DBLOCKS=%d" % (CORES, BLOCKS)
    L = []
    L.append("# %d x %s multi-tenant build" % (n, TAG))
    L.append("")
    L.append("%d independent tenants, %d of 32 HBM channels, %d mover CUs + %d engines "
             "= **%d compute units**, target **%d MHz**."
             % (n, pcs, pcs, n, cus, clock))
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
    L.append("")
    L.append("The engine `.xo` is the one the single-tenant 4x4 build already used and")
    L.append("verified. Nothing in the RTL changes, so nothing in the RTL needs re-verifying.")
    L.append("")
    L.append("## 1. Copy to the server")
    L.append("")
    L.append("```bash")
    L.append("# from the repo root")
    L.append("scp multi_tenant/%s/*.cfg multi_tenant/host_sparse_multi.cpp \\" % d)
    L.append("    multi_tenant/prep_tenants.py multi_tenant/run_multi_measure.py \\")
    L.append("    skoulas@coroni.microlab.ntua.gr:/home/skoulas/GEMV_Sparse/Vitis_multi_%s/" % d)
    L.append("```")
    L.append("")
    L.append("Create that directory first. It needs the mover `.xo` files copied in beside")
    L.append("it, exactly like every other `Vitis_<tag>` build directory:")
    L.append("")
    L.append("```bash")
    L.append("mkdir -p ~/GEMV_Sparse/Vitis_multi_%s && cd ~/GEMV_Sparse/Vitis_multi_%s" % (d, d))
    L.append("cp ~/GEMV_Sparse/Vitis_4x4/krnl_mm2s.hw.xo ~/GEMV_Sparse/Vitis_4x4/krnl_s2mm.hw.xo .")
    L.append("cp ~/GEMV_Sparse/Vitis_4x4/krnl_mm2s.hw_emu.xo ~/GEMV_Sparse/Vitis_4x4/krnl_s2mm.hw_emu.xo .")
    L.append("# impl_family.cfg lives in each Vitis_<tag> build dir on the server,")
    L.append("# NOT in ~/GEMV_Sparse/Vitis. Copy it from a family build, or scp the")
    L.append("# repo copy (Vitis/impl_family.cfg) up -- they are the same file.")
    L.append("cp ~/GEMV_Sparse/Vitis_4x4/impl_family.cfg .   # or: find ~/GEMV_Sparse -name impl_family.cfg")
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
    L.append("")
    L.append("## 3. hw_emu first")
    L.append("")
    L.append("This channel topology has never been linked. hw_emu catches a dangling")
    L.append("`stream_connect` in under an hour; in `-t hw` the same mistake costs a whole")
    L.append("build and its only symptom is one tenant hanging.")
    L.append("")
    L.append("```bash")
    L.append("emconfigutil --platform $PLATFORM --nd 1")
    L.append("export XCL_EMULATION_MODE=hw_emu")
    L.append("v++ -t hw_emu --platform $PLATFORM --config sparse_hbm_%s_x%d.cfg \\" % (TAG, n))
    L.append("    --kernel_frequency %d -l -o sparse_%s_x%d.hw_emu.xclbin \\" % (clock, TAG, n))
    L.append("    ../krnl_gemv_sparse_%s.xo krnl_mm2s.hw_emu.xo krnl_s2mm.hw_emu.xo" % TAG)
    L.append("```")
    L.append("")
    L.append("Then build the host and run it against the emulated bitstream:")
    L.append("")
    L.append("```bash")
    L.append("g++ -Wall -O2 -std=c++1y -I$XILINX_XRT/include host_sparse_multi.cpp \\")
    L.append("    -L$XILINX_XRT/lib -lOpenCL -lrt -lstdc++ -pthread%s -o host_sparse_multi"
             % dflags)
    L.append("./host_sparse_multi sparse_%s_x%d.hw_emu.xclbin %d 0 %s"
             % (TAG, n, clock, " ".join("%d:t%d" % (k, k) for k in range(n))))
    L.append("for k in $(seq 0 %d); do (cd t$k && python3 compare_gemv4_py36.py); done" % (n - 1))
    L.append("unset XCL_EMULATION_MODE")
    L.append("```")
    L.append("")
    L.append("**Gate: every tenant bit-exact against its own golden.** hw_emu timings are")
    L.append("meaningless (simulation-time timestamps); correctness only.")
    L.append("")
    L.append("## 4. Link for hardware")
    L.append("")
    L.append("In `tmux`. Check `df -h ~` first -- a full `/home` killed a link once.")
    L.append("")
    L.append("```bash")
    L.append("v++ -t hw --platform $PLATFORM \\")
    L.append("    --config sparse_hbm_%s_x%d.cfg \\" % (TAG, n))
    L.append("    --config impl_family.cfg \\")
    L.append("    --config %s \\" % fp)
    L.append("    --kernel_frequency %d \\" % clock)
    L.append("    -l -o sparse_%s_x%d_%d.xclbin \\" % (TAG, n, clock))
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
    L.append("mkdir -p ~/GEMV_Sparse/reports_multi_%s && cp -r _x/reports/link/imp ~/GEMV_Sparse/reports_multi_%s/"
             % (d, d))
    L.append("```")
    L.append("")
    L.append("## 5. On the card")
    L.append("")
    L.append("```bash")
    L.append("# all tenants together")
    L.append("./host_sparse_multi sparse_%s_x%d_%d.xclbin %d 0 %s"
             % (TAG, n, clock, clock, " ".join("%d:t%d" % (k, k) for k in range(n))))
    L.append("# each tenant ALONE -- the uncontended baseline for interference")
    for k in range(n):
        L.append("./host_sparse_multi sparse_%s_x%d_%d.xclbin %d 0 %d:t%d"
                 % (TAG, n, clock, clock, k, k))
    L.append("```")
    L.append("")
    L.append("Interference for tenant k = its span with everyone running / its span alone.")
    L.append("Check the printed **overlap window** first: if the tenants barely overlapped,")
    L.append("the comparison says nothing.")
    L.append("")
    L.append("## Pass criteria")
    L.append("")
    L.append("- `xclbinutil --info` lists **%d** HBM channels bound and **%d** CUs" % (pcs, cus))
    L.append("- the link reports **WNS >= 0** at %d MHz (and note what it closed at)" % clock)
    L.append("- **every tenant bit-exact against its own golden**, both in hw_emu and on the card")
    L.append("- the overlap window is a large fraction of the union window")
    return "\n".join(L) + "\n"


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
    a = ap.parse_args()

    configure(a.config)
    clock = a.clock if a.clock else DEFAULT_CLOCK
    n = a.tenants
    if n < 1:
        raise SystemExit("--tenants must be >= 1")
    need = n * PCS_PER_TENANT
    if need > HBM_PCS:
        raise SystemExit("%d tenants need %d HBM channels; the U280 has %d. "
                         "The ceiling for %s is %d tenants."
                         % (n, need, HBM_PCS, TAG, HBM_PCS // PCS_PER_TENANT))

    bram_forces = single_die_bram(n) > SPLIT_ABOVE * SLR0_BRAM_TILES
    if a.floorplan == "auto":
        split, why = bram_forces, ("bram" if bram_forces else "")
    else:
        split = a.floorplan == "split"
        why = ("bram" if bram_forces else "explicit") if split else ""
    d = os.path.join(HERE, build_dir(n))
    if not os.path.isdir(d):
        os.makedirs(d)
    files = [("sparse_hbm_%s_x%d.cfg" % (TAG, n), link_cfg(n, clock)),
             ("slr_floorplan_%s_x%d.cfg" % (TAG, n), floorplan_cfg(n, False, why)),
             ("slr_floorplan_%s_x%d_split.cfg" % (TAG, n), floorplan_cfg(n, True, why)),
             ("BUILD.md", build_md(n, clock, split, why))]
    for name, text in files:
        with io.open(os.path.join(d, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print("  wrote %s" % os.path.join(build_dir(n), name))

    mover_bram = n * MOVER_BRAM + n * GEMV_BRAM + 30
    print("\n%d tenants: %d HBM channels (%d spare), %d mover CUs + %d engines = %d CUs"
          % (n, need, HBM_PCS - need, need, n, need + n))
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
    print("\nnext: read %s/BUILD.md" % build_dir(n))


if __name__ == "__main__":
    main()

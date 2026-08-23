# Integration Plan — Getting Both GEMV Architectures Running on the Alveo U280

**Status at the time of writing (2026-07-29).** Both architectures are RTL-complete,
verified bit-exact against the Python golden model (behavioural *and* post-implementation
functional simulation), and implemented out-of-context on `xcu280-fsvh2892-2L-e`. The
module-level comparison numbers are collected in `Utilization.xlsx`.

**What is missing** is everything between the AXI4-Stream boundary and the board: the
HBM/DDR read-write engine, kernel packaging, host software, and the measurement
campaign.

**Goal.** Run both architectures on a real U280 **at 8 cores** and compare them on
throughput, bandwidth, area, frequency and power. Two secondary axes vary the *memory
system* under that fixed architecture pair: memory type (HBM vs DDR) and bus bandwidth.

**Scope note (2026-08-18):** 8 cores is the fixed reference configuration, not a variable.
The core-count sweep is **not a project goal** — it may be attempted some day, but nothing is
scheduled or gated on it. See Phase 1 and §7.4.

---

## The one architectural idea that makes this tractable

The compute blocks (`two2N`, `dense_gemv`) are **pure AXI4-Stream** and know nothing
about memory. Every difference between HBM and DDR, every change in port width, every
change in burst strategy is absorbed by the engine.

> **The engine changes across every experiment. The compute block never changes.**

This is what keeps the sparse-vs-dense comparison honest across the whole sweep — the
verified RTL is never touched again, so any performance difference is attributable to
the architecture or the memory system, never to a re-verification.

Corollary: **do not** let memory-system concerns leak into the compute RTL. If an
experiment seems to require changing `two2N`/`dense_gemv`, stop and reconsider — it
almost certainly belongs in the engine.

---

## Phase 0 — Decisions and prerequisites

### 0.1 Confirm the toolchain — ✅ **DONE (verified 2026-08-06 on coroni)**

Everything below is measured, not assumed. Nothing in the integration phase is blocked on
tooling or hardware access.

| Item | Value |
|---|---|
| Vitis / Vivado | **2021.1** (`/opt/Xilinx/Vitis/2021.1`) — newest installed |
| XRT | **2.13.0, branch 2022.1** — newer than Vitis; safe skew (XRT is backward compatible) |
| Platform | **`xilinx_u280_xdma_201920_3`** — exposes **both HBM and DDR** |
| Card | **U280 at BDF `0000:af:00.1`**, shell `xilinx_u280_xdma_201920_3`, Device Ready: Yes |
| Shell ↔ platform | **MATCHED** — no reflash needed |
| Host | Ubuntu 18.04.6, 40 cores, 192 GB RAM |
| GUI | X2Go, full GUI for Vivado / Vitis / `vitis_hls` |

**Consequences that matter later:**

1. **Three Alveo cards are in this machine** — U200 at `0000:3b:00.1`, U50 at
   `0000:86:00.1`, U280 at `0000:af:00.1`. **Select by BDF, never by device index** —
   the first device the runtime hands you will most likely be the U200, and the failure will
   look like a shell mismatch. In the OpenCL host (§4) this means
   **`xcl::get_xil_devices()` returns all three** — filter on the device name and assert you
   got the U280 before loading the xclbin.
2. **Two U280 platforms are installed and one is poison.**
   `xilinx_u280_gen3x16_xdma_1_202211_1` is built for 2022.2; Vitis 2021.1 cannot parse its
   `.xpfm` and fails with *"failed to parse the XPFM file"*. Because `platforminfo -l` scans
   the whole repo and dies on the first bad file, its mere presence breaks platform
   discovery. **Scope corrected 2026-08-22: this affects `platforminfo -l` only.** A `v++`
   compile enumerated all 15 installed platforms, the 2022.2 one included, without error --
   so no symlink workaround is needed for builds. Just pass the absolute `.xpfm`:
   `/opt/xilinx/platforms/xilinx_u280_xdma_201920_3/xilinx_u280_xdma_201920_3.xpfm`
   (v++-verified). Keep the symlink trick in reserve only for `platforminfo`.
3. **The HBM-vs-DDR sweep (§7.2) is viable on this single platform** — a `connectivity.cfg`
   change only, no re-platforming.
4. The board is **on the build server**, so no `.xclbin` shuffling between machines.
5. Shared machine — check for card contention before a run.

Environment setup, to be sourced before any tool work:

```bash
source /opt/Xilinx/Vitis/2021.1/settings64.sh    # sets up Vitis AND Vivado
source /opt/xilinx/xrt/setup.sh                  # sets XILINX_XRT
export PLATFORM_REPO_PATHS=~/platforms_2021      # the isolated good platform
```

### 0.2 Kernel topology — recommended: THREE kernels, stream-connected

*(Revised 2026-07-29 after reading the AMD tutorials and examples — see the
`reference-vitis-rtl-kernel-workflow` memory. The evidence favours the multi-kernel path
over the single-RTL-kernel one originally recommended here.)*

| Option | Structure | Verdict |
|---|---|---|
| **A. Three kernels, `stream_connect`** | read-stage (HLS) → compute (RTL, free-running) → write-stage (HLS) | **Recommended.** This is AMD's documented mainstream pattern, with two working examples |
| B. Single RTL kernel | HLS engine as IP + compute block in one RTL top, `package_xo` | Viable fallback; more hand-wiring, and you own the control-register plumbing |
| C. HLS kernel with RTL blackbox | compute block dropped into HLS | Blackbox protocol support is restrictive for 13 in + 4 out streams |

Three AMD examples match our topology, and one of them matches it almost exactly:

| Example | What it shows | Relevance |
|---|---|---|
| `rtl_adder_streams` | 3 RTL kernels chained mem→stream→stream→mem | the chained shape |
| `rtl_streaming_k2k_mm` | 2 stream-connected RTL kernels + C++ `krnl_mm2s`/`krnl_s2mm` data movers | movers in C++/HLS, compute in RTL |
| **`rtl_streaming_free_running_k2k`** | **`krnl_mm2s` (C++) → free-running `ap_ctrl_none` RTL kernel → `krnl_s2mm` (C++)** | **exactly our topology** |

`rtl_streaming_free_running_k2k` settles it: an `ap_ctrl_none` RTL compute kernel sandwiched
between two HLS data-mover kernels is a shipped, maintained AMD example. Its whole link
config is two lines:

```ini
[connectivity]
stream_connect=krnl_mm2s_1.out:myadder_1.in
stream_connect=myadder_1.out:krnl_s2mm_1.in
```

Following a documented, exemplified path is worth a great deal on a project where hw_emu
debugging is the expensive part.

```
13 × krnl_mm2s  (HLS, ap_ctrl_hs)   1 m_axi → 1 AXIS   each on its own HBM PC
        │  stream_connect × 13
krnl_gemv         (RTL, ap_ctrl_none, FREE-RUNNING)   two2N / dense_gemv
        │  stream_connect × 4
 4 × krnl_s2mm   (HLS, ap_ctrl_hs)   1 AXIS → 1 m_axi
```

**Two mover source files, many compute units.** `krnl_mm2s` and `krnl_s2mm` are each one
trivial kernel replicated by `nk=` — 10 (dense) or 13 (sparse) read CUs and 4 write CUs.
See §0.2e for why this shape was chosen over one wide read kernel.

**Why the compute block fits `ap_ctrl_none` perfectly:** free-running kernels "have no
control signal ports, cannot be started or stopped", interact only through streams, and
start automatically when the xclbin is programmed. `two2N` and `dense_gemv` have exactly
`clk`, `resetn` and AXI-Stream ports — nothing else. The host never starts them; the
write-stage kernel owns the control interface and signals `ap_done`.

### 0.2b BLOCKER — the compute blocks' stream ports are bundled

`s_axis_w_tdata` is **one 2048-bit port with an 8-bit `tvalid`/`tready`/`tlast` vector** —
eight logical AXI-Stream channels collapsed into a single port declaration. The **output side
is bundled too**: `m_axis_c_tdata` is one 1024-bit port with 4-bit `tvalid`/`tlast`/`tready`
vectors. Vitis needs **17 separate, properly named AXIS interfaces** (13 slave + 4 master:
`s_axis_w0_tdata`, `s_axis_w0_tvalid`, `s_axis_w0_tready`, `s_axis_w0_tlast`,
`s_axis_w0_tkeep`, …) so the packager can infer them.

Fix with a **thin mechanical splitter wrapper** (`dense_gemv_axis.vhd`, `two2N_axis.vhd`).
Per §0.2d it must do four things, all of them zero-logic:

1. **slice** the wide `tdata` ports into per-PC 256-bit ports (little-endian: PC *k* =
   `tdata[256k+255 : 256k]`), and the `tvalid`/`tready`/`tlast` vectors into scalars;
2. **drive `tkeep`** — tie every master `tkeep` to all-1s; ignore every slave `tkeep`
   (the core never has a ragged tail: all beats are full 256-bit PC words);
3. **supply `tlast` on the 3 index streams**, which have no `tlast` port today — the
   spec requires one. Mirror the weight `tlast`; the core ignores it either way;
4. **keep the port list flat** — no records, no arrays — so the wizard/packager infers
   each interface cleanly.

Still pure re-wiring, so the verified RTL is never touched. Do this in Phase 1 alongside
the parameterisation audit; it is a prerequisite for packaging anything.

### 0.2c How the compute kernel gets packaged — **RTL Kernel Wizard**, not a hand-written `kernel.xml`

*(Added 2026-08-10 after re-reading the sources. This supersedes the hand-written
`kernel.xml` route sketched in §3.3/§3.5 below — those sections have been rewritten to match.)*

`rtl_streaming_free_running_k2k` does **not** hand-write `kernel.xml`. Its `gen_xo.tcl`
drives the **RTL Kernel Wizard**, which generates the AXIS-conformant kernel shell *and* the
`kernel.xml`, and then packages it.

**Read from the `2021.1` branch — our toolchain — not `main`** (on 2021.1 the script lives at
`src/gen_xo.tcl`, invoked from `config.mk` as
`vivado -mode batch -source ./src/gen_xo.tcl -tclargs <xo> myadder $(TARGET) <xpfm> <xsa>`):

```tcl
create_ip -name rtl_kernel_wizard -vendor xilinx.com -library ip -version 1.0 \
          -module_name myadder
# CONFIG properties actually set (verbatim, 2021.1):
#   CONFIG.Component_Name  myadder
#   CONFIG.KERNEL_NAME     myadder
#   CONFIG.KERNEL_CTRL     ap_ctrl_none
#   CONFIG.NUM_INPUT_ARGS  0
#   CONFIG.NUM_M_AXI       0
#   CONFIG.NUM_AXIS        2
#   CONFIG.AXIS00_NAME     out
#   CONFIG.AXIS01_NAME     in
generate_target {instantiation_template} [get_files myadder.xci]
open_example_project -force -in_process -dir [pwd] [get_ips myadder]
package_xo -xo_path myadder_ex/imports/myadder.xo -kernel_name myadder \
           -ip_directory myadder_ex/myadder \
           -kernel_files myadder_ex/imports/myadder_cmodel.cpp
```

Every property name above is **confirmed on 2021.1**. Note what is *not* there: no
`AXISnn_MODE` and no `AXISnn_WIDTH` — the example sets only names and lets the defaults
supply direction and width. Whether those properties exist at all, and how the wizard
decides which stream is master vs slave, is what S7 probes.

Note what is **absent**: no `-kernel_xml`, no `-ctrl_protocol` flag (the wizard's
`KERNEL_CTRL` property carries it), no `ipx::add_register` plumbing, no AXI4-Lite map at all
for the free-running kernel. Zero `m_axi`, zero scalar args — exactly our compute block's
profile.

**Consequence for us:** the compute kernel is packaged wizard-first — configure
`NUM_AXIS = 17` (13 slave + 4 master) with `KERNEL_CTRL = ap_ctrl_none`, generate the
example project, and replace the wizard's placeholder VADD IP with our splitter-wrapped
`dense_gemv` / `two2N`. The wizard owns the packaging correctness; we own only the RTL.
Hand-written `kernel.xml` (§3.3) stays documented as the fallback if the wizard cannot
express 17 streams.

The **data-mover kernels are ordinary HLS C++ kernels** (`krnl_mm2s` / `krnl_s2mm` in the
example) — `v++ -c` compiles them straight to `.xo`, no wizard and no packaging Tcl.

### 0.2d AXI4-Stream conformance rules (UG1393 “Streaming Interfaces”) — these constrain the wrapper

Verified from the 2021.1 spec; every one of these applies to the splitter wrapper:

| Rule | Our status |
|---|---|
| `TDATA` must be **8/16/32/64/128/256 or 512 bits** (1–64 bytes, power of 2) | 256 b per PC ✅ |
| `TKEEP` (per byte) **must be all 1s when `TLAST` is 0**, and may never be all zeros | ❌ **we have no `TKEEP` at all** — wrapper must drive it |
| `TLAST` **must be asserted at the end of a packet** | ❌ the 3 index streams have **no `TLAST`** — wrapper must supply one |
| Max **32 AXI4-Stream interfaces per kernel** | 17 sparse / 14 dense ✅ — but 16-core sparse is **exactly 32**, at the limit |
| Interfaces are one-way; cannot be bidirectional | ✅ |
| Every port must be associated with a bus interface at packaging time | wizard handles it |
| `stream_connect` auto-inserts a **width converter** on mismatch, and takes an optional **FIFO depth** | useful — see §3.4 |

The wizard-generated AXIS ports carry `TDATA/TKEEP/TLAST/TVALID/TREADY`, so the wrapper must
present all five per channel even though the verified RTL only knows four.

### 0.2e Conformance audit against the reference examples — 2026-08-10

Every design decision in this plan, checked line-by-line against
`rtl_streaming_free_running_k2k` **on the `2021.1` branch**. ✅ = the reference does it our
way; ⚠ = we deviate deliberately; ❌ = the plan was wrong and has been corrected.

| Plan says | Reference does | |
|---|---|---|
| Three kernels: mover → free-running RTL → mover | same | ✅ |
| Compute kernel `ap_ctrl_none`, 0 `m_axi`, 0 scalar args | `KERNEL_CTRL ap_ctrl_none`, `NUM_M_AXI 0`, `NUM_INPUT_ARGS 0` | ✅ |
| Compute kernel packaged by the RTL Kernel Wizard | `create_ip rtl_kernel_wizard -version 1.0` → `package_xo` | ✅ |
| Movers are plain HLS C++ via `v++ -c` | `krnl_mm2s.cpp` / `krnl_s2mm.cpp`, no packaging Tcl | ✅ |
| Stream type `ap_axiu<256,0,0,0>` | `typedef ap_axiu<DWIDTH,0,0,0> pkt;` (DWIDTH 512) | ✅ width differs, form identical |
| Pragmas: `m_axi ... offset=slave bundle=`, `axis port=`, `s_axilite` | identical set | ✅ |
| Read stage asserts TLAST from the loop bound | sets `last = 1` on the final iteration | ✅ |
| Write stage terminates on a **beat count** (`n_out_beats`), not TLAST | `for (i = 0; i < size*32/DWIDTH; i++)` — count-based, ignores TLAST | ✅ |
| Link by `stream_connect` in a `.cfg` | `myadder.cfg`, two `stream_connect` lines | ✅ |
| ~~`HLS DATAFLOW` with many concurrent per-PC loops~~ → **single loop per CU** | single loop, no DATAFLOW | ✅ **resolved 2026-08-10** |
| ~~One read-stage kernel with 10–13 `m_axi` bundles~~ → **replicated 1-PC movers** | one `m_axi` + one stream per kernel | ✅ **resolved 2026-08-10** |
| ~~Host uses the XRT native C++ API~~ → **OpenCL via `xcl2.hpp`** | OpenCL, `cl::CommandQueue` + `enqueueTask` | ✅ **resolved 2026-08-10** |
| “Start the sink before the source” | enqueues **`krnl_mm2s` first, then `krnl_s2mm`**, on an out-of-order queue | ❌ **corrected — see §4** |
| Wizard Tcl uses `AXISnn_MODE` / `AXISnn_WIDTH` | sets **only** `AXISnn_NAME` | ❌ those two were my invention; S7 probes whether they exist |
| `gen_xo.tcl` lives in `scripts/` | on 2021.1 it is `src/gen_xo.tcl` | ❌ corrected in §0.2c |

**Both open deviations were closed on 2026-08-10, in favour of the reference in each case:**

1. **Mover width → replicate the 1-PC mover.** `nk=krnl_mm2s:13:...` instead of one kernel
   owning 13 `m_axi` bundles. Every CU is then literally the proven example, no `DATAFLOW`
   is needed anywhere, and each CU carries its own beat count — which suits activations
   stopping early while weights keep streaming. Cost: ~17 AXI-Lite control interfaces and 17
   `enqueueTask` calls (a host-side loop).
2. **Host API → OpenCL** (`xcl2.hpp`, out-of-order `cl::CommandQueue`, `enqueueTask`), the
   API every 2021.1 RTL-kernel example uses. It is also the better-trodden path for
   `xrt.ini` profiling on 2021.1, which the entire §7/§8 measurement campaign depends on.
   `xcl2.cpp`/`xcl2.hpp` are vendored from the examples repo (Apache-2.0).

The result: **no part of the Vitis-side implementation is now without a working 2021.1
reference to copy from.**

### 0.3 Decide what "done" means for the thesis

Write this down before building, because it determines how much of the sweep matters:

- minimum: both architectures run correctly on hardware at 8 cores, with throughput and
  bandwidth measured
- target: + the HBM/DDR comparison (§7.2)
- stretch: + the bus-width / burst-tuning sweep (§7.3)
- **out of scope: the core-count sweep** (§7.4) — 8 cores is the fixed reference config

---

## Phase 1 — RTL parameterisation audit — ❌ **OUT OF SCOPE (2026-08-18)**

**The thesis question is dense vs sparse at a fixed 8 cores.** The core-count sweep is not a
project goal; it may be attempted some day, but nothing should be scheduled or gated on it.
This whole phase exists only to enable §7.4, so it is dropped — not deferred.

Kept below for reference in case the sweep is ever revisited. If it is, budget for the
hidden cost the wrapper introduces: `dense_gemv_axis` / `two2N_axis` have **flat,
hand-written port lists** (VHDL cannot generate ports from a generic), so every core count
needs a regenerated wrapper *and* a regenerated RTL Kernel Wizard configuration on top of
re-verifying the RTL.

### 1.1 Verify the generics propagate

The tops expose `PC_WIDTH`, `W_PCS`, `IND_PCS`, `A_PCS`, `C_PCS`, `CORES_NUM`,
`BLOCKS_NUM`, `IND_BITS`. Check that changing `CORES_NUM` alone produces a consistent
design — several widths are *derived* and may be hardcoded in places.

Relationships that must hold (BLOCKS_NUM = 8, 16-bit elements, 256-bit PCs):

| Quantity | Formula | 4 cores | 8 cores | 16 cores |
|---|---|---|---|---|
| Weight PCs | `CORES_NUM` | 4 | 8 | 16 |
| Index bits | `80 × CORES_NUM` | 320 | 640 | 1280 |
| Index PCs | `ceil((IND_BITS+2)/256)` | 2 | 3 | 6 |
| Activation PCs | 2 (fixed, 32-el window) | 2 | 2 | 2 |
| Output bits | `128 × CORES_NUM` | 512 | 1024 | 2048 |
| Output PCs | `CORES_NUM/2` | 2 | 4 | 8 |
| **Total PCs (sparse)** | | **10** | **17** | **32** |
| **Total PCs (dense)** | | **8** | **14** | **26** |

Note 16 cores sparse needs **32 PCs** — exactly all of HBM, leaving nothing spare. Worth
knowing before promising that data point.

### 1.2 Re-run the co-sim ladder at each core count

The Python generators hardcode `CORES = 8`. Parameterise them, then re-verify at 4 and
16 cores. **A core-count sweep with unverified RTL is worthless** — if the numbers look
odd later you will not know whether it is the design or a packing bug.

Deliverable: `CORES_NUM ∈ {4, 8, 16}` verified bit-exact for both architectures.

### 1.3 Decide the bus-bandwidth axis

"Bus bandwidth" can mean two different experiments — pick deliberately:

- **(a) Fewer/more PCs at fixed 256-bit width** — this is really the core-count sweep,
  since PC count is tied to `CORES_NUM`.
- **(b) Wider PCs** — `PC_WIDTH` 256 → 512. HBM pseudo-channels are natively 256-bit, so
  512 means pairing two PCs in the engine. For DDR (512-bit AXI) this is the natural
  width.

(b) is the more interesting result and pairs naturally with the DDR experiment — and since
the core-count axis is out of scope, (a) is not available anyway: PC count is tied to
`CORES_NUM`, so varying it *is* the core sweep under another name. **§7.3 means (b).**

---

## Phase 2 — The HLS data movers (`krnl_mm2s` / `krnl_s2mm`)

Per §0.2/§0.2c/§0.2e: **two tiny HLS kernels, replicated as many compute units** — the same
two source files as `rtl_streaming_free_running_k2k`, instantiated once per HBM
pseudo-channel. Each is an ordinary `v++ -c` C++ kernel with **one** `m_axi`, **one** `axis`
port and an `ap_ctrl_hs` control interface; neither ever sees the compute block.

### 2.1 What the movers own

From the architecture reference, the read/write stages are responsible for:

- per-PC read scheduling and burst generation
- **stopping the activation stream at end-of-vector** (loaded once, then silent)
- asserting **activation TLAST** (once per lap — drives the accumulator flush)
- asserting **weight TLAST** (end of matrix — the end-of-calculation marker)
- little-endian packing, and for sparse the **sparsity code in index PC2 padding [641:640]**
- draining the 4 output streams back to memory

### 2.2 Structure — two tiny top functions, replicated

**One PC per compute unit.** No `DATAFLOW`, no multi-bundle kernel, no per-stream
specialisation: the same `krnl_mm2s` serves weights, indices and activations, because every
difference between those streams is either a *beat count* (an argument) or *buffer content*
(packed by the host). Stream elements are `ap_axiu<256,0,0,0>` — `data`/`keep`/`last`, the
type the AXIS rules in §0.2d require.

```cpp
typedef ap_axiu<256,0,0,0> axis_t;

// ---- memory -> one stream.  Instantiated 10x (dense) / 13x (sparse). ----
void krnl_mm2s(const ap_uint<256>* in, hls::stream<axis_t>& out, unsigned n_beats) {
#pragma HLS INTERFACE m_axi     port=in offset=slave bundle=gmem \
        max_read_burst_length=64 num_read_outstanding=32
#pragma HLS INTERFACE axis      port=out
#pragma HLS INTERFACE s_axilite port=in
#pragma HLS INTERFACE s_axilite port=n_beats
    for (unsigned k = 0; k < n_beats; ++k) {
    #pragma HLS PIPELINE II=1
        axis_t b;
        b.data = in[k];
        b.keep = -1;                    // all ones: full 256-bit beat, never ragged
        b.last = (k == n_beats - 1);    // TLAST falls out of the loop bound
        out.write(b);
    }
}

// ---- one stream -> memory.  Instantiated 4x. ----
void krnl_s2mm(hls::stream<axis_t>& in, ap_uint<256>* out, unsigned n_beats) {
    for (unsigned k = 0; k < n_beats; ++k) {
    #pragma HLS PIPELINE II=1
        out[k] = in.read().data;        // count-based, exactly like the reference:
    }                                   // TLAST is never inspected on the write side
}
```

**Why one kernel covers all three input streams.** Weight TLAST (end of matrix) and
activation TLAST (end of vector) are both just "last beat of my loop" — the two markers
differ in *meaning*, not in *generation*. The activation CU simply has a much smaller
`n_beats` and finishes early, which is precisely the "channel stops" behaviour the replay
buffer expects. The index CUs get a TLAST they do not need, which the AXIS spec requires
anyway (§0.2d). The 2-bit sparsity code lives in the *buffer contents* at index PC2 bits
[641:640], written by the host packer — not by any mover.

**Termination.** The 4 `krnl_s2mm` CUs are what the host waits on: each counts `n_beats`,
returns, and that return raises its `ap_done`. This is count-based, matching the reference
exactly — the write side never inspects TLAST. The compute kernel between them never starts
or stops.

The movers keep `s_axilite` / `ap_ctrl_hs` — **only the compute kernel is `ap_ctrl_none`**.
Free-running kernels may not have memory ports (UG1393), which is precisely why all the
`m_axi` work lives in the movers and never with the compute block.

Tune `max_read_burst_length` and `num_read_outstanding` later — they are a legitimate
part of the bandwidth experiment.

### 2.3 Verify the engine standalone — **reuse the existing hex files**

This is the highest-value shortcut available. The engine's job is to reproduce exactly
the beats already sitting in `weights.hex` / `indices.hex` / `activations.hex`.

1. Convert the hex files into binary memory images (small Python script).
2. `csim`: engine reads the images, writes its streams to files, diff against the `.hex`.
3. `cosim`: same check through the RTL.

If the engine reproduces those files beat-for-beat, it is correct — and the whole
compute-side verification chain already built stays valid unchanged.

Deliverable: both movers pass csim + cosim against the existing stimulus.

---

## Phase 3 — Kernel packaging

*(Rewritten 2026-08-10. The previous §3.1–3.3 described option B — one RTL top wrapping the
HLS engine as IP, with a hand-written `kernel.xml`. That contradicted the three-kernel
decision in §0.2 and is not what the AMD examples do; see §0.2c.)*

**Three `.xo` files, packaged three different ways.**

| Kernel | Type | Control | CUs | How it becomes an `.xo` |
|---|---|---|---|---|
| `krnl_mm2s` | HLS C++ | `ap_ctrl_hs` | 10 dense / 13 sparse | `v++ -c -k krnl_mm2s` |
| `krnl_gemv_dense` / `krnl_gemv_sparse` | RTL | **`ap_ctrl_none`** | 1 | RTL Kernel Wizard → `package_xo` |
| `krnl_s2mm` | HLS C++ | `ap_ctrl_hs` | 4 | `v++ -c -k krnl_s2mm` |

### 3.1 The two movers — nothing to package

```bash
v++ -c -t $TGT --platform $PLATFORM -k krnl_mm2s -o krnl_mm2s.xo krnl_mm2s.cpp
v++ -c -t $TGT --platform $PLATFORM -k krnl_s2mm -o krnl_s2mm.xo krnl_s2mm.cpp
```

**Two `.xo` files total, however many PCs there are** — replication happens at link time via
`nk=`, not at compile time. No `export_design`, no `kernel.xml`, no register map to
transcribe by hand. That is the whole reason the movers are their own kernels.

### 3.2 The compute kernel — RTL Kernel Wizard

**Property names captured from the 2021.1 GUI itself (S8a, 2026-08-10)** — Vivado echoes the
Tcl for every dialog action, so this is the tool's own output, not a reconstruction:

```tcl
create_ip -name rtl_kernel_wizard -vendor xilinx.com -library ip -version 1.0 \
          -module_name krnl_gemv_dense
set_property -dict [list \
    CONFIG.KERNEL_NAME     {krnl_gemv_dense} \
    CONFIG.KERNEL_CTRL     {ap_ctrl_none} \
    CONFIG.NUM_RESETS      {1} \
    CONFIG.NUM_INPUT_ARGS  {0} \
    CONFIG.NUM_M_AXI       {0} \
    CONFIG.NUM_AXIS        {14} \
    CONFIG.AXIS00_NAME {s_axis_w0} CONFIG.AXIS00_MODE {read_only}  CONFIG.AXIS00_NUM_BYTES {32} \
    ...                                                                                        \
    CONFIG.AXIS13_NAME {m_axis_c3} CONFIG.AXIS13_MODE {write_only} CONFIG.AXIS13_NUM_BYTES {32} \
    ] [get_ips krnl_gemv_dense]

generate_target all [get_files krnl_gemv_dense.xci]
open_example_project -force -in_process -dir [pwd] [get_ips krnl_gemv_dense]
# replace krnl_gemv_dense_example with dense_gemv_axis.vhd (+ its sub-modules)
package_xo -force -xo_path krnl_gemv_dense.xo -kernel_name krnl_gemv_dense \
           -ip_directory ./krnl_gemv_dense_ex/krnl_gemv_dense
```

Three names differ from the earlier reconstruction — **`NUM_RESETS`** (not `HAS_RESET`),
**`AXISnn_NUM_BYTES`** (not `AXISnn_WIDTH`), and the mode values are
**`read_only` / `write_only`** (not `Master` / `Slave`, which is only the GUI's wording).

> ⚠ **Set `AXISnn_MODE` on every stream explicitly in the script.** The GUI's default
> alternates `write_only` (even indices) and `read_only` (odd), and Vivado echoes only the
> properties that *differ* from default — so the captured Tcl legitimately omits `MODE` on
> the rows where the alternating default already happened to be right. Copying that echo
> verbatim into a script that later changes `NUM_AXIS` would silently flip stream
> directions. `gen_xo_dense.tcl` must be explicit on all 14.

`-kernel_name` **must match the RTL module name**. Sparse is identical with 17 streams
(add `s_axis_ind0..2` as `read_only`).

**Generated hierarchy** (from `Open IP Example Design`): the kernel top is
`krnl_gemv_dense.v`, which instantiates `inst_example : krnl_gemv_dense_example`
(`krnl_gemv_dense_example.sv`) — a placeholder that chains each slave stream to a master via
`krnl_gemv_dense_example_vadd_axis` (unpaired slaves go `..._to_NULL`).

**The swap is inside `krnl_gemv_dense.v`, not a file replacement.** The generated top carries
the instruction itself — *"Top level of the kernel. Do not modify module name, parameters or
ports"* around the header, and *"Add kernel logic here. Modify/remove example code as
necessary. // Example RTL block. Remove to insert custom logic."* around the body. So: keep
the header exactly as generated (that is what `package_xo` infers the 14 AXIS interfaces
from), delete the `krnl_gemv_dense_example` instance, and put `dense_gemv_axis` in its place.
The whole placeholder hierarchy (`_example`, `_example_vadd_axis`, `_example_adder`,
`_example_number_generator`, `_example_counter`) is then dead and can be dropped from the
project.

The edited top is checked in at `GEMV_Dense_Source/Design/krnl_gemv_dense.v`.

**Port names match `dense_gemv_axis` one-for-one** (verified against the generated file
2026-08-10): `ap_clk`, `ap_rst_n`, and per stream `_tvalid` / `_tready` / `_tdata[255:0]` /
`_tkeep[31:0]` / `_tlast`. The generated top also declares `C_*_TDATA_WIDTH` parameters used
only for its own port widths; the VHDL wrapper's generics already default to the matching
values, and Verilog→VHDL instantiation takes those defaults, so nothing is passed across.

**Wizard capabilities — VERIFIED 2026-08-10** in the 2021.1 GUI (RTL Kernel Wizard 1.0) on
`xcu280-fsvh2892-2L-e`:

| Wizard page | Finding |
|---|---|
| General Settings | **Kernel control interface = `ap ctrl none` is selectable**, nothing greys out. Kernel type `RTL`, Number of clocks `1`. |
| General Settings | ⚠ **`Has reset` defaults to `0`** — must be set to **1**, our blocks need `resetn` |
| Scalars | count goes to **0** ✅ |
| Global Memory | **forced to 0 and locked** once `ap_ctrl_none` is chosen — the tool enforces UG1393's "free-running kernels have no memory ports" |
| Streaming interfaces | **min 1, max 32** — 14 (dense) and 17 (sparse) both fit ✅ |
| Streaming interfaces | per stream: **Name**, **Mode** (`Master`/`Slave`), **Width in BYTES** from {1, 2, 4, 8, 16, 32, 64} |
| Streaming interfaces | ⚠ defaults are `axis00, axis01, …`, **alternating Master/Slave**, width **64 B** — all three must be overridden |
| Summary | equivalent prototype is `hls::stream<qdma_axis<W,0,0,0>>` — **confirms every stream carries TDATA + TKEEP + TLAST**, so the §0.2b wrapper must drive `tkeep` |
| Summary | the wizard also emits example host code at `./exports/src/host_example.cpp` — a free starting point for §4 |

Consequences worth recording:

- **Width 32 B = 256 b** is exactly our PC width, and **64 B = 512 b is offered**, so the
  §7.3 `PC_WIDTH` 256→512 experiment is expressible in the wizard with no fallback needed.
- **32 streams is a hard ceiling**, matching the UG1393 per-kernel limit exactly — so
  16-core sparse (§1.1, 32 PCs) sits precisely at the maximum with nothing spare.
- `AXISnn_MODE` and `AXISnn_WIDTH` **do** exist after all (an earlier draft flagged them as
  invented); the reference simply never set them.

### 3.3 Fallback — hand-written `kernel.xml`

Only if the wizard cannot express 17 streams. `kernel.xml` then declares each stream port
with `portType="stream"` (**stream ports omit `range` and `base`**), and:

```tcl
package_xo -force -xo_path krnl_gemv_sparse.xo -kernel_name krnl_gemv_sparse \
           -ip_directory ./ip -kernel_xml kernel.xml -ctrl_protocol ap_ctrl_none
```

### 3.4 Link configuration

One `.cfg` does both jobs — memory binding (`sp=`) and kernel-to-kernel streams
(`stream_connect=`, verified syntax from `rtl_adder_streams/adder.cfg`):

Dense (10 read CUs + 4 write CUs = 14 HBM PCs):

```ini
[connectivity]
# --- one CU per pseudo-channel, named so sp= and stream_connect= stay readable ---
nk=krnl_mm2s:10:mm2s_w0.mm2s_w1.mm2s_w2.mm2s_w3.mm2s_w4.mm2s_w5.mm2s_w6.mm2s_w7.mm2s_a0.mm2s_a1
nk=krnl_gemv_dense:1:gemv
nk=krnl_s2mm:4:s2mm_c0.s2mm_c1.s2mm_c2.s2mm_c3

# --- memory binding: each CU's single m_axi arg to its own HBM PC ---
sp=mm2s_w0.in:HBM[0]
sp=mm2s_w1.in:HBM[1]
# ... w2..w7 -> HBM[2..7], a0,a1 -> HBM[8,9]
sp=s2mm_c0.out:HBM[10]
# ... c1..c3 -> HBM[11..13]

# --- kernel-to-kernel streams ---
stream_connect=mm2s_w0.out:gemv.s_axis_w0
stream_connect=mm2s_w1.out:gemv.s_axis_w1
# ... 10 input streams total (13 for sparse, + s_axis_ind0..2)
stream_connect=gemv.m_axis_c0:s2mm_c0.in
# ... 4 output streams

[clock]
freqHz=300000000:gemv
```

Note `nk=<kernel>:<count>:<name1>.<name2>...` names each CU explicitly — without it you are
referring to `krnl_mm2s_1` … `krnl_mm2s_10` by position, and one transposed digit silently
binds a weight PC to an activation stream.

Full syntax (UG1393):
`stream_connect=<cu>.<output_port>:<cu>.<input_port>[:<fifo_depth>]` — `<cu>` is the
*compute-unit* name (`<kernel>_<n>`, or whatever `nk=` assigns), and `sc=` is the
abbreviation. Two options worth knowing:

- **the optional `<fifo_depth>`** inserts a FIFO on that connection — a cheap knob for the
  bandwidth experiment (§7.3) and the first thing to try if a stream stalls in hw_emu;
- **v++ auto-inserts a width converter** if producer and consumer widths differ, so a
  future `PC_WIDTH` 256→512 change (§1.3(b)) does not have to be matched on both sides at
  once.

**This file is the entire HBM-vs-DDR experiment.** Swapping `HBM[n]` for `DDR[0]`
changes the memory system without touching a line of RTL.

### 3.5 Kernel interface requirements (UG1393) — the checklist to package against

Applies to the **movers**; the free-running compute kernel is exempt from the AXI4-Lite and
`m_axi` rows because it has neither.

- **exactly one** AXI4-Lite slave, named `s_axi_control` (case-sensitive)
- at least one clock, packaged as a bus interface; active-low reset optional but
  recommended, tied to the clock via `ASSOCIATED_RESET`
- an interrupt output, if present, must be named exactly `interrupt`
- `m_axi`: **64-bit addresses**, no Wrap or Fixed bursts, `AxSIZE` = bus width
- `axis`: one-way only, never bidirectional; see the AXIS rules in §0.2d
- **every port must be associated with an interface at packaging time**, or packaging errors
- register map: `0x00` CTRL (bit0 `ap_start`, bit1 `ap_done`, bit2 `ap_idle`), `0x04` GIER,
  `0x08` IP_IER, `0x0C` IP_ISR, arguments from `0x10`

For the §3.3 fallback only: `kernel.xml` `<port>` takes `name`, `mode` (`slave`/`master`),
`range`, `dataWidth`, `portType` — `addressable` for AXI4/AXI4-Lite, **`stream` for
AXI4-Stream, which omits `range` and `base` entirely**. Pointer args use
`addressQualifier="1"`.

---

## Phase 4 — Host application

Use **OpenCL via `xcl2.hpp`** (§0.2e) — the API every 2021.1 RTL-kernel example uses, and
the better-trodden path for `xrt.ini` profiling on this release. Vendor `xcl2.cpp`/`xcl2.hpp`
from `Vitis_Accel_Examples/common/includes/xcl2` (Apache-2.0).

The host drives **14 CUs** (dense) or **17** (sparse): 10/13 `krnl_mm2s` and 4 `krnl_s2mm`.
The compute kernel is free-running, has no control interface, and is **never referenced from
the host at all** — `stream_connect` ports take no `setArg`.

```cpp
// out-of-order queue: every CU runs concurrently, exactly as in the reference
OCL_CHECK(err, cl::CommandQueue q(context, device,
          CL_QUEUE_PROFILING_ENABLE | CL_QUEUE_OUT_OF_ORDER_EXEC_MODE_ENABLE, &err));

// one cl::Kernel per COMPUTE UNIT, addressed by the nk= name in <>
OCL_CHECK(err, cl::Kernel k_w0(program, "krnl_mm2s:{mm2s_w0}", &err));
// ... mm2s_w1..w7, mm2s_a0, mm2s_a1, s2mm_c0..c3

// buffers: XRT places each one in the bank sp= assigned, from the kernel it is bound to
OCL_CHECK(err, cl::Buffer buf_w0(context, CL_MEM_USE_HOST_PTR | CL_MEM_READ_ONLY,
                                 bytes, host_w0.data(), &err));
k_w0.setArg(0, buf_w0);
k_w0.setArg(2, n_weight_beats);     // arg 1 is the stream -- NEVER set it

q.enqueueMigrateMemObjects({buf_w0, ...}, 0 /* to device */);

for (auto& k : all_cus) OCL_CHECK(err, err = q.enqueueTask(k));
q.finish();                          // all CUs done = calculation complete

q.enqueueMigrateMemObjects({buf_c0, ...}, CL_MIGRATE_MEM_OBJECT_HOST);
q.finish();
```

Three things that bite:

- **`"krnl_mm2s:{mm2s_w0}"`** is how OpenCL addresses one specific CU of a replicated
  kernel. Plain `"krnl_mm2s"` grabs whichever CU the runtime feels like — with 10 of them
  bound to 10 different HBM PCs, that is a silent wrong-answer bug, not an error.
- **Never `setArg` the stream argument** (arg 1). Kernel-to-kernel stream ports take no
  argument; setting one is an error.
- **Device selection.** `xcl::get_xil_devices()` returns *all three* Alveo cards in this
  machine. Filter by name/BDF and assert you got the U280 — index 0 is most likely the U200
  (§0.1). In `hw_emu` there is only the emulated device, so the filter must tolerate that.

**Launch order — corrected 2026-08-10.** An earlier draft of this plan said the sink *must*
start first. That is **not** what the reference does: `rtl_streaming_free_running_k2k`
enqueues `krnl_mm2s` **first** and `krnl_s2mm` second, on a queue created with
`CL_QUEUE_OUT_OF_ORDER_EXEC_MODE_ENABLE` — so both are simply in flight together and the
order carries no meaning. **The out-of-order flag is what makes that safe**: on a default
in-order queue the second `enqueueTask` would not start until the first completed, and a
producer waiting on a consumer that cannot start is a guaranteed deadlock. Do not drop it.

Nor is a late sink a hazard here: if the output FIFOs fill, `prog_full` closes the compute
block's global gate, which stops it consuming inputs, which backpressures the read stage
through `tready`. Beats stall; nothing is dropped. That gate is exactly what it is for.

So: launch both before waiting on either, and treat sink-first as a free precaution rather
than a requirement. If the design ever *does* deadlock, launch order is not the cause — look
at a dangling `stream_connect` first (§S18).

**Data preparation:** extend the Python generators to emit per-PC binary images
alongside the hex files. The host then loads images and compares results against
`golden.txt` — the same golden model, unchanged, from RTL simulation all the way to
hardware. That continuity is worth protecting.

---

## Phase 5 — Hardware emulation

```bash
v++ -t hw_emu --platform $PLATFORM --config connectivity.cfg -l -o gemv_sparse.xclbin \
    krnl_mm2s.xo krnl_gemv_sparse.xo krnl_s2mm.xo
```

**Use a tiny case.** `hw_emu` simulates the full AXI stack; the 1024×1024 case would run
for hours. Use `--nwin 1 --nlaps 1` (16 beats dense / 2 beats sparse at 2:16) — enough to
prove the plumbing.

What hw_emu catches that nothing before it can: AXI protocol errors, buffer/bank
mis-assignment, control-register mistakes, arguments in the wrong order, and TLAST
arriving at the wrong beat through the real interconnect.

Deliverable: one output beat, bit-exact against `golden.txt`, in hw_emu.

---

## Phase 6 — Hardware bring-up

```bash
v++ -t hw --platform $PLATFORM --config connectivity.cfg -l -o gemv_sparse.xclbin \
    krnl_mm2s.xo krnl_gemv_sparse.xo krnl_s2mm.xo
```

Expect **hours** per build. Budget accordingly and batch them.

Bring-up order — do not skip ahead:

1. smallest case, verify bit-exact against `golden.txt`
2. full 1024-element vector, 1 lap
3. full 1024×1024 (16 laps)
4. sparse: all four sparsity modes, then the mixed runtime-reconfiguration case

> **Stimulus reality check (2026-08-16).** The case actually verified on the server is
> **2048 rows × V=64**, not 1024×1024: `weights.hex` is 1024 beats × 128 elements =
> 131 072 weight elements ÷ 2048 rows = 64 elements per row; `activations.hex` is 2 beats
> (V/32); weight beats per lap = V/2 = 32, so 1024/32 = **32 laps** → 32 output beats ×
> 64 rows = 2048 rows. Every number closes exactly, which is why the S3 compare passed.
>
> Two consequences: the "1024 beats" in the file is a *beat* count, not a row count; and
> **§7.1's table is quoted at V=1024**, so the measurement campaign needs a larger-V
> stimulus generated before Phase 6 step 2. Not a blocker for Stage C–E — the small case is
> exactly what hw_emu wants — but it must exist before any throughput number is reported.

Useful commands:

```bash
xbutil validate --device 0000:af:00.1
xbutil examine  --device 0000:af:00.1 --report all
```

**This is where the clock frequency decision gets made** (deferred by design). The
`[clock] freqHz` in the link config sets the kernel clock; if timing fails at 450 MHz in
the full system, lower it. Both architectures must ultimately be reported at whatever
they each achieve — and note that dense may sustain a higher clock than sparse, which is
itself a result.

---

## Phase 7 — The experiment sweeps

Run each as a separate `.xclbin`. Change **one variable at a time**.

### 7.1 Architecture (the primary comparison)

| Config | Weight beats/lap (V=1024) | Expected speedup vs dense |
|---|---|---|
| dense | 512 | 1× |
| sparse 2:4 | 256 | 2× |
| sparse 2:8 | 128 | 4× |
| sparse 2:16 | 64 | 8× |
| sparse 2:32 | 32 | 16× |

Also measure **bytes moved**: sparse beats carry indices (2048 + 640 b vs dense's 2048),
a 31 % per-beat overhead. Net data moved by sparse relative to dense = `(2/M) × 21/16`
→ 66 % / 33 % / 16 % / 8 %. The 2:4 asymmetry (2× faster but only 34 % less data) is the
interesting story: index overhead eats the bandwidth win at low sparsity.

### 7.2 Memory type — HBM vs DDR

Changed entirely in `connectivity.cfg`.

The catch: the U280 has **2 DDR banks** but you need 14–17 ports. Options:

- **(a)** Map several `m_axi` bundles to the same `DDR[0]` — Vitis inserts an
  interconnect. Simple; serialises the traffic, which is arguably the honest result.
- **(b)** Rewrite the engine with 1–2 wide 512-bit ports that internally fan out to the
  17 streams. More faithful to how a DDR design would really be built.

Start with (a) since it needs no engine change. Since GEMV is memory-bound
(~0.38 MAC/byte, no weight reuse), expect DDR to be dramatically slower — that *is* the
result, and it justifies the platform choice.

### 7.3 Bus bandwidth

- `PC_WIDTH` 256 → 512 (pair HBM PCs, or match DDR's native width)
- HLS burst-length and outstanding-transaction tuning
- number of PCs actually used

### 7.4 Core count — ❌ **OUT OF SCOPE**

8 cores is the fixed reference configuration for the dense-vs-sparse comparison (see
Phase 1). Recorded for completeness: 16-core sparse would consume **all 32 HBM PCs** and sit
exactly on the 32-AXIS-interfaces-per-kernel spec limit, with no margin.

---

## Phase 8 — Measurements and write-up

Collect per configuration:

| Category | Metric | Source |
|---|---|---|
| Performance | kernel wall-clock, throughput (rows/s), cycles/lap | XRT profiling, `xrt.ini` |
| Bandwidth | bytes moved, achieved GB/s, % of peak | calculated + profile |
| Area | LUT / FF / DSP / BRAM | `report_utilization` post-impl |
| Frequency | achieved kernel clock, WNS | `report_timing_summary` |
| Power | total, per-rail | `report_power`, `xbutil examine` |
| Correctness | bit-exact vs `golden.txt` | host-side compare |

Enable profiling:

```ini
# xrt.ini
[Debug]
profile=true
timeline_trace=true
data_transfer_trace=coarse
```

Results already banked (module-level OOC, 450 MHz constraint): dense is **−29.1 % LUTs**,
**−8192 F7 muxes** (the exact gather fingerprint: 128 gathers × 16 bits × 4 MUXF7),
**identical 512 DSPs**, and **meets timing where sparse fails** (+0.132 vs −0.363 ns).

---

## Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| ~~Vitis/platform version mismatch~~ | ~~blocks everything~~ | **RESOLVED 2026-08-06** — see §0.1; platform, shell and card all verified and matched |
| Wrong Alveo card selected (3 in the machine) | looks like a shell mismatch; wastes a build cycle | filter `xcl::get_xil_devices()` by name/BDF `0000:af:00.1` and assert; never take index 0 |
| **Bundled AXIS ports not recognised by Vitis** | cannot package the compute kernel at all | splitter wrapper, Phase 0.2b / Phase 1 — do it first |
| **No `TKEEP` anywhere in the RTL; index streams have no `TLAST`** | violates the k2k AXIS rules (§0.2d); packaging or hw_emu fails | wrapper drives `tkeep` all-1s and supplies index `tlast` — zero-logic, Phase 0.2b |
| ~~RTL Kernel Wizard property names differ in 2021.1~~ | — | **RESOLVED 2026-08-10** — wizard verified in the 2021.1 GUI, §3.2 |
| ~~Wizard may cap the stream count below 17~~ | — | **RESOLVED 2026-08-10** — max is 32; 14 and 17 both fit |
| `Has reset` defaults to 0 in the wizard | kernel built with no `ap_rst_n`; compute block never leaves reset | set **Has reset = 1** on the General Settings page (§3.2) |
| Stream width defaults to 64 B (512 b) | silent width mismatch vs our 256-bit PCs (v++ would insert a converter and hide it) | set **Width = 32 bytes** on all streams (§3.2) |
| 17 CUs + 17 stream connections exceed link limits | forces a mover redesign | prototype the link with stub kernels first; the reference only proves 2 streams and 3 CUs |
| Wrong CU picked for a replicated kernel | **silent wrong answer** — a weight PC feeds an activation stream | always address CUs as `"krnl_mm2s:{mm2s_w0}"`, never bare `"krnl_mm2s"` (§4) |
| In-order command queue with replicated movers | guaranteed deadlock | keep `CL_QUEUE_OUT_OF_ORDER_EXEC_MODE_ENABLE` (§4) |
| hw builds take hours | slow iteration | batch overnight; get hw_emu right first |
| DDR port mapping | 17 ports → 2 banks | option (a) first, no engine change |
| Host data packing bugs | silent wrong answers | reuse the Python generator; compare vs `golden.txt` at every stage |

---

## Suggested order

1. **Phase 0** — versions, platform, topology decision *(hours)*
3. **Phase 2** — HLS engine + standalone verification against existing hex *(days)*
4. **Phase 3** — package the three kernels (dense first: fewer ports, meets timing) *(days)*
5. **Phase 5** — hw_emu, tiny case, bit-exact *(days — the real debugging happens here)*
6. **Phase 6** — first hardware run, dense, smallest case *(hours + build time)*
7. Repeat 3–6 for **sparse**
8. **Phase 7** — sweeps, one variable at a time
9. **Phase 8** — collect, tabulate, write

**Do dense first throughout.** It has fewer ports (14 vs 17), no index path, no sparsity
control, and it already meets timing — so when something breaks in the plumbing, the
cause is unambiguous. Bring sparse up only once the dense path works end to end.

---
name: vitis-phase-prompt
description: "SUPERSEDED 2026-08-27 bootstrap prompt for the Vitis phase — describes a starting state that no longer exists; kept only as a record of how the phase was framed"
metadata:
  node_type: memory
  type: reference
  modified: 2026-09-03T00:00:00.000Z
---

> ⚠️ **SUPERSEDED 2026-08-27 — this bootstrap prompt describes a starting state that no
> longer exists.** It says "Phase 0.1 is done; everything after it is open". In fact the
> whole integration is finished: BOTH architectures run bit-exact on the physical U280,
> the sparsity ladder and runtime reconfiguration pass on hardware, and throughput AND
> power are measured for all five configurations at 300 MHz, with power and clock-scaling
> repeated at 325 MHz. Fmax: dense 350, sparse 325.
>
> **For a fresh chat, read `INTEGRATION_STEPS.md` first — that is the live status.**
> `INTEGRATION_PLAN.md` remains valid as the *spec* (design decisions, conformance audits,
> config formats) but not as a to-do list. Keep this file only as a record of how the phase
> was originally framed.

# Prompt for a new Claude Code chat — Vitis / system-integration phase

Copy everything below the line into a fresh chat opened in `c:\Koulas\ECE\Thesis\Code`.

---

I'm starting the **Vitis / system-integration phase** of my FPGA thesis. The RTL side is
finished and verified; this chat is about getting both architectures running on a real
Alveo U280 and measuring them.

## Read these first, in this order

1. `CLAUDE.md` — project rules and the full architecture reference (read the whole thing;
   the C Block / C Core / FIFO / top-module descriptions matter)
2. `INTEGRATION_PLAN.md` — the 8-phase plan for this exact work. **This is the spec for
   this chat.** Phase 0.1 is already done and marked ✅; everything after it is open.
3. These memory files (siblings of this one in `.claude/memory/`):
   - `gemv-server-2021-port` — the server environment, verified 2026-08-06
   - `gemv-impl-results-sparse-vs-dense` — the milestone results and where we are
   - `reference-vitis-rtl-kernel-workflow` — AMD RTL-kernel packaging reference I had you
     extract from the tutorials/examples (package_xo, kernel.xml, stream_connect syntax)
   - `project-gemv4-cosim` — the co-sim flow I'll reuse to verify the HLS engine

## Where things stand

**Done:** Two GEMV accelerators, both RTL-complete and verified bit-exact against a Python
golden model (behavioural *and* post-implementation functional sim, stressed and
unstressed, full 1024×1024):

- **Sparse `two2N`** — runtime-selectable 2:4 / 2:8 / 2:16 / 2:32, source in
  `GEMV_4.0_Source/`
- **Dense `dense_gemv`** — the comparison baseline, source in `GEMV_Dense_Source/`

Both are implemented OOC on `xcu280-fsvh2892-2L-e`. Headline result: dense is −29.1 % LUTs,
−8192 F7 muxes (the exact gather fingerprint), identical 512 DSPs, and meets 450 MHz where
sparse fails (+0.132 vs −0.363 ns).

**Verified environment on the university server `coroni`** (all measured, not assumed):

| Item | Value |
|---|---|
| Vitis / Vivado | 2021.1 |
| XRT | 2.13.0 (branch 2022.1) |
| Platform | `xilinx_u280_xdma_201920_3` — exposes **both HBM and DDR** |
| Card | U280 at BDF **`0000:af:00.1`**, shell matches the platform, Device Ready |
| Host | Ubuntu 18.04.6, 40 cores, 192 GB RAM, `python3` = 3.6.9 |
| GUI | X2Go — full GUI works fine for Vivado / Vitis / `vitis_hls` |

Two traps already identified, both recorded in `INTEGRATION_PLAN.md` §0.1:
- **Three Alveo cards are in this machine** (U200, U50, U280). Always select by BDF;
  `xrt::device(0)` returns the wrong card.
- **Two U280 platforms are installed and one is poison** — the `gen3x16_..._202211_1` one
  is 2022.2 and breaks `platforminfo -l` entirely with "failed to parse the XPFM file".

## What I want to do in this chat

Work through `INTEGRATION_PLAN.md` from §0.2b onward. In order:

1. **§0.2b — the AXIS splitter wrapper (BLOCKER, do this first).** The compute tops'
   stream ports are *bundled*: `s_axis_w_tdata` is one 2048-bit port with an 8-bit
   `tvalid`/`tready`/`tlast` vector — 8 logical AXIS channels in one declaration. Vitis
   needs 13 separate, properly named AXIS interfaces so the IP packager can infer them.
   This must be **pure mechanical re-wiring, no logic changes** — the verified RTL is never
   touched. Write `two2N_axis.vhd` / `dense_gemv_axis.vhd` wrappers.
2. **§1 — parameterisation audit**, so the core-count sweep is actually possible later.
3. **§2 — the HLS read/write engine**, verified standalone against the existing `.hex`
   stimulus files (this reuses the whole verification chain I already built).
4. **§3 — kernel packaging**, then §5 hw_emu, §6 hardware bring-up, §7 sweeps.

**Do dense first at every step** — 14 ports vs 17, no index path, and it already meets
timing, so when the plumbing breaks the cause is unambiguous.

## How I want to work

- **Rung by rung.** Give me one concrete step, I run it on the server and report back, you
  check the result before we move on. This worked well for the RTL verification and I want
  to keep it. Don't hand me five phases at once.
- **You never run Vivado, Vitis, or `v++`.** I run every synthesis, implementation,
  simulation, build and hardware run myself and paste you the output. You may run Python.
- **You never push, pull, or rebase git.** You can read, edit, and make local commits; I
  handle everything that touches the remote.
- Flag your assumptions. If something in the plan turns out to be wrong when it meets real
  tools, say so rather than working around it silently.

Start by reading the files above and confirming you understand the current state — then
give me your first concrete step on the splitter wrapper.

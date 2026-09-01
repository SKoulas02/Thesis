"""hex <-> per-pseudo-channel binary images, for the Vitis host and the HLS movers.

SPARSE (2:M) version. Python 3.6 compatible (the server has 3.6.9). Stdlib only.

WHY THIS EXISTS (INTEGRATION_PLAN.md 2.3, INTEGRATION_STEPS.md S23):
The RTL was verified against weights.hex / indices.hex / activations.hex /
golden.txt. The mover kernels' entire job is to reproduce those same beats out of
HBM, so if we feed them binary images of those exact files and get the same
streams back, the whole existing verification chain carries over untouched.
Nothing about the golden model changes from simulation to hardware.

Identical to the dense packer plus ONE stream: indices.hex -> 3 PCs. The mover
kernels are unchanged -- an index PC is just another 256-bit stream to them.

PACKING -- little-endian throughout, matching the design and AXI byte order:
  weights.hex     one 2048-bit beat per line, 512 hex chars, MSB first.
                  PC i = bits[256i+255 : 256i], so PC 0 is the LAST 64 hex
                  chars of the line and PC 7 is the FIRST 64.
  indices.hex     one 768-bit beat per line, 192 hex chars -> 3 PCs.
                  640 bits are index data (8 cores x 80 bits); the rest is
                  padding, and the 2-bit SPARSITY code lives at [641:640].
  activations.hex one 512-bit beat per line, 128 hex chars -> 2 PCs.
  output          1024-bit beat = 4 PCs; lane r = bits[16r+15 : 16r], so PC j
                  carries lanes 16j .. 16j+15.
  Each PC beat is 32 bytes written LSB-first -- i.e. exactly the bytes an
  ap_uint<256> occupies in the host buffer that krnl_mm2s reads.

THE SPARSITY CODE IS DATA, NOT A SIGNAL. Bits [641:640] of the joined index
word land in index PC2 at bits [129:128] -- see sparsity_of_beat() below. No
mover, no link line and no host argument knows it exists; the engine re-samples
it at every window load, which is what makes runtime reconfiguration possible.
`pack` prints the codes it finds so a mispacked run is caught before the FPGA is
ever programmed.

USAGE
  python3 hex_to_bin.py pack      # weights/indices/activations .hex -> bin/*.bin
  python3 hex_to_bin.py unpack    # bin/c_pc*.bin -> output_from_bin.txt
  python3 hex_to_bin.py verify    # pack, read back, compare to the .hex (self-test)
"""

from pathlib import Path
import sys

HERE = Path(__file__).parent
BIN = HERE / "bin"

PC_BITS = 256
PC_BYTES = PC_BITS // 8          # 32
PC_HEX = PC_BITS // 4            # 64

W_PCS = 8                        # weights     : 8 x 256 = 2048 b
IND_PCS = 3                      # indices     : 3 x 256 =  768 b (640 used)
A_PCS = 2                        # activations : 2 x 256 =  512 b
C_PCS = 4                        # output      : 4 x 256 = 1024 b
LANES = 64                       # 8 cores x 8 blocks

SPARSITY_BIT = 640               # code occupies [641:640] of the joined index word
SP_NAME = {0: "2:4", 1: "2:8", 2: "2:16", 3: "2:32"}


def read_hex(path):
    """One big integer per line, plus the expected hex width."""
    lines = [ln.strip() for ln in path.open() if ln.strip()]
    if not lines:
        raise SystemExit("empty file: {}".format(path))
    width = len(lines[0])
    for i, ln in enumerate(lines):
        if len(ln) != width:
            raise SystemExit("{}: line {} is {} chars, expected {}".format(
                path.name, i + 1, len(ln), width))
    return [int(ln, 16) for ln in lines], width


def split_pcs(value, npcs):
    """Little-endian: PC 0 holds the least significant 256 bits."""
    mask = (1 << PC_BITS) - 1
    return [(value >> (PC_BITS * i)) & mask for i in range(npcs)]


def join_pcs(pcs):
    v = 0
    for i, p in enumerate(pcs):
        v |= p << (PC_BITS * i)
    return v


def sparsity_of_beat(index_beat):
    """The 2-bit code carried by one joined 768-bit index beat."""
    return (index_beat >> SPARSITY_BIT) & 0x3


def pack_file(hexpath, npcs, prefix):
    beats, width = read_hex(hexpath)
    if width != npcs * PC_HEX:
        raise SystemExit("{}: {} hex chars, expected {} for {} PCs".format(
            hexpath.name, width, npcs * PC_HEX, npcs))
    streams = [bytearray() for _ in range(npcs)]
    for v in beats:
        for i, pc in enumerate(split_pcs(v, npcs)):
            streams[i] += pc.to_bytes(PC_BYTES, "little")
    BIN.mkdir(exist_ok=True)
    for i, s in enumerate(streams):
        out = BIN / "{}{}.bin".format(prefix, i)
        out.write_bytes(bytes(s))
    print("  {:<18} {} beats x {} PCs -> {}0..{}.bin  ({} bytes each)".format(
        hexpath.name, len(beats), npcs, prefix, npcs - 1, len(streams[0])))
    return len(beats), beats


def report_sparsity(index_beats):
    """Print the sparsity schedule the packed images will actually deliver.

    Cheap, and it catches two real mistakes before a single second of FPGA time
    is spent: a stimulus set generated at the wrong sparsity, and a packing bug
    that puts the code somewhere other than [641:640] (which would silently run
    the whole calculation at 2:4, since 00 is what an empty field reads as).
    """
    codes = [sparsity_of_beat(v) for v in index_beats]
    runs = []
    for c in codes:
        if runs and runs[-1][0] == c:
            runs[-1][1] += 1
        else:
            runs.append([c, 1])
    print("")
    print("  sparsity schedule (from index PC2 bits [129:128] = joined [641:640]):")
    for code, n in runs:
        print("    {:02b} = {:<5} for {} beat(s)".format(code, SP_NAME[code], n))
    if len(runs) > 1:
        print("    -> {} changes: this is a RUNTIME-RECONFIGURATION run".format(len(runs) - 1))


def cmd_pack():
    print("pack:")
    nw, _ = pack_file(HERE / "weights.hex", W_PCS, "weights_pc")
    ni, ibeats = pack_file(HERE / "indices.hex", IND_PCS, "ind_pc")
    na, _ = pack_file(HERE / "activations.hex", A_PCS, "act_pc")

    if ni != nw:
        raise SystemExit(
            "indices.hex has {} beats but weights.hex has {} -- they are joined "
            "beat-for-beat by the weights FIFO and MUST match.".format(ni, nw))

    report_sparsity(ibeats)
    print("")
    print("  n_weight_beats = {}   (krnl_mm2s arg for each weight AND index CU)".format(nw))
    print("  n_act_beats    = {}   (krnl_mm2s arg for each activation CU)".format(na))
    print("  images in {}/".format(BIN))


def cmd_unpack():
    """bin/c_pc0..3.bin -> output_from_bin.txt, in golden.txt row order."""
    pcs = []
    for j in range(C_PCS):
        p = BIN / "c_pc{}.bin".format(j)
        if not p.exists():
            raise SystemExit("missing {} -- run the kernel first".format(p))
        pcs.append(p.read_bytes())
    n = len(pcs[0])
    for j, b in enumerate(pcs):
        if len(b) != n:
            raise SystemExit("c_pc{}.bin is {} bytes, c_pc0.bin is {} -- PC desync".format(
                j, len(b), n))
    if n % PC_BYTES:
        raise SystemExit("c_pc0.bin is {} bytes, not a multiple of {}".format(n, PC_BYTES))
    nbeats = n // PC_BYTES

    out = HERE / "output_from_bin.txt"
    with out.open("w") as f:
        for k in range(nbeats):
            words = [int.from_bytes(pcs[j][k * PC_BYTES:(k + 1) * PC_BYTES], "little")
                     for j in range(C_PCS)]
            beat = join_pcs(words)
            for r in range(LANES):
                f.write("{:04X}\n".format((beat >> (16 * r)) & 0xFFFF))
    print("unpack: {} beat(s) x {} lanes -> {}".format(nbeats, LANES, out.name))
    print("  compare with: python3 compare_gemv4_py36.py   (after copying it over output.txt)")


def cmd_verify():
    """Round-trip self-test: the images must rebuild the .hex files exactly."""
    cmd_pack()
    print("")
    print("verify:")
    ok = True
    for name, npcs, prefix in (("weights.hex", W_PCS, "weights_pc"),
                               ("indices.hex", IND_PCS, "ind_pc"),
                               ("activations.hex", A_PCS, "act_pc")):
        beats, width = read_hex(HERE / name)
        streams = [(BIN / "{}{}.bin".format(prefix, i)).read_bytes() for i in range(npcs)]
        bad = 0
        for k, want in enumerate(beats):
            words = [int.from_bytes(streams[i][k * PC_BYTES:(k + 1) * PC_BYTES], "little")
                     for i in range(npcs)]
            if join_pcs(words) != want:
                bad += 1
                if bad <= 3:
                    print("    beat {} mismatch".format(k))
        if bad:
            ok = False
            print("  {:<18} FAIL  {} / {} beats differ".format(name, bad, len(beats)))
        else:
            print("  {:<18} PASS  {} beats round-trip exactly".format(name, len(beats)))

    # The sparsity code must survive the split into PC images. It sits in PC2,
    # so a wrong PC order or endianness would move or lose it while the plain
    # round-trip above still passed on the joined value.
    ibeats, _ = read_hex(HERE / "indices.hex")
    ind2 = (BIN / "ind_pc2.bin").read_bytes()
    bad = 0
    for k, want in enumerate(ibeats):
        pc2 = int.from_bytes(ind2[k * PC_BYTES:(k + 1) * PC_BYTES], "little")
        if ((pc2 >> (SPARSITY_BIT - 2 * PC_BITS)) & 0x3) != sparsity_of_beat(want):
            bad += 1
    if bad:
        ok = False
        print("  {:<18} FAIL  {} beat(s) lost the code in the PC split".format(
            "sparsity in PC2", bad))
    else:
        print("  {:<18} PASS  code intact at ind_pc2[129:128] on all {} beats".format(
            "sparsity in PC2", len(ibeats)))

    print("")
    print("=== {} ===".format("PASS" if ok else "FAIL"))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "verify"
    if cmd == "pack":
        cmd_pack()
    elif cmd == "unpack":
        cmd_unpack()
    elif cmd == "verify":
        cmd_verify()
    else:
        raise SystemExit(__doc__)

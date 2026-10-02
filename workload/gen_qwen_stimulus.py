"""Realistic random stimulus for the Qwen MoE step-1 test. Python 3.6, stdlib only.

A NEW FILE (2026-09-29), after GEMV_4.0_Source/Emulation/gen_timing_stimulus.py (untouched).
It writes the SAME per-PC images in the SAME layout -- the same files (weights_pc<i>.bin,
ind_pc<i>.bin, act_pc<i>.bin), the same beat counts, the sparsity code in every index beat
at the same bit, the same zero padding above it -- and differs ONLY in the values:

  weights      bf16 of a dense N(0, WEIGHT_SIGMA) matrix pruned to 2:M by magnitude, i.e.
               the 2 largest of every M: the distribution of a KEPT weight is the order-
               statistic mixture (max + second max of M |N(0, sigma)|)/2, a different one per
               sparsity. Sign random; the exponent is drawn from that distribution to a pair
               of binades (a 128-level quantile table); the mantissa and the lowest exponent
               bit uniform. Never zero, denormal, Inf or NaN.
  indices      every pair VALID FOR ITS GROUP: at cycle c of a window's freeze (32/M cycles)
               a block takes the two non-zeros of group c, so both indices lie in
               [c*M, c*M + M), distinct, ascending -- what a real 2:M matrix gives the
               gather. Drawn per beat from a per-layer pool of random windows.
  activations  N(0, 1) rounded to bf16 (RNE): a post-norm hidden state.

WEIGHT SIGMA (2026-10-01, the user's decision): the dense weights before pruning are N(0, 1) by
default -- plain, unit-scale random numbers, the same scale as the vector. That is big enough for
the engine's accumulator, which is FIXED POINT with its LSB at 2^-7 (Accumulator.xci
C_Accum_Lsb = -7): the card's result then differs from exact arithmetic only by bf16's own
rounding (median 0.26% of the rms output, p95 0.90%, max 1.5%; the largest summand ~26, the
largest running sum ~140, far inside the accumulator's range). A trained model's weights are
~0.02 (set_weight_sigma / --weight-sigma 0.02): with them the 2^-7 LSB costs ~4% median error --
the reason to widen the accumulator's internal range later (a separate experiment).

The values are random from a seed (random.Random), so a run is reproducible from its seed;
run_qwen.py draws a new seed per run from os.urandom and records it. Every layer gets new
weights, new indices and a new vector.

WHY NOT gen_timing_stimulus.py. Its fill is one constant 32-byte tile: timing does not depend
on the values, but POWER does (toggle rates), and the professor's test asks for realistic
data. The run LENGTH is still set only by the beat counts and the sparsity codes, exactly as
there.

Speed: a layer is ~13 MB of images per engine type, 40 layers per build. Weights are random
bytes with every high byte mapped through a 256-entry table (bytes.translate, C speed);
index beats are joined from a pool of pre-built windows. No per-element Python work.

    python3 gen_qwen_stimulus.py --cores 4 --blocks 4 --nwin 64 --mix 00:3,01:3,10:2,11:1 --out bin
    python3 gen_qwen_stimulus.py --cores 4 --blocks 4 --nwin 64 --sparsity 00 --nlaps 2
"""

import argparse
import math
import os
import random
import struct

PC_BITS = 256
PC_BYTES = PC_BITS // 8            # 32 -- one 256-bit pseudo-channel beat
A_PCS = 2                          # the 32-element window is 512 bits at every shape
IND_BITS = 5
WIN_ELEMS = 32
HBM_PC_BYTES = 256 * 1024 * 1024   # one U280 pseudo-channel
SP_MAP = {"00": 4, "01": 8, "10": 16, "11": 32}
SPARSITIES = ["00", "01", "10", "11"]

WEIGHT_SIGMA = 1.0                 # dense weights before pruning: standard normal (0.02 = a
                                   # trained model's scale, too small for the 2^-7 accumulator)
ACT_SIGMA = 1.0                    # the token vector after RMSNorm


def ceildiv(a, b):
    return -(-a // b)


def shape(cores, blocks):
    """Every width that depends on CORES x BLOCKS -- gen_timing_stimulus.shape(), verbatim."""
    lanes = cores * blocks
    ind_bits = 10 * lanes
    return dict(cores=cores, blocks=blocks, lanes=lanes,
                w_pcs=ceildiv(lanes * 32, PC_BITS),
                ind_pcs=ceildiv(ind_bits + 2, PC_BITS),   # +2: the code rides ABOVE
                w_per_core=2 * blocks,
                ind_per_core_bits=10 * blocks,
                sparsity_bit=ind_bits,
                sp_pc=ind_bits // PC_BITS,
                sp_byte=(ind_bits % PC_BITS) // 8)


# ---- bf16 --------------------------------------------------------------------------------
def bf16_rne_raw(x):
    """float -> bf16 raw bits, round to nearest even (as gemv4_cosim_gen.py)."""
    b = struct.unpack(">I", struct.pack(">f", float(x)))[0]
    b = (b + 0x7FFF + ((b >> 16) & 1)) & 0xFFFFFFFF
    return (b >> 16) & 0xFFFF


def bf16_float(raw):
    return struct.unpack(">f", struct.pack(">I", (raw & 0xFFFF) << 16))[0]


# ---- random bytes from a seeded generator ---------------------------------------------------
class Rng(random.Random):
    def bytes(self, n):
        return self.getrandbits(8 * n).to_bytes(n, "little") if n else b""


# ---- weights: the high-byte table of one sparsity -----------------------------------------
def _kept_cdf(t, m, sigma):
    """CDF of |w| for a weight KEPT by 2:m magnitude pruning of iid N(0, sigma): half the
    kept weights are the largest of their group, half the second largest."""
    f = math.erf(t / (sigma * math.sqrt(2.0)))
    first = f ** m
    second = f ** m + m * f ** (m - 1) * (1.0 - f)
    return 0.5 * (first + second)


def weight_hi_table(code, sigma=WEIGHT_SIGMA):
    """256 entries: random byte -> the HIGH byte of a bf16 weight (sign + exponent bits 7..1).
    Bit 7 of the random byte is the sign; its low 7 bits pick one of 128 quantiles of the
    kept-magnitude distribution, whose binade pair becomes the exponent (the low byte, random,
    supplies exponent bit 0 and the mantissa)."""
    if not sigma > 0:
        raise SystemExit("the weight sigma must be positive, got %r" % sigma)
    m = SP_MAP[code]
    out = bytearray(256)
    for r in range(256):
        q = ((r & 0x7F) + 0.5) / 128.0
        lo, hi = 0.0, 20.0 * sigma
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            if _kept_cdf(mid, m, sigma) < q:
                lo = mid
            else:
                hi = mid
        t = 0.5 * (lo + hi)
        e = int(math.floor(math.log(t, 2))) + 127
        if not 2 <= e <= 250:
            raise SystemExit("weight table: exponent %d out of the normal range (sigma %g)"
                             % (e, sigma))
        out[r] = ((r >> 7) << 7) | (e >> 1)
    return bytes(out)


_HI_TABLES = {}
_SIGMA = [WEIGHT_SIGMA]            # the weight sigma of this process (set_weight_sigma)


def set_weight_sigma(s):
    """The standard deviation of the dense weights (before pruning) generated from now on."""
    s = float(s)
    if not s > 0:
        raise SystemExit("the weight sigma must be positive, got %r" % s)
    if s != _SIGMA[0]:
        _HI_TABLES.clear()
        _SIGMA[0] = s


def weight_sigma():
    return _SIGMA[0]


def hi_table(code):
    if code not in _HI_TABLES:
        _HI_TABLES[code] = weight_hi_table(code, sigma=_SIGMA[0])
    return _HI_TABLES[code]


def weight_bytes(code, nbytes, rng):
    """nbytes of little-endian bf16 weights of one sparsity (nbytes even)."""
    buf = bytearray(rng.bytes(nbytes))
    buf[1::2] = bytes(buf[1::2]).translate(hi_table(code))
    return bytes(buf)


# ---- indices ----------------------------------------------------------------------------
def _pairs(m):
    return [(a, b) for a in range(m) for b in range(a + 1, m)]


def index_window(S, code, rng):
    """One window of index beats (freeze = 32/M of them) -> [bytes per index PC], each the
    freeze beats of that PC back to back. Beat c: every lane's pair from group c."""
    m = SP_MAP[code]
    freeze = 32 // m
    pairs = _pairs(m)
    npairs = len(pairs)
    per_pc = [[] for _ in range(S["ind_pcs"])]
    for c in range(freeze):
        base = c * m
        ibus = 0
        for lane in range(S["lanes"]):
            a, b = pairs[int(rng.random() * npairs)]
            ibus |= ((base + a) | ((base + b) << IND_BITS)) << (2 * IND_BITS * lane)
        ibus |= (int(code, 2) & 0x3) << S["sparsity_bit"]
        raw = ibus.to_bytes(S["ind_pcs"] * PC_BYTES, "little")
        for i in range(S["ind_pcs"]):
            per_pc[i].append(raw[i * PC_BYTES:(i + 1) * PC_BYTES])
    return [b"".join(x) for x in per_pc]


def pool_size(S):
    """Windows per pool: 256 for up to 32 lanes, fewer for wider engines (the cost of a pool
    is windows x lanes); always a power of two so a random byte picks one."""
    return 256 if S["lanes"] <= 32 else (128 if S["lanes"] <= 64 else 64)


def index_pool(S, code, rng, size=None):
    """-> [list of window bytes, per index PC]."""
    size = size or pool_size(S)
    wins = [index_window(S, code, rng) for _ in range(size)]
    return [[w[i] for w in wins] for i in range(S["ind_pcs"])]


def index_picks(pool, nwindows, rng):
    """Which pool window each of nwindows windows is -- ONE draw for ALL index PCs: a lane's
    10-bit field can straddle two PCs (at 32+ lanes), so every PC must take the same window."""
    size = len(pool[0])
    return rng.bytes(nwindows).translate(bytes(i & (size - 1) for i in range(256)))


def index_bytes(pool_pc, picks):
    """The picked windows of one index PC, back to back."""
    return b"".join(map(pool_pc.__getitem__, picks))


# ---- the vector ------------------------------------------------------------------------
def make_vector(nwin, rng, sigma=ACT_SIGMA):
    """-> ([bytes of act PC 0, act PC 1], [nwin x 32 bf16 raw values]). Element k of a window
    is at bits [16k+15:16k] of its 512 bits: PC 0 holds elements 0..15, PC 1 16..31."""
    raw = [[bf16_rne_raw(rng.gauss(0.0, sigma)) for _ in range(WIN_ELEMS)] for _ in range(nwin)]
    pcs = []
    for p in range(A_PCS):
        out = bytearray()
        for w in range(nwin):
            for k in range(16 * p, 16 * p + 16):
                out += struct.pack("<H", raw[w][k])
        pcs.append(bytes(out))
    return pcs, raw


# ---- one calculation ----------------------------------------------------------------------
def segment_beats(nwin, segments):
    return [n * nwin * (32 // SP_MAP[c]) for c, n in segments]


def write_calc(bindir, S, nwin, segments, vec_pcs, rng, pools=None):
    """Write one calculation's images into bindir: weights and indices of the segments
    ([[code, laps], ...], in order) and the given vector. pools: {code: index pool} to draw
    from (a layer's), else new ones. -> bytes written."""
    if not os.path.isdir(bindir):
        os.makedirs(bindir)
    pools = pools if pools is not None else {}
    nbeats = sum(segment_beats(nwin, segments))
    if nbeats * PC_BYTES > HBM_PC_BYTES:
        raise SystemExit("%s: %d beats exceed one HBM pseudo-channel" % (bindir, nbeats))
    total = 0
    for i in range(S["w_pcs"]):
        with open(os.path.join(bindir, "weights_pc%d.bin" % i), "wb") as f:
            for (c, n), nb in zip(segments, segment_beats(nwin, segments)):
                data = weight_bytes(c, nb * PC_BYTES, rng)
                f.write(data)
                total += len(data)
    for c, _ in segments:
        if c not in pools:
            pools[c] = index_pool(S, c, rng)
    picks = [index_picks(pools[c], n * nwin, rng) for c, n in segments]
    for i in range(S["ind_pcs"]):
        with open(os.path.join(bindir, "ind_pc%d.bin" % i), "wb") as f:
            for (c, n), pk in zip(segments, picks):
                data = index_bytes(pools[c][i], pk)
                f.write(data)
                total += len(data)
    for i in range(A_PCS):
        with open(os.path.join(bindir, "act_pc%d.bin" % i), "wb") as f:
            f.write(vec_pcs[i])
            total += len(vec_pcs[i])
    return total


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def verify_calc(bindir, S, nwin, segments, vec_pcs=None, sample=64, rng=None):
    """Check a written calculation from its FILES -> list of problems (empty = good):
    every image's size; the sparsity code of EVERY index beat and the zero padding above it
    (C-speed slices); the weights' high bytes all from their sparsity's table (so no zero,
    denormal, Inf or NaN); the vector, if given; and on `sample` random beats plus the first
    and last of every segment, every lane's index pair valid for its group."""
    bad = []
    seg_nb = segment_beats(nwin, segments)
    nbeats = sum(seg_nb)
    w = [_read(os.path.join(bindir, "weights_pc%d.bin" % i)) for i in range(S["w_pcs"])]
    ind = [_read(os.path.join(bindir, "ind_pc%d.bin" % i)) for i in range(S["ind_pcs"])]
    act = [_read(os.path.join(bindir, "act_pc%d.bin" % i)) for i in range(A_PCS)]
    for name, imgs, want in (("weights", w, nbeats), ("ind", ind, nbeats), ("act", act, nwin)):
        for i, img in enumerate(imgs):
            if len(img) != want * PC_BYTES:
                bad.append("%s_pc%d.bin is %d bytes, expected %d" % (name, i, len(img),
                                                                     want * PC_BYTES))
    for name in os.listdir(bindir):
        for prefix, keep in (("weights_pc", S["w_pcs"]), ("ind_pc", S["ind_pcs"]),
                             ("act_pc", A_PCS)):
            tail = name[len(prefix):-4]
            if name.startswith(prefix) and name.endswith(".bin") and tail.isdigit() \
                    and int(tail) >= keep:
                bad.append("stray image %s" % name)
    if bad:
        return bad
    # codes and padding, every beat
    sp_pc, sp_byte = S["sp_pc"], S["sp_byte"]
    lowbit = S["sparsity_bit"] % 8
    want = b"".join(bytes([int(c, 2) << lowbit]) * nb for (c, _), nb in zip(segments, seg_nb))
    if ind[sp_pc][sp_byte::PC_BYTES] != want:
        bad.append("the sparsity code (and nothing above it) is not in every index beat at "
                   "PC%d byte %d as planned" % (sp_pc, sp_byte))
    for i in range(sp_pc, S["ind_pcs"]):
        for off in range(sp_byte + 1 if i == sp_pc else 0, PC_BYTES):
            if any(ind[i][off::PC_BYTES]):
                bad.append("padding not zero: ind_pc%d byte %d" % (i, off))
                break
    # weights: every high byte from its sparsity's table
    pos = 0
    for (c, _), nb in zip(segments, seg_nb):
        allowed = hi_table(c)
        for i in range(S["w_pcs"]):
            chunk = w[i][pos * PC_BYTES:(pos + nb) * PC_BYTES]
            if chunk[1::2].translate(None, allowed):
                bad.append("weights_pc%d: a high byte not of the 2:%d table" % (i, SP_MAP[c]))
        pos += nb
    if vec_pcs is not None and act != list(vec_pcs):
        bad.append("the vector is not the layer's")
    # index groups on a sample of beats
    rng = rng or random.Random(1)
    beats, pos = set(), 0
    for nb in seg_nb:
        beats.update((pos, pos + nb - 1))
        pos += nb
    beats.update(rng.randrange(nbeats) for _ in range(min(sample, nbeats)))
    starts = [sum(seg_nb[:j]) for j in range(len(seg_nb))]
    for beat in sorted(beats):
        j = max(k for k in range(len(starts)) if starts[k] <= beat)
        m = SP_MAP[segments[j][0]]
        c = (beat - starts[j]) % (32 // m)
        word = int.from_bytes(b"".join(x[beat * PC_BYTES:(beat + 1) * PC_BYTES] for x in ind),
                              "little")
        for lane in range(S["lanes"]):
            f = (word >> (2 * IND_BITS * lane)) & 0x3FF
            a, b = f & 0x1F, f >> IND_BITS
            if not (c * m <= a < b < c * m + m):
                bad.append("beat %d lane %d: indices %d, %d not a pair of group %d (2:%d)"
                           % (beat, lane, a, b, c, m))
                break
        if len(bad) > 8:
            break
    return bad


# ---- command line: the same flags as gen_timing_stimulus.py, plus --seed and --out -------
def main():
    ap = argparse.ArgumentParser(description="realistic random SPARSE stimulus (Qwen MoE test)")
    ap.add_argument("--sparsity", default="00", choices=list(SP_MAP))
    ap.add_argument("--nwin", type=int, default=32, help="activation windows; V = 32*nwin")
    ap.add_argument("--nlaps", type=int, help="laps = output beats")
    ap.add_argument("--mix", default=None, metavar="CODE:LAPS,...",
                    help="consecutive segments, each with its own sparsity (as "
                         "gen_timing_stimulus.py); exclusive with --sparsity/--nlaps")
    ap.add_argument("--cores", type=int, default=8)
    ap.add_argument("--blocks", type=int, default=8)
    ap.add_argument("--seed", type=int, default=None, help="default: a new one from os.urandom")
    ap.add_argument("--out", default="bin", help="directory for the images (default ./bin)")
    ap.add_argument("--weight-sigma", type=float, default=WEIGHT_SIGMA,
                    help="standard deviation of the dense weights before pruning (default 1.0; "
                         "0.02 = a trained model's scale)")
    a = ap.parse_args()
    S = shape(a.cores, a.blocks)
    if a.mix:
        if a.nlaps is not None:
            raise SystemExit("--mix and --nlaps are mutually exclusive")
        segments = []
        for part in a.mix.split(","):
            code, laps = part.strip().split(":", 1)
            if code not in SP_MAP or int(laps) <= 0:
                raise SystemExit("bad --mix entry %r" % part)
            segments.append([code, int(laps)])
    else:
        if a.nlaps is None:
            raise SystemExit("give --nlaps (single sparsity) or --mix (mixed matrix)")
        segments = [[a.sparsity, a.nlaps]]
    seed = a.seed if a.seed is not None else int.from_bytes(os.urandom(8), "little")
    set_weight_sigma(a.weight_sigma)
    rng = Rng(seed)
    vec, _ = make_vector(a.nwin, rng)
    total = write_calc(a.out, S, a.nwin, segments, vec, rng)
    # images of a LARGER shape left in the same directory would be read by the next host
    stale = []
    for prefix, keep in (("weights_pc", S["w_pcs"]), ("ind_pc", S["ind_pcs"])):
        for name in os.listdir(a.out):
            tail = name[len(prefix):-4]
            if name.startswith(prefix) and name.endswith(".bin") and tail.isdigit() \
                    and int(tail) >= keep:
                os.remove(os.path.join(a.out, name))
                stale.append(name)
    bad = verify_calc(a.out, S, a.nwin, segments, vec)
    if bad:
        raise SystemExit("self-check failed: " + "; ".join(bad))
    nbeats = sum(segment_beats(a.nwin, segments))
    print("QWEN realistic stimulus, seed %d, weights N(0, %g) pruned, vector N(0, 1)"
          % (seed, a.weight_sigma))
    print("  shape %dx%d (%d lanes)  W_PCS=%d IND_PCS=%d  code at PC%d byte %d"
          % (a.cores, a.blocks, S["lanes"], S["w_pcs"], S["ind_pcs"], S["sp_pc"], S["sp_byte"]))
    print("  V = %d elements, segments %s, %d weight beats, %.1f MB"
          % (32 * a.nwin, ", ".join("%s:%d" % (c, n) for c, n in segments), nbeats, total / 1e6))
    if stale:
        print("  removed stale  = %s" % " ".join(sorted(stale)))
    print("  self-check passed (sizes, every beat's code and padding, weight exponents, "
          "index groups on a sample)")


if __name__ == "__main__":
    main()

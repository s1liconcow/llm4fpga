"""SHA-256 software specification. Hardware boundary: one compression block.

packed_input[767:512] is the eight-word chaining state (h0 most significant).
packed_input[511:0] is the 64-byte message block in big-endian order.
The result is the updated eight-word state, h0 most significant.
Padding and message splitting belong to the host; all 64 rounds belong to the FPGA.
"""

MASK = 0xFFFFFFFF
INITIAL = (0x6A09E667, 0xBB67AE85, 0x3C6EF372, 0xA54FF53A,
           0x510E527F, 0x9B05688C, 0x1F83D9AB, 0x5BE0CD19)
K = (
    0x428A2F98, 0x71374491, 0xB5C0FBCF, 0xE9B5DBA5, 0x3956C25B, 0x59F111F1, 0x923F82A4, 0xAB1C5ED5,
    0xD807AA98, 0x12835B01, 0x243185BE, 0x550C7DC3, 0x72BE5D74, 0x80DEB1FE, 0x9BDC06A7, 0xC19BF174,
    0xE49B69C1, 0xEFBE4786, 0x0FC19DC6, 0x240CA1CC, 0x2DE92C6F, 0x4A7484AA, 0x5CB0A9DC, 0x76F988DA,
    0x983E5152, 0xA831C66D, 0xB00327C8, 0xBF597FC7, 0xC6E00BF3, 0xD5A79147, 0x06CA6351, 0x14292967,
    0x27B70A85, 0x2E1B2138, 0x4D2C6DFC, 0x53380D13, 0x650A7354, 0x766A0ABB, 0x81C2C92E, 0x92722C85,
    0xA2BFE8A1, 0xA81A664B, 0xC24B8B70, 0xC76C51A3, 0xD192E819, 0xD6990624, 0xF40E3585, 0x106AA070,
    0x19A4C116, 0x1E376C08, 0x2748774C, 0x34B0BCB5, 0x391C0CB3, 0x4ED8AA4A, 0x5B9CCA4F, 0x682E6FF3,
    0x748F82EE, 0x78A5636F, 0x84C87814, 0x8CC70208, 0x90BEFFFA, 0xA4506CEB, 0xBEF9A3F7, 0xC67178F2,
)


def rotate_right(x, n):
    return ((x >> n) | (x << (32 - n))) & MASK


def pack_words(words):
    value = 0
    for word in words:
        value = (value << 32) | word
    return value


def compress(packed_input):
    state = [(packed_input >> (512 + 32 * (7 - i))) & MASK for i in range(8)]
    words = [(packed_input >> (32 * (15 - i))) & MASK for i in range(16)]
    for i in range(16, 64):
        s0 = rotate_right(words[i-15], 7) ^ rotate_right(words[i-15], 18) ^ (words[i-15] >> 3)
        s1 = rotate_right(words[i-2], 17) ^ rotate_right(words[i-2], 19) ^ (words[i-2] >> 10)
        words.append((words[i-16] + s0 + words[i-7] + s1) & MASK)
    a, b, c, d, e, f, g, h = state
    for i in range(64):
        s1 = rotate_right(e, 6) ^ rotate_right(e, 11) ^ rotate_right(e, 25)
        choose = (e & f) ^ ((~e) & g)
        t1 = (h + s1 + choose + K[i] + words[i]) & MASK
        s0 = rotate_right(a, 2) ^ rotate_right(a, 13) ^ rotate_right(a, 22)
        majority = (a & b) ^ (a & c) ^ (b & c)
        t2 = (s0 + majority) & MASK
        h, g, f, e, d, c, b, a = g, f, e, (d+t1) & MASK, c, b, a, (t1+t2) & MASK
    return pack_words([(x+y) & MASK for x,y in zip(state,(a,b,c,d,e,f,g,h))])


def padded_blocks(message):
    length = len(message)
    padded = message + b'\x80' + bytes((55-length) % 64) + (length*8).to_bytes(8, 'big')
    return [padded[i:i+64] for i in range(0,len(padded),64)]


def sha256(message):
    state = pack_words(INITIAL)
    for block in padded_blocks(message):
        state = compress((state << 512) | int.from_bytes(block,'big'))
    return state.to_bytes(32,'big')

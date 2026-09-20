"""Batch, floating-point legacy OFDM reference and deterministic test transmitter.

Rate, training, and pilot tables follow OpenOFDM (Apache-2.0), pinned by the
capture fetcher. No existing HDL is used by this module. Integer bit processing
and floating-point sample processing are deliberately separate.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import zlib

import numpy as np

RATES = {6: (1, 48, 24), 9: (1, 48, 36), 12: (2, 96, 48),
         18: (2, 96, 72), 24: (4, 192, 96), 36: (4, 192, 144),
         48: (6, 288, 192), 54: (6, 288, 216)}
RATE_BITS = {6: '1101', 9: '1111', 12: '0101', 18: '0111',
             24: '1001', 36: '1011', 48: '0001', 54: '0011'}
ACTIVE = np.array(list(range(-26, 0)) + list(range(1, 27)))
PILOTS = np.array([-21, -7, 7, 21])
DATA = np.array([k for k in ACTIVE if k not in PILOTS])
LTS_SIGNS = np.array([1,1,-1,-1,1,1,-1,1,-1,1,1,1,1,1,1,-1,-1,1,1,
    -1,1,-1,1,1,1,1,1,-1,-1,1,1,-1,1,-1,1,-1,-1,-1,-1,-1,1,1,-1,-1,
    1,-1,1,-1,1,1,1,1])
POLARITY = np.array([1,1,1,1,-1,-1,-1,1,-1,-1,-1,-1,1,1,-1,1,-1,-1,1,
    1,-1,1,1,-1,1,1,1,1,1,1,-1,1,1,1,-1,1,1,-1,-1,1,1,1,-1,1,-1,-1,
    -1,1,-1,1,-1,-1,1,-1,-1,1,1,1,1,1,-1,-1,1,1,-1,-1,1,-1,1,-1,1,1,
    -1,-1,-1,1,1,-1,-1,-1,-1,1,-1,-1,1,-1,1,1,1,1,-1,1,-1,1,-1,1,-1,
    -1,-1,-1,-1,1,-1,1,1,-1,1,-1,1,1,1,-1,-1,1,-1,-1,-1,1,1,1,-1,-1,
    -1,-1,-1,-1,-1])


def long_training() -> np.ndarray:
    bins = np.zeros(64, complex)
    bins[ACTIVE % 64] = LTS_SIGNS
    return np.fft.ifft(bins)


def short_training() -> np.ndarray:
    bins = np.zeros(64, complex)
    bins[np.array([-24,-20,-16,-12,-8,-4,4,8,12,16,20,24]) % 64] = (
        np.array([1,-1,1,-1,-1,1,-1,-1,1,1,1,1]) * (1+1j) * np.sqrt(13/6))
    return np.fft.ifft(bins)[:16]


def constellation(n: int) -> np.ndarray:
    if n == 1:
        return np.array([-1, 1], complex)
    axis = {2: [-1,1], 4: [-3,-1,3,1], 6: [-7,-5,-1,-3,7,5,1,3]}[n]
    size = 1 << (n//2)
    return np.array([complex(axis[k//size], axis[k%size]) for k in range(1<<n)]) / math.sqrt({2:2,4:10,6:42}[n])


def puncture_mask(rate: int) -> list[int]:
    return [1,1,1,0,0,1] if rate in (9,18,36,54) else [1,1,1,0] if rate == 48 else [1,1]


def convolutional_encode(bits: np.ndarray) -> np.ndarray:
    state, out = 0, []
    for bit in bits:
        reg = (int(bit)<<6) | state
        out.extend([(reg & 0o133).bit_count() & 1, (reg & 0o171).bit_count() & 1])
        state = reg >> 1
    return np.array(out, np.uint8)


def viterbi(coded: np.ndarray, rate: int = 6) -> np.ndarray:
    """64-state maximum-likelihood hard decoder; punctures carry no evidence."""
    mask = puncture_mask(rate)
    expanded = []
    pos = 0
    while pos < len(coded):
        for keep in mask:
            if keep:
                if pos == len(coded):
                    raise ValueError('Incomplete puncturing period')
                expanded.append(int(coded[pos])); pos += 1
            else:
                expanded.append(2)
    obs = np.array(expanded).reshape(-1, 2)
    states = np.arange(64)
    prev0, prev1 = (states & 31)*2, (states & 31)*2+1
    reg0, reg1 = (states >> 5)*64+prev0, (states >> 5)*64+prev1
    expected0 = np.array([[(int(r)&p).bit_count()&1 for p in (0o133,0o171)] for r in reg0])
    expected1 = np.array([[(int(r)&p).bit_count()&1 for p in (0o133,0o171)] for r in reg1])
    metrics = np.full(64, 100000, int); metrics[0] = 0
    paths = np.empty((len(obs),64), np.uint8)
    for t, pair in enumerate(obs):
        a = metrics[prev0]+np.sum((expected0 != pair) & (pair != 2), axis=1)
        b = metrics[prev1]+np.sum((expected1 != pair) & (pair != 2), axis=1)
        choose = b < a
        metrics = np.minimum(a,b)
        paths[t] = np.where(choose, prev1, prev0)
    state = int(metrics.argmin())
    result = np.empty(len(obs),np.uint8)
    for t in range(len(obs)-1,-1,-1):
        result[t] = state >> 5
        state = int(paths[t,state])
    return result


def interleave_indices(n: int) -> np.ndarray:
    count = 48*n; k = np.arange(count); s = max(n//2,1)
    i = (count//16)*(k%16)+k//16
    return s*(i//s)+(i+count-(16*i)//count)%s


def scramble(bits: np.ndarray, seed: int) -> np.ndarray:
    state = [(seed>>i)&1 for i in range(7)]
    result = []
    for b in bits:
        feedback = state[6]^state[3]
        result.append(int(b)^feedback)
        state = [feedback]+state[:-1]
    return np.array(result,np.uint8)


def descramble(bits: np.ndarray) -> np.ndarray:
    x = [0]*7
    x[0] = int(bits[2]^bits[6]); x[1] = int(bits[1]^bits[5]); x[2] = int(bits[0]^bits[4])
    x[3] = x[0]^int(bits[3]); x[4] = x[1]^int(bits[2]); x[5] = x[2]^int(bits[1]); x[6] = x[3]^int(bits[0])
    return scramble(bits, sum(b<<i for i,b in enumerate(x)))


def transmit(payload: bytes, rate: int, *, seed: int = 93, amplitude: float = 16000,
             prefix: int = 100, suffix: int = 400, cfo: float = 0, noise: float = 0,
             channel: tuple[complex,...] = (1,), rng_seed: int = 1,
             corrupt_fcs: bool = False, invalid_signal: bool = False) -> tuple[np.ndarray, bytes]:
    """Independent test transmitter; cfo in radians/sample, noise in ADC units."""
    frame = payload + (zlib.crc32(payload) ^ int(corrupt_fcs)).to_bytes(4,'little')
    if not 14 <= len(frame) <= 4095:
        raise ValueError('Frame including FCS must have 14..4095 bytes')
    n, count, dbps = RATES[rate]
    header = np.array([int(b) for b in RATE_BITS[rate]]+[0]+[(len(frame)>>i)&1 for i in range(12)]+[0]*7,np.uint8)
    header[17] = sum(int(b) for b in header[:17])%2
    if invalid_signal:
        header[17] ^= 1  # Deliberately wrong SIGNAL parity; DATA remains intact.
    def symbol(bits, width, index):
        perm = interleave_indices(width)
        inter = np.empty(len(bits),np.uint8); inter[perm] = bits
        labels = inter.reshape(-1,width).dot(1<<np.arange(width-1,-1,-1))
        bins = np.zeros(64,complex)
        bins[DATA%64] = constellation(width)[labels]
        bins[PILOTS%64] = POLARITY[index%127]*np.array([1,1,1,-1])
        time = np.fft.ifft(bins)
        return np.r_[time[-16:],time]
    length = math.ceil((16+8*len(frame)+6)/dbps)*dbps
    data = np.zeros(length,np.uint8)
    data[16:16+8*len(frame)] = np.unpackbits(np.frombuffer(frame,np.uint8),bitorder='little')
    data = scramble(data,seed)
    data[16+8*len(frame):22+8*len(frame)] = 0
    coded = convolutional_encode(data)
    mask = np.resize(puncture_mask(rate),len(coded)).astype(bool)
    coded = coded[mask]
    lts = long_training()
    waveform = [np.zeros(prefix),np.tile(short_training(),10),lts[-32:],lts,lts,
                symbol(convolutional_encode(header),1,0)]
    waveform += [symbol(coded[i:i+count],n,i//count+1) for i in range(0,len(coded),count)]
    waveform += [np.zeros(suffix)]
    samples = np.convolve(np.concatenate(waveform)*amplitude,np.asarray(channel))
    samples *= np.exp(1j*cfo*np.arange(len(samples)))
    rng = np.random.default_rng(rng_seed)
    samples += noise*(rng.normal(size=len(samples))+1j*rng.normal(size=len(samples)))
    samples = np.clip(np.rint(samples.real),-32768,32767)+1j*np.clip(np.rint(samples.imag),-32768,32767)
    return samples,frame


@dataclass
class Packet:
    start: int
    end: int
    rate: int
    data: bytes
    fcs_ok: bool
    cfo: float

    def as_dict(self):
        return {'start':self.start,'end':self.end,'rate':self.rate,'length':len(self.data),
                'data_hex':self.data.hex(),'fcs_ok':self.fcs_ok,'cfo':self.cfo}


def decode_at(samples: np.ndarray, start: int) -> Packet:
    x = np.asarray(samples[start:],complex)
    if len(x)<400:
        raise ValueError('Truncated preamble')
    cfo = float(np.angle(np.vdot(x[80:144],x[96:160]))/16)
    x = x*np.exp(-1j*cfo*np.arange(len(x)))
    # Channel estimates absorb the common phase; pilots track its later drift.
    gain = (np.fft.fft(x[192:256])+np.fft.fft(x[256:320]))/2
    gain[ACTIVE%64] *= LTS_SIGNS
    if np.any(abs(gain[ACTIVE%64])<1e-9):
        raise ValueError('Degenerate channel')
    def demod(index,n):
        offset = 320+index*80
        if offset+80>len(x):
            raise ValueError('Truncated symbol')
        bins = np.fft.fft(x[offset+16:offset+80])
        polarity = POLARITY[index%127]*np.array([1,1,1,-1])
        phase = np.angle(np.sum(np.conj(bins[PILOTS%64])*gain[PILOTS%64]*polarity))
        equal = bins[DATA%64]*np.exp(1j*phase)/gain[DATA%64]
        idx = np.argmin(abs(equal[:,None]-constellation(n)[None,:]),axis=1)
        bits = ((idx[:,None]>>np.arange(n-1,-1,-1))&1).flatten().astype(np.uint8)
        return bits[interleave_indices(n)]
    header = viterbi(demod(0,1))
    rate_bits = ''.join(str(int(b)) for b in header[:4])
    inverse = {v:k for k,v in RATE_BITS.items()}
    if rate_bits not in inverse or header[4] or sum(int(b) for b in header[:18])%2 or any(header[18:]):
        raise ValueError('Invalid SIGNAL')
    rate = inverse[rate_bits]
    length = sum(int(header[5+i])<<i for i in range(12))
    if not 14<=length<=4095:
        raise ValueError('Invalid length')
    n,_,dbps = RATES[rate]
    count = math.ceil((16+8*length+6)/dbps)
    coded = np.concatenate([demod(i+1,n) for i in range(count)])
    decoded = descramble(viterbi(coded,rate))
    if any(decoded[:16]):
        raise ValueError('Invalid SERVICE')
    data = np.packbits(decoded[16:16+length*8],bitorder='little').tobytes()
    return Packet(start,start+400+80*count,rate,data,zlib.crc32(data[:-4])==int.from_bytes(data[-4:],'little'),cfo)


def receive(samples: np.ndarray) -> list[Packet]:
    """Acquire from raw samples without packet-start, length, or rate metadata."""
    samples = np.asarray(samples,complex)
    if len(samples)<400:
        return []
    # Short training has 16-sample periodicity; a 48-pair average rejects noise.
    corr = np.convolve(np.conj(samples[:-16])*samples[16:],np.ones(48),mode='valid')
    power = np.convolve(abs(samples[16:])**2,np.ones(48),mode='valid')
    eligible = (abs(corr)>0.72*power)&(power>48*100**2)
    candidates = np.flatnonzero(eligible & ~np.r_[False,eligible[:-1]])
    packets = []; consumed = 0; lts = long_training()
    for trigger in candidates:
        if trigger < consumed:
            continue
        cfo = float(np.angle(corr[trigger])/16)
        lo = max(0,int(trigger)-80); hi = min(len(samples),int(trigger)+400)
        window = samples[lo:hi]*np.exp(-1j*cfo*np.arange(lo,hi))
        if len(window)<128:
            continue
        scores = abs(np.correlate(window,lts,mode='valid'))
        # Two consecutive LTS peaks, with exact 64-sample separation.
        pairs = np.minimum(scores[:-64],scores[64:])
        for first in np.argsort(pairs)[-4:][::-1]:
            start = lo+int(first)-192
            if start<consumed or start<0:
                continue
            try:
                pkt = decode_at(samples,start)
            except (ValueError,IndexError):
                continue
            packets.append(pkt); consumed = pkt.end
            break
    return packets


def load_iq(path: Path) -> np.ndarray:
    if path.suffix == '.dat':
        values = np.fromfile(path,dtype='<i2').reshape(-1,2)
        return values[:,0].astype(float)+1j*values[:,1]
    lines = path.read_text().splitlines()
    if len(lines[0].split()) == 2:
        values = np.loadtxt(path,dtype=np.int64)
        if np.any(values < -32768) or np.any(values > 32767):
            raise ValueError('IQ values exceed signed16 range')
        return values[:,0].astype(float)+1j*values[:,1]
    # Original OpenOFDM .txt files pack signed I in the high half, Q in the low half.
    values = np.array([int(s,16) for s in lines],np.uint32)
    return (values>>16).astype(np.uint16).view(np.int16).astype(float)+1j*(values&65535).astype(np.uint16).view(np.int16)

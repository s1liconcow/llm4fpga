"""Independent numerical contracts for blocks in the Codex receiver decomposition."""
from __future__ import annotations
from dataclasses import asdict
from pathlib import Path

import numpy as np

from .contracts import OutputField, TranslationSpec, Vector
from .problem import write_json


def prepare_fft(out: Path) -> Path:
    out.mkdir(parents=True,exist_ok=True)
    source='''import numpy as np
def fft64(samples):
    # 64 complex samples, each component an integer in [-32768,32767].
    # Forward transform, natural frequency-bin order, no 1/N normalization.
    return np.fft.fft(np.asarray(samples,dtype=complex))
'''
    (out/'fft64.py').write_text(source)
    fields=tuple(OutputField(f'{part}{i}',24,48*i+24*j,True,1,12,4)
                 for i in range(64) for j,part in enumerate(('real','imag')))
    spec=TranslationSpec('wifi_fft64',2048,3072,400,400,
        '64-point forward complex FFT, natural input and output order, NO 1/N normalization. '
        'Input complex sample i: signed16 real at bits32*i+15:32*i, signed16 imaginary at32*i+31:32*i+16. '
        'Output bin i: signed24 real at48*i+23:48*i, signed24 imaginary at48*i+47:48*i+24. '
        'Algorithm source np.fft.fft uses exp(-2*pi*j*k*n/64). Infer the radix-2 streaming/refinement '
        'architecture from this numerical function. Reuse multipliers across cycles: 192 butterflies; '
        'at most400 cycles per block including loading/draining. Enough internal integer bits for '
        'full-scale input; fixed twiddles >=18-bit accuracy. Do not reset every sample memory word. '
        'Buffers at receiver boundaries collect/drain this block interface. All arithmetic in generated RTL.',
        'fft64.py','dev.json','audit.json',clock_mhz=200,output_fields=fields,
        max_luts=12000,max_ffs=12000,max_dsps=16,max_brams=12)
    for split,seed,count in [('dev',1923,24),('audit',742192,96)]:
        rng=np.random.default_rng(seed)
        waves=[np.zeros(64,complex),np.ones(64)*32767,np.ones(64)*(-32768-32768j),
               np.r_[32767+32767j,np.zeros(63)]]
        for k in (1,7,16,31,32,47,63):
            x=30000*np.exp(2j*np.pi*k*np.arange(64)/64)
            waves.append(np.rint(x.real)+1j*np.rint(x.imag))
        for _ in range(count):
            waves.append(rng.integers(-32768,32768,64)+1j*rng.integers(-32768,32768,64))
        rows=[]
        for index,x in enumerate(waves):
            packed=sum(((int(v.real)&65535)|((int(v.imag)&65535)<<16))<<(32*i) for i,v in enumerate(x))
            ref=np.fft.fft(x)
            values=[float(v) for z in ref for v in (z.real,z.imag)]
            output=sum((int(round(v))&0xffffff)<<(24*i) for i,v in enumerate(values))
            rows.append(Vector(packed,output,tuple(values),f'fft-{index}').as_dict())
        write_json(out/f'{split}.json',rows)
    write_json(out/'spec.json',asdict(spec))
    return out/'spec.json'

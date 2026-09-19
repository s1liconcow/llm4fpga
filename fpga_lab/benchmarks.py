"""Build reproducible reference data without granting the model oracle access."""
from __future__ import annotations

import hashlib
import importlib.util
import random
import shutil
from dataclasses import asdict
from pathlib import Path

import numpy as np

from .contracts import TranslationSpec, Vector
from .problem import Golden, ROOT, partitions, write_json


def sha_module():
    path = ROOT / 'examples/sha256/sha256.py'
    spec = importlib.util.spec_from_file_location('sha256_reference', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def prepare_sha256(out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    mod = sha_module()
    shutil.copy2(ROOT / 'examples/sha256/sha256.py', out / 'sha256.py')
    spec = TranslationSpec('sha256', 768, 256, 66, 66,
        'SHA-256 compression. Input high 256 bits: chaining state, h0 first; low 512 bits: '
        'message block, first byte most significant. Output updated state, h0 first. '
        'All arithmetic modulo 2^32. Use a 16-word rolling schedule and 64 iterative rounds. '
        'Accept arbitrary 256-bit chaining states and arbitrary 512-bit blocks. '
        'Host pads messages; every compression round and state addition must be hardware.',
        'sha256.py', 'dev.json', 'audit.json', feedback_bits=256)
    rng = random.Random(20260919)
    dev_messages = [b'', b'abc', b'hello world', b'a'*55, b'a'*56, b'a'*64,
                    b'abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq', bytes(range(256))]
    audit_messages = [b'z'*n for n in [1,2,54,57,63,65,119,120,127,128,129,1000,4096]]
    audit_messages += [rng.randbytes(rng.randrange(1,512)) for _ in range(128)]
    manifests = {}
    for name, messages in [('dev',dev_messages), ('audit',audit_messages)]:
        vectors = []
        manifest = []
        for index, message in enumerate(messages):
            state = mod.pack_words(mod.INITIAL)
            for block_index, block in enumerate(mod.padded_blocks(message)):
                packed = (state << 512) | int.from_bytes(block,'big')
                state = mod.compress(packed)
                vectors.append(Vector(packed, state, label=f'message-{index}/block-{block_index}', chain=block_index>0))
            expected = hashlib.sha256(message).hexdigest()
            if state.to_bytes(32,'big').hex() != expected:
                raise RuntimeError('Independent hashlib oracle disagrees with Python SHA-256 reference')
            manifest.append({'bytes': len(message), 'sha256': expected,
                             'final_vector': len(vectors)-1,
                             'text': message.decode('ascii') if len(message)<100 and message.isascii() else None})
        # Exercise arbitrary chaining states, beyond standard message initial states.
        for index in range(8 if name=='dev' else 64):
            packed = rng.getrandbits(768)
            vectors.append(Vector(packed, mod.compress(packed), label=f'arbitrary-state-{index}'))
        write_json(out / f'{name}.json', [v.as_dict() for v in vectors])
        manifests[name] = {'messages': manifest, 'compression_vectors': len(vectors)}
    write_json(out / 'messages.json', manifests)
    write_json(out / 'spec.json', asdict(spec))
    return out / 'spec.json'


def prepare_normalizer(out: Path, golden: Golden | None = None) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    golden = golden or Golden.load()
    shutil.copy2(ROOT / 'examples/normalizer/normalize_sensor.m', out / 'normalize_sensor.m')
    spec = TranslationSpec('normalizer',12,16,16,1,
        'Input signed 12-bit ADC, range -2048..2047. Interpret input_data as signed. '
        'Compute z=adc/2048; y=z/sqrt(0.25+z*z). Output signed integer round(y*16384). '
        'Approximation permitted within the stated max and RMS error, measured after output/16384. '
        'Throughput one input per clock with bubbles preserved. '
        'Piecewise linear interpolation or a symmetry-reduced LUT are good options.',
        'normalize_sensor.m','dev.json','audit.json', output_signed=True,output_scale=16384.0,
        max_abs_error=2e-4,max_rms_error=6e-5, clock_mhz=100.0)
    dev, audit = partitions()
    for name, codes in [('dev',dev), ('audit',audit)]:
        values = golden.at(codes)
        vectors = [Vector(int(c)&4095,int(np.rint(v*16384))&65535,float(v),f'adc={c}') for c,v in zip(codes,values)]
        write_json(out / f'{name}.json',[v.as_dict() for v in vectors])
    write_json(out / 'spec.json',asdict(spec))
    write_json(out / 'golden.json',golden.provenance)
    return out / 'spec.json'

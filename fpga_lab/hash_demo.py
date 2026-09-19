"""Hash real messages by feeding a generated VHDL compression core in GHDL."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .benchmarks import sha_module
from .contracts import TranslationSpec, Vector
from .problem import write_json
from .toolchain import Toolchain
from .vhdl import Proposal, evaluate


def hash_message(message: bytes, proposal_path: Path, out: Path) -> dict:
    module = sha_module()
    proposal = Proposal.parse(proposal_path.read_text())
    spec = TranslationSpec('sha256',768,256,66,66,'SHA-256 compression','sha256.py','dev','audit',feedback_bits=256)
    state = module.pack_words(module.INITIAL)
    vectors = []
    for index, block in enumerate(module.padded_blocks(message)):
        packed = (state << 512) | int.from_bytes(block,'big')
        state = module.compress(packed)
        vectors.append(Vector(packed,state,label=f'block-{index}',chain=index>0))
    reference = hashlib.sha256(message).hexdigest()
    if state.to_bytes(32,'big').hex() != reference:
        raise RuntimeError('Python compression reference disagrees with hashlib')
    result = evaluate(proposal,spec,vectors,out,Toolchain.configured(),synthesize=False)
    if not result['accepted']:
        raise RuntimeError(f'Hardware simulation failed: {result}')
    outputs = [row.split()[2] for row in (out/'trace.txt').read_text().splitlines() if row.split()[1]=='1']
    actual = outputs[-1].lower().zfill(64)
    if actual != reference:
        raise RuntimeError('Hardware digest disagrees with hashlib')
    summary = {'sha256':actual,'reference_match':True,'bytes':len(message),'blocks':len(vectors),
               'latency_per_block':proposal.latency,'execution':'GHDL simulation of Codex-generated VHDL',
               'result':str(out/'result.json')}
    write_json(out/'hash.json',summary)
    return summary

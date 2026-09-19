import hashlib
import random
from pathlib import Path

import numpy as np
import pytest

from fpga_lab.benchmarks import prepare_sha256, sha_module
from fpga_lab.contracts import TranslationSpec, load_vectors
from fpga_lab.problem import Contract, Golden, metrics, partitions
from fpga_lab.structured import Design, search


def test_sha_reference_matches_independent_hashlib():
    module = sha_module()
    rng = random.Random(8211)
    for length in [0,1,55,56,63,64,65,119,120,127,128,1000,4096]:
        message = rng.randbytes(length)
        assert module.sha256(message) == hashlib.sha256(message).digest()


def test_sha_vectors_chain_and_partition(tmp_path):
    path = prepare_sha256(tmp_path)
    spec = TranslationSpec.load(path)
    dev = load_vectors(tmp_path / spec.vectors, spec)
    audit = load_vectors(tmp_path / spec.audit_vectors, spec)
    assert not set(v.input for v in dev) & set(v.input for v in audit)
    assert any(v.chain for v in audit)
    for prev, vector in zip(audit, audit[1:]):
        if vector.chain:
            assert vector.input >> 512 == prev.output


def test_partitions_are_exhaustive_and_disjoint():
    dev, audit = partitions()
    assert len(dev) == len(audit) == 2048
    assert not set(dev) & set(audit)
    assert set(dev) | set(audit) == set(range(-2048,2048))


def test_selected_design_is_accurate_on_all_codes(tmp_path):
    golden, contract = Golden.load(), Contract()
    candidates = search(golden, contract, tmp_path)
    codes = np.arange(-2048,2048)
    for row in candidates:
        assert metrics(codes, Design(**row['design']).evaluate(codes), golden, contract)['pass']


@pytest.mark.parametrize('log', range(3,12))
def test_normalizer_negative_endpoint_does_not_overflow(log):
    design = Design('pwl',log,16,'nearest')
    y = design.evaluate(np.array([-2048,0,2047]))
    assert -14656 <= y[0] <= -14652
    assert y[1] == 0
    assert 14650 <= y[2] <= 14655

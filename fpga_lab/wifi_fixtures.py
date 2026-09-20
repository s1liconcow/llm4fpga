"""Pinned radio recordings and deterministic, separate development/audit inputs."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import urllib.request

import numpy as np

from .problem import ROOT, digest, write_json
from .wifi_reference import RATES, Packet, load_iq, receive, transmit


@dataclass
class Case:
    name: str
    samples: np.ndarray
    expected: list[Packet]
    provenance: dict
    stalls: bool = False
    reset_at: int | None = None

    def metadata(self):
        packed = np.column_stack((self.samples.real,self.samples.imag)).astype('<i2').tobytes()
        return {'name':self.name,'samples':len(self.samples),'iq_sha256':digest(packed),
                'expected':[p.as_dict() for p in self.expected], 'provenance':self.provenance,
                'stalls':self.stalls,'reset_at':self.reset_at}


def fetch(out: Path) -> dict:
    manifest = json.loads((ROOT/'examples/wifi/captures.json').read_text())
    for item in manifest['files']:
        target = out/item['path']
        if target.exists() and digest(target.read_bytes()) == item['sha256']:
            continue
        url = f'https://raw.githubusercontent.com/jhshi/openofdm/{manifest["commit"]}/{item["path"]}'
        data = urllib.request.urlopen(url,timeout=90).read()
        if digest(data) != item['sha256']:
            raise ValueError(f'Capture checksum mismatch: {item["path"]}')
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(data)
    write_json(out/'provenance.json',manifest)
    return manifest


def capture_case(path: Path, *, limit: int | None = None, skip: int = 0) -> Case:
    samples = load_iq(path)
    packets = receive(samples)
    if not packets or not all(p.fcs_ok for p in packets):
        raise ValueError(f'Reference failed capture: {path}')
    if limit is not None:
        end = min(len(samples),packets[min(limit,len(packets))-1].end+100)
        samples = samples[:end]
        packets = [p for p in packets if p.end<=end]
    offset = 0
    if skip:
        offset = packets[skip-1].end
        samples = samples[offset:]
        packets = [Packet(p.start-offset,p.end-offset,p.rate,p.data,p.fcs_ok,p.cfo)
                   for p in packets[skip:]]
    # Padding permits a capture that starts in the first sample of the preamble.
    samples = np.r_[np.zeros(100),samples,np.zeros(500)]
    packets = [Packet(p.start+100,p.end+100,p.rate,p.data,p.fcs_ok,p.cfo) for p in packets]
    return Case(path.stem+('-tail' if skip else ''),samples,packets,{'kind':'external_capture',
        'path':path.name,'sha256':digest(path.read_bytes()),'prefix_zeros':100,'suffix_zeros':500,
        'source_sample_offset':offset,'packets_skipped':skip,
        'reference':'batch float decoder, every recovered frame validated by CRC32'})


def cases(data: Path, *, audit: bool = False) -> list[Case]:
    manifest=json.loads((ROOT/'examples/wifi/captures.json').read_text())
    for item in manifest['files']:
        path=data/item['path']
        if not path.exists() or digest(path.read_bytes())!=item['sha256']:
            raise ValueError(f'Missing or changed pinned capture: {path}; run wifi fetch')
    result = []
    seed = 903751 if audit else 41603
    rng = np.random.default_rng(seed)
    for rate in RATES:
        length = (173 if audit else 46)+rate
        cfo = (0.007 if rate%12 else -0.009) if audit else (0.003 if rate in (12,36) else 0)
        payload = rng.bytes(length)
        options = {'seed':int(rng.integers(1,128)),'prefix':int(rng.integers(80,180)),
                   'cfo':cfo,'noise':3 if audit else 0,'rng_seed':seed+rate,
                   'channel':(1,0,0.08+0.04j) if audit else (1,)}
        samples,frame = transmit(payload,rate,**options)
        ref = receive(samples)
        if len(ref)!=1 or ref[0].data != frame or not ref[0].fcs_ok:
            raise ValueError(f'Test transmitter/reference disagree at {rate} Mbps')
        result.append(Case(f'{"audit" if audit else "dev"}-{rate}Mbps',samples,ref,
                           {'kind':'synthetic','seed':seed,'known_frame_sha256':digest(frame),
                            'cfo':cfo,'noise':options['noise']},stalls=audit))
    files = sorted((data/'testing_inputs/conducted').glob('dot11a*.dat'))
    if len(files)!=7:
        raise ValueError('Fetch the pinned Wi-Fi captures first')
    for path in files:
        development = '24mbps' in path.name
        if development != audit:
            result.append(capture_case(path,limit=3 if development else None))
        elif audit and development:
            # Development uses only the first three packets of this recording.
            # Keep the remaining sixteen packets for the frozen-candidate audit.
            result.append(capture_case(path,skip=3))
    if audit:
        for path in sorted((data/'testing_inputs/simulated').glob('ag_*.txt')):
            result.append(capture_case(path))
        result.append(capture_case(data/'testing_inputs/radiated/ack-ok-openwifi.txt'))
    # Two packets in one stream, a checksum failure, silence, and reset/reacquisition.
    x,a = transmit(rng.bytes(30),24,corrupt_fcs=True)
    y,b = transmit(rng.bytes(90),6,prefix=200)
    both = np.r_[x,y]
    expected = receive(both)
    if [p.data for p in expected] != [a,b] or [p.fcs_ok for p in expected] != [False,True]:
        raise ValueError('Bad-FCS fixture reference failed')
    result.append(Case('bad-fcs-then-good',both,expected,{'kind':'synthetic','seed':seed}))
    result.append(Case('silence',np.zeros(1000,complex),[],{'kind':'synthetic'}))
    interrupted = np.r_[x[:700],np.zeros(100),y]
    reset_expected = receive(interrupted[700:])
    reset_expected = [Packet(p.start+700,p.end+700,p.rate,p.data,p.fcs_ok,p.cfo) for p in reset_expected]
    if len(reset_expected)!=1 or reset_expected[0].data!=b:
        raise ValueError('Reset fixture reference failed')
    result.append(Case('reset-reacquire',interrupted,reset_expected,{'kind':'synthetic','seed':seed},reset_at=700))
    malformed,_=transmit(rng.bytes(40),24,invalid_signal=True)
    recovery=np.r_[malformed,y]
    recovered=receive(recovery)
    if len(recovered)!=1 or recovered[0].data!=b:
        raise ValueError('Malformed SIGNAL fixture reference failed')
    result.append(Case('invalid-signal-recovery',recovery,recovered,{'kind':'synthetic','seed':seed}))
    # Long enough to expose designs whose acquisition overhead steadily fills a FIFO.
    burst=[np.zeros(100)];frames=[]
    for _ in range(48 if audit else 32):
        wave,frame=transmit(rng.bytes(10),54,prefix=0,suffix=320,seed=int(rng.integers(1,128)))
        burst.append(wave);frames.append(frame)
    burst=np.concatenate(burst);decoded=receive(burst)
    if [p.data for p in decoded]!=frames or not all(p.fcs_ok for p in decoded):
        raise ValueError('Sustained short-packet fixture reference failed')
    result.append(Case('sustained-short-packets',burst,decoded,
        {'kind':'synthetic','seed':seed,'packet_count':len(frames),'gap_samples':320,
         'check_sustained_rate':True,'max_lag_growth_cycles':200},stalls=audit))
    # Maximum-size frames exercise output drain bandwidth as well as acquisition.
    result.append(maximum_frame_burst(audit=audit))
    return result


def maximum_frame_burst(*, audit: bool = False) -> Case:
    seed = 792615 if audit else 616042
    rng = np.random.default_rng(seed)
    waves = [np.zeros(100)]
    packets = []
    offset = 100
    for i in range(16):
        wave, frame = transmit(rng.bytes(4091),54,prefix=0,suffix=320,seed=i+1)
        decoded = receive(wave)
        if len(decoded)!=1 or decoded[0].data!=frame or not decoded[0].fcs_ok:
            raise ValueError('Maximum-frame transmitter/reference mismatch')
        p = decoded[0]
        packets.append(Packet(p.start+offset,p.end+offset,p.rate,p.data,p.fcs_ok,p.cfo))
        waves.append(wave)
        offset += len(wave)
    return Case('sustained-maximum-54Mbps',np.concatenate(waves),packets,
        {'kind':'synthetic','seed':seed,'packet_count':16,'gap_samples':320,
         'check_sustained_rate':True,'max_lag_growth_cycles':200},stalls=audit)


def reference_report(data: Path, out: Path) -> dict:
    manifest = json.loads((ROOT/'examples/wifi/captures.json').read_text())
    rows = []
    for item in manifest['files']:
        path = data/item['path']
        if digest(path.read_bytes()) != item['sha256']:
            raise ValueError(f'Capture changed: {path}')
        case = capture_case(path)
        rows.append(case.metadata())
    report = {'passed':True,'captures':len(rows),'packets':sum(len(r['expected']) for r in rows),
              'reference_sha256':digest((ROOT/'fpga_lab/wifi_reference.py').read_bytes()),
              'source':manifest,'results':rows,'hardware_executed':False}
    out.mkdir(parents=True,exist_ok=True)
    write_json(out/'reference.json',report)
    return report

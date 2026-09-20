"""Observable PHY behavior, independent recordings, and hostile scoreboard cases."""
from pathlib import Path
import json
import os
import shutil

import numpy as np
import pytest

from fpga_lab.wifi_reference import RATES, POLARITY, Packet, receive, transmit
from fpga_lab.wifi_fixtures import Case, cases, reference_report
from fpga_lab.wifi_hardware import scoreboard, mapped_resources


@pytest.mark.parametrize('rate',RATES)
def test_known_payload_all_rates(rate):
    payload=np.random.default_rng(888+rate).bytes(161)
    samples,frame=transmit(payload,rate,cfo=-0.008,noise=3,channel=(1,0,0.1+0.04j))
    packets=receive(samples)
    assert len(packets)==1
    assert packets[0].data==frame and packets[0].fcs_ok and packets[0].rate==rate


@pytest.mark.parametrize('rate',[6,54])
def test_long_frame_crosses_pilot_period(rate):
    assert len(POLARITY)==127
    samples,frame=transmit(np.random.default_rng(rate).bytes(4091),rate)
    packets=receive(samples)
    assert len(packets)==1 and packets[0].data==frame and packets[0].fcs_ok


def test_bad_checksum_recovery_and_silence():
    x,a=transmit(bytes(range(60)),24,corrupt_fcs=True)
    y,b=transmit(bytes(range(120)),54)
    packets=receive(np.r_[x,y])
    assert [p.data for p in packets]==[a,b]
    assert [p.fcs_ok for p in packets]==[False,True]
    assert receive(np.zeros(4000))==[]
    bad,_=transmit(bytes(range(60)),24,invalid_signal=True)
    recovered=receive(np.r_[bad,y])
    assert len(recovered)==1 and recovered[0].data==b


def test_scoreboard_requires_bytes_metadata_boundaries_and_crc():
    x,frame=transmit(bytes(range(10)),6)
    case=Case('test',x,receive(x),{})
    lines=[f'{i+10} {int(i==0)} {int(i==len(frame)-1)} {int(i==len(frame)-1)} 06 {len(frame):03x} {b:02x}'
           for i,b in enumerate(frame)]
    assert scoreboard('\n'.join(lines),case)['pass']
    assert not scoreboard('',case)['pass']
    wrong=lines.copy();wrong[4]=wrong[4][:-2]+'ff'
    assert not scoreboard('\n'.join(wrong),case)['pass']
    wrong=lines.copy();wrong[-1]=wrong[-1].replace(' 1 1 06 ',' 1 0 06 ')
    assert not scoreboard('\n'.join(wrong),case)['pass']
    with pytest.raises(ValueError,match='without first'):
        scoreboard('\n'.join(lines[1:]),case)
    with pytest.raises(ValueError,match='Truncated'):
        scoreboard('\n'.join(lines[:-1]),case)
    assert scoreboard('0'+lines[0][2:]+'\nRESET 1\n'+'\n'.join(lines),case)['pass']


def test_pinned_external_captures(tmp_path):
    data=Path(os.environ.get('FPGA_LAB_WIFI_DATA','runs/wifi-data/openofdm'))
    if not (data/'testing_inputs').exists():
        pytest.skip('Download pinned captures with fpga-lab wifi fetch')
    report=reference_report(data,tmp_path)
    assert report['captures']==14 and report['packets']==137
    partitions=[]
    for audit in (False,True):
        identities=set()
        for case in cases(data,audit=audit):
            if case.provenance['kind']=='external_capture':
                for packet in case.expected:
                    identities.add((case.provenance['path'],
                        packet.start-100+case.provenance['source_sample_offset']))
        partitions.append(identities)
    assert partitions[0].isdisjoint(partitions[1])
    assert len(partitions[0] | partitions[1]) == 137


def test_scoreboard_rejects_correct_packets_with_growing_backlog():
    packets = [Packet(i*800, i*800+480, 54, b'abc', True, 0) for i in range(32)]
    case = Case('sustained', np.zeros(1), packets,
                {'check_sustained_rate': True, 'max_lag_growth_cycles': 200})

    def trace(extra_per_packet):
        rows = []
        for i, packet in enumerate(packets):
            start = packet.end*10 + 2000 + extra_per_packet*i
            for j, byte in enumerate(packet.data):
                rows.append(f'{start+j} {int(j==0)} {int(j==2)} {int(j==2)} 36 003 {byte:02x}')
        return '\n'.join(rows)

    assert scoreboard(trace(0), case)['pass']
    delayed = scoreboard(trace(638), case)
    assert delayed['matched_frames'] == 32
    assert not delayed['pass']
    assert delayed['throughput']['output_cycles_per_packet'] == 8638


def test_resource_check_counts_ram_luts_and_rejects_unmapped_logic(tmp_path):
    def netlist(types):
        (tmp_path/'netlist.json').write_text(json.dumps({'modules':{'dut':{
            'cells':{str(i):{'type':t} for i,t in enumerate(types)}}}}))
    netlist(['LUT6','RAM64M','RAMB36E1','FDRE','$buf'])
    resources=mapped_resources(tmp_path)
    assert resources['luts']==5
    assert resources['bram18_equivalents']==2
    netlist(['LUT6','$mul'])
    with pytest.raises(ValueError,match='Unmapped logic'):
        mapped_resources(tmp_path)


@pytest.mark.hardware
@pytest.mark.skipif(os.getenv('FPGA_LAB_TEST_WIFI_HARDWARE')!='1',reason='Requires the Wi-Fi tools image')
def test_vendor_ram_model_reads_written_data(tmp_path):
    from fpga_lab.problem import ROOT
    from fpga_lab.toolchain import Toolchain
    shutil.copyfile(ROOT/'fpga_lab/hdl/RAMB36E1.v',tmp_path/'RAMB36E1.v')
    shutil.copyfile(ROOT/'tests/hdl/wifi_ram.v',tmp_path/'top.v')
    shutil.copyfile(ROOT/'tests/hdl/wifi_ram.cpp',tmp_path/'driver.cpp')
    tools=Toolchain(image=os.getenv('FPGA_LAB_WIFI_TEST_IMAGE','xls-e2e-wifi:local'))
    tools.run(['verilator','--cc','--exe','--build','-j','2','--no-timing','-Wno-fatal',
        '--top-module','sim_top','top.v','RAMB36E1.v','driver.cpp'],tmp_path,timeout=120,log='build.log')
    output=tools.run(['./obj_dir/Vsim_top'],tmp_path,log='simulation.log')
    assert '8192 RAM words match' in output

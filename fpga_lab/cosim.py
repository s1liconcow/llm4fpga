"""Persistent mapped-RTL simulation for host/hardware feedback algorithms."""
from __future__ import annotations

import selectors
import json
import shutil
import subprocess
import uuid
from pathlib import Path

from .contracts import TranslationSpec
from .problem import digest, write_json
from .toolchain import Toolchain
from .vhdl import Proposal


def driver(spec: TranslationSpec, latency: int) -> str:
    if spec.input_bits <= 64 or spec.output_bits <= 64:
        raise ValueError('Persistent simulator currently requires wide (>64 bit) packed buses')
    return f'''#include "Vdut.h"
#include "verilated.h"
#include <algorithm>
#include <iostream>
#include <iomanip>
#include <sstream>
#include <string>
#include <memory>
#include <stdexcept>
int main(int argc, char** argv) {{
  auto context = std::make_unique<VerilatedContext>();
  context->commandArgs(argc,argv);
  auto dut = std::make_unique<Vdut>(context.get());
  auto tick = [&]() {{
    dut->clk=0; context->timeInc(1); dut->eval();
    dut->clk=1; context->timeInc(1); dut->eval();
  }};
  try {{
    dut->in_valid=0; dut->rst=1;
    for(int i=0;i<{(spec.input_bits+31)//32};++i) dut->input_data[i]=0;
    for(int i=0;i<3;++i) tick();
    if(dut->out_valid) throw std::runtime_error("reset failed");
    dut->rst=0;
    std::cout << "READY" << std::endl;
    std::string line;
    while(std::getline(std::cin,line)) {{
      if(line.size() != {(spec.input_bits+3)//4}) throw std::runtime_error("bad input width");
      for(int i=0;i<{(spec.input_bits+31)//32};++i) {{
        int end=int(line.size())-8*i;
        int start=std::max(0,end-8);
        dut->input_data[i]=std::stoul(line.substr(start,end-start),nullptr,16);
      }}
      std::string output;
      for(int edge=1;edge<={max(spec.initiation_interval,latency)};++edge) {{
        dut->in_valid=(edge==1);
        tick();
        if(bool(dut->out_valid) != (edge=={latency}))
          throw std::runtime_error("cycle-level validity mismatch");
        if(dut->out_valid) {{
          std::ostringstream out;
          out << std::hex << std::setfill('0');
          for(int i={(spec.output_bits+31)//32-1};i>=0;--i) out << std::setw(8) << dut->output_data[i];
          output=out.str();
        }}
      }}
      std::cout << output << std::endl;
    }}
    dut->final();
  }} catch(const std::exception& e) {{ std::cerr << e.what() << std::endl; return 2; }}
  return 0;
}}
'''


def build_server(candidate: Path, spec: TranslationSpec, out: Path) -> Path:
    proposal = Proposal.parse((candidate/'proposal.json').read_text())
    result = json.loads((candidate/'result.json').read_text())
    if not result.get('accepted') or not result.get('post_synthesis',{}).get('pass'):
        raise ValueError('Persistent cosimulation requires a verified, synthesized candidate')
    if digest(proposal.vhdl.encode()) != result['vhdl_sha256'] or (candidate/'candidate.vhd').read_text() != proposal.vhdl:
        raise ValueError('Candidate hash does not match its verified proposal')
    summary_path = candidate.parent/'summary.json'
    if summary_path.exists():
        summary = json.loads(summary_path.read_text())
        if summary.get('contract') != spec.public_contract() or not summary.get('accepted'):
            raise ValueError('Candidate translation contract is different or its audit did not pass')
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidate/'mapped.v',out/'mapped.v')
    (out/'driver.cpp').write_text(driver(spec,proposal.latency))
    Toolchain.configured().run(['verilator','--cc','--exe','--build','--timing','-j','2','-Wno-fatal',
                                '--top-module','dut','--Mdir','server_obj','-o','rtl_server',
                                'mapped.v','driver.cpp','/usr/share/yosys/xilinx/cells_sim.v'],
                               out,timeout=300,log='server_build.log')
    write_json(out/'server.json',{'candidate_vhdl_sha256':result['vhdl_sha256'],
                                  'mapped_verilog_sha256':digest((out/'mapped.v').read_bytes()),
                                  'driver_sha256':digest((out/'driver.cpp').read_bytes()),
                                  'executable_sha256':digest((out/'server_obj/rtl_server').read_bytes()),
                                  'latency':proposal.latency,'contract':spec.public_contract(),
                                  'simulator':'Verilator Xilinx-mapped netlist, two-state'})
    return out


class HardwareSession:
    """One persistent Podman process; every request executes the mapped hardware."""
    def __init__(self, folder: Path, spec: TranslationSpec, timeout: float = 30, log_folder: Path | None = None):
        self.folder, self.spec, self.timeout = folder, spec, timeout
        self.log_folder = log_folder or folder
        self.tools = Toolchain.configured()
        self.name = 'fpga-lab-' + uuid.uuid4().hex[:16]
        self.process = None
        self.selector = None
        self.log = None
        self.requests = 0

    def _line(self) -> str:
        if not self.selector.select(self.timeout):
            raise TimeoutError('Mapped RTL simulator did not respond before the deadline')
        line = self.process.stdout.readline().strip()
        if not line:
            raise RuntimeError(f'Mapped RTL simulator closed its output; see {self.log_folder / "server.log"}')
        return line

    def __enter__(self):
        metadata = json.loads((self.folder/'server.json').read_text())
        if metadata['contract'] != self.spec.public_contract():
            raise ValueError('Persistent simulator contract mismatch')
        for filename,key in [('mapped.v','mapped_verilog_sha256'),('driver.cpp','driver_sha256'),
                              ('server_obj/rtl_server','executable_sha256')]:
            if key in metadata and digest((self.folder/filename).read_bytes()) != metadata[key]:
                raise ValueError(f'Persistent simulator artifact changed: {filename}')
        self.log = (self.log_folder/'server.log').open('w')
        command = self.tools.command(['./server_obj/rtl_server'],self.folder,self.name,interactive=True)
        try:
            self.process = subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                            stderr=self.log,text=True,bufsize=1)
            self.selector = selectors.DefaultSelector()
            self.selector.register(self.process.stdout,selectors.EVENT_READ)
            if self._line() != 'READY':
                raise RuntimeError('Unexpected simulator startup response')
        except BaseException:
            self.__exit__(None,None,None)
            raise
        return self

    def request(self, word: int) -> int:
        if type(word) is not int or not 0 <= word < 1 << self.spec.input_bits:
            raise ValueError('Input does not fit the hardware port')
        self.process.stdin.write(f'{word:0{(self.spec.input_bits+3)//4}x}\n')
        self.process.stdin.flush()
        response = self._line()
        if len(response) != 8*((self.spec.output_bits+31)//32):
            raise RuntimeError('Malformed simulator output width')
        result = int(response,16)
        if result >= 1 << self.spec.output_bits:
            raise RuntimeError('Simulator output exceeds the port width')
        self.requests += 1
        return result

    def __exit__(self, *_):
        if self.process is not None:
            if self.process.stdin:
                try:
                    self.process.stdin.close()
                except BrokenPipeError:
                    pass
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            finally:
                subprocess.run([self.tools.runtime,'rm','-f',self.name],capture_output=True,timeout=15)
            if self.process.stdout:
                self.process.stdout.close()
        if self.selector:
            self.selector.close()
        if self.log:
            self.log.close()

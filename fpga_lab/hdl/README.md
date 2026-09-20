# FPGA simulation models

`RAMB36E1.v` is copied unchanged from Xilinx's Apache-2.0
[Unisim library](https://github.com/Xilinx/XilinxUnisimLibrary/blob/1c8e05fd1e9a79ceb8b996a0996674122eed086f/verilog/src/unisims/RAMB36E1.v),
commit `1c8e05fd1e9a79ceb8b996a0996674122eed086f`. Its license is
in `LICENSE.xilinx`.

Yosys's `cells_sim.v` declares this block RAM without implementing storage.
The mapped receiver harness replaces that declaration with this vendor model.
Other mapped primitives use Yosys's functional models. Verilator runs functional
simulation with delays disabled; the driver advances simulation time for the
vendor model's clock/collision bookkeeping. Global configuration reset is low;
the receiver's own reset is tested explicitly.

# Usage: vivado -mode batch -source examples/wifi/search/vivado.tcl -tclargs <FPGA-part>
if {$argc != 1} { error "Pass the actual AMD/Xilinx FPGA part" }
set here [file dirname [file normalize [info script]]]
set part [lindex $argv 0]
create_project -in_memory -part $part
read_vhdl -vhdl2008 [file join $here receiver.vhd]
read_xdc [file join $here clock.xdc]
synth_design -top dut -part $part -mode out_of_context
report_utilization -file [file join $here vivado_utilization.rpt]
report_timing_summary -file [file join $here vivado_timing.rpt]
write_checkpoint -force [file join $here receiver_synth.dcp]
# Board integration, placement, routing, and bitstream generation follow this step.

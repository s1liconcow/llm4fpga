# Usage: vivado -mode batch -source vivado.tcl -tclargs xc7a35tcpg236-1
# Out-of-context IP synthesis: choose the actual part for your board.
if {$argc != 1} { error "Pass the FPGA part as the sole argument" }
set here [file dirname [file normalize [info script]]]
set part [lindex $argv 0]
create_project -in_memory -part $part
read_vhdl -vhdl2008 [file join $here sha256_core.vhd]
read_xdc [file join $here clock.xdc]
synth_design -top dut -part $part -mode out_of_context
report_utilization -file [file join $here vivado_utilization.rpt]
report_timing_summary -file [file join $here vivado_timing.rpt]
write_checkpoint -force [file join $here dut_synth.dcp]
# Pin assignments / board integration are needed before implementation and bitstream generation.

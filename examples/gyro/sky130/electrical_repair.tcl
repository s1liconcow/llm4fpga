# Adapted from LibreLane repair_design_postgrt.tcl.
# Copyright 2020-2023 Efabless Corporation
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0: https://www.apache.org/licenses/LICENSE-2.0
# Distributed on an AS IS BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND.
source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
source $::env(SCRIPTS_DIR)/openroad/common/resizer.tcl
read_current_odb
set_propagated_clock [all_clocks]
set_dont_touch_objects
source $::env(SCRIPTS_DIR)/openroad/common/set_rc.tcl

# Preserve the balanced CTS network. Its root is far from the top-left clock
# pin, so add one low-input-capacitance, strong-drive entry buffer near the pin.
# Blanket clock-wire repair introduced excessive insertion delay in a trial.
if {[llength [get_cells -quiet gyro_clock_entry*]] == 0} {
    set block [ord::get_db_block]
    set units [$block getDbUnitsPerMicron]
    set core [$block getCoreArea]
    set port [$block findBTerm $::env(CLOCK_PORT)]
    set pin [lindex [$port getBPins] 0]
    set box [lindex [$pin getBoxes] 0]
    set x [expr {([$box xMin] + [$box xMax]) / (2.0 * $units)}]
    set y [expr {([$box yMin] + [$box yMax]) / (2.0 * $units)}]
    set x [expr {min(max($x, [$core xMin] / double($units) + 20), [$core xMax] / double($units) - 20)}]
    set y [expr {min(max($y, [$core yMin] / double($units) + 20), [$core yMax] / double($units) - 20)}]
    log_cmd insert_buffer -buffer_cell sky130_fd_sc_hd__clkbuf_8 \
        -net [get_nets $::env(CLOCK_PORT)] -location [list $x $y] \
        -buffer_name gyro_clock_entry -net_name gyro_clock_distribution
}
estimate_parasitics -placement
source $::env(SCRIPTS_DIR)/openroad/common/dpl.tcl
source $::env(SCRIPTS_DIR)/openroad/common/grt.tcl

# Repeat with updated routes: newly inserted buffers and diodes must be included
# in the next load estimate. Keep the actual timing/slew limits unchanged.
for {set pass 1} {$pass <= 3} {incr pass} {
    puts "GYRO_ELECTRICAL_PASS $pass"
    estimate_parasitics -global_routing
    log_cmd repair_design -verbose -max_wire_length $::env(GRT_DESIGN_REPAIR_MAX_WIRE_LENGTH) \
        -slew_margin $::env(GRT_DESIGN_REPAIR_MAX_SLEW_PCT) \
        -cap_margin $::env(GRT_DESIGN_REPAIR_MAX_CAP_PCT)
    source $::env(SCRIPTS_DIR)/openroad/common/dpl.tcl
    source $::env(SCRIPTS_DIR)/openroad/common/grt.tcl
}
unset_dont_touch_objects
write_views

# Copyright 2026. SPDX-License-Identifier: Apache-2.0
# Optimize at max-RC/slow PVT; independently verify all nine corners afterward.
source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
source $::env(SCRIPTS_DIR)/openroad/common/resizer.tcl
foreach key [array names ::env _LIB_CORNER_*] { unset ::env($key) }
set ::env(_LIB_CORNER_0) [concat max_ss_100C_1v60 [dict get $::env(LIB) *_ss_100C_1v60]]
read_current_odb
remove_fillers
# Track geometry before editing. Invalidate every incident signal/clock wire
# after legalization, including neighbors moved by the placer. This avoids the
# stale-fragment failure of naive incremental rerouting.
proc gyro_geometry {inst} {
    set box [$inst getBBox]
    return [list [[$inst getMaster] getName] [$box xMin] [$box yMin] \
        [$box xMax] [$box yMax] [$inst getOrient]]
}
set gyro_route_baseline [dict create]
foreach inst [[ord::get_db_block] getInsts] {
    dict set gyro_route_baseline [$inst getName] [gyro_geometry $inst]
}
proc gyro_invalidate_changed_routes {} {
    set dirty [dict create]
    set changed 0
    set missing $::gyro_route_baseline
    foreach inst [[ord::get_db_block] getInsts] {
        set name [$inst getName]
        if {[dict exists $missing $name]} { dict unset missing $name }
        if {![dict exists $::gyro_route_baseline $name] ||
            [gyro_geometry $inst] ne [dict get $::gyro_route_baseline $name]} {
            incr changed
            foreach pin [$inst getITerms] {
                set net [$pin getNet]
                if {$net == "NULL" || [$net getSigType] in {POWER GROUND}} { continue }
                dict set dirty [$net getName] $net
            }
        }
    }
    # Unexpected removal loses endpoint geometry: fall back to a full reroute.
    if {[dict size $missing] > 0} {
        foreach net [[ord::get_db_block] getNets] {
            if {[$net getSigType] ni {POWER GROUND}} { dict set dirty [$net getName] $net }
        }
    }
    dict for {name net} $dirty {
        set wire [$net getWire]
        if {$wire != "NULL"} { odb::dbWire_destroy $wire }
        $net clearGuides
    }
    puts "INVALIDATED_ROUTES nets=[dict size $dirty] changed_instances=$changed removed_instances=[dict size $missing]"
}
set_propagated_clock [all_clocks]
set_dont_touch_objects
source $::env(SCRIPTS_DIR)/openroad/common/set_rc.tcl

# Retain the full network so cell replacement can re-reduce it with new pin
# capacitances. Do not initialize estimated parasitics before this sizing pass.
log_cmd read_spef -corner max_ss_100C_1v60 $::env(GYRO_SPEF_MAX)
report_check_types -max_slew -max_capacitance -violators > $::env(STEP_DIR)/electrical-before.rpt
source [file join [file dirname [info script]] targeted_sizing.tcl]

# Extracted max-RC corners exposed one remaining clock-trunk violation at the
# CTS root input. Split that single long wire; retain all balanced CTS branches.
if {[llength [get_cells -quiet gyro_clock_midpoint*]] == 0} {
    set block [ord::get_db_block]
    set root [$block findInst clkbuf_0_clk]
    if {$root == "NULL"} {
        error "Expected CTS root is missing"
    }
    set trunk [[$root findITerm A] getNet]
    set entry "NULL"
    foreach pin [$trunk getITerms] {
        if {[$pin getIoType] eq "OUTPUT"} {
            set entry [$pin getInst]
        }
    }
    if {$entry == "NULL"} {
        error "CTS root input has no cell driver"
    }
    set a [$entry getBBox]
    set b [$root getBBox]
    set units [$block getDbUnitsPerMicron]
    set x [expr {([$a xMin]+[$a xMax]+[$b xMin]+[$b xMax])/(4.0*$units)}]
    set y [expr {([$a yMin]+[$a yMax]+[$b yMin]+[$b yMax])/(4.0*$units)}]
    log_cmd insert_buffer -buffer_cell sky130_fd_sc_hd__clkbuf_16 \
        -net [get_nets [$trunk getName]] -location [list $x $y] \
        -buffer_name gyro_clock_midpoint -net_name gyro_clock_root_feed
}

# A maximum-drive logic gate can still be too weak for its local loads even
# after long-wire repair. Give each extracted overloaded net a nearby source
# buffer, then repair its downstream branches. Preserve the original net name.
dict for {net_name driver} $gyro_buffer_drivers {
    set inst [$gyro_block findInst [file dirname $driver]]
    set box [$inst getBBox]
    set units [$gyro_block getDbUnitsPerMicron]
    set x [expr {([$box xMin]+[$box xMax])/(2.0*$units)}]
    set y [expr {([$box yMin]+[$box yMax])/(2.0*$units)}]
    set nets [get_nets -of_objects [get_pins $driver]]
    if {[llength $nets] != 1} { error "Expected one source net at $driver" }
    log_cmd insert_buffer -buffer_cell sky130_fd_sc_hd__buf_8 -net $nets \
        -location [list $x $y] -buffer_name gyro_signal_root -net_name gyro_signal_feed
    set net [$gyro_block findNet $net_name]
    if {$net == "NULL"} { error "Source buffering renamed expected net $net_name" }
    set new_driver ""
    foreach pin [$net getITerms] {
        if {[$pin getIoType] eq "OUTPUT"} {
            set new_driver "[[$pin getInst] getName]/[[$pin getMTerm] getName]"
        }
    }
    if {![string match gyro_signal_root* $new_driver]} {
        error "Expected inserted source buffer on $net_name, got $new_driver"
    }
    dict set gyro_buffer_drivers $net_name $new_driver
}
source $::env(SCRIPTS_DIR)/openroad/common/dpl.tcl
gyro_invalidate_changed_routes
source $::env(SCRIPTS_DIR)/openroad/common/grt.tcl
estimate_parasitics -global_routing

# Buffer only the nets identified using the full extracted model. This pinned
# OpenROAD entry point takes the length in meters; 200 um bounds each branch.
set gyro_existing [dict create]
foreach inst [$gyro_block getInsts] { dict set gyro_existing [$inst getName] 1 }
dict for {net_name driver} $gyro_buffer_drivers {
    set nets [get_nets -of_objects [get_pins $driver]]
    if {[llength $nets] != 1} { error "Expected one net at $driver" }
    log_cmd rsz::repair_net_cmd [lindex $nets 0] 0.0002 30 50
}
# New repeaters need headroom for detailed-wire and protection-diode loads.
# Keep their existing logical family, and use at least drive 8 when available.
set gyro_new_buffers 0
foreach inst [$gyro_block getInsts] {
    set name [$inst getName]
    if {[dict exists $gyro_existing $name]} { continue }
    set cell [[$inst getMaster] getName]
    if {![regexp {^(sky130_fd_sc_hd__(?:clkbuf|buf))_([0-9]+)$} $cell -> family strength]} {
        error "Unexpected cell inserted during targeted buffering: $cell"
    }
    set sizes [lsort -integer -unique [dict get $gyro_families $family]]
    set selected [lindex $sizes end]
    foreach size $sizes { if {$size >= 8} { set selected $size; break } }
    if {$selected > $strength} { replace_cell $name ${family}_$selected }
    incr gyro_new_buffers
}
puts "TARGETED_NEW_BUFFERS $gyro_new_buffers"
source $::env(SCRIPTS_DIR)/openroad/common/dpl.tcl
gyro_invalidate_changed_routes
source $::env(SCRIPTS_DIR)/openroad/common/grt.tcl
estimate_parasitics -global_routing
report_check_types -max_slew -max_capacitance -violators > $::env(STEP_DIR)/electrical-estimated-after.rpt
unset_dont_touch_objects
write_views

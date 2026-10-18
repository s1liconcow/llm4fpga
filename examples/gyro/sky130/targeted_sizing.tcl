# Copyright 2026. SPDX-License-Identifier: Apache-2.0
# Optimize against a retained full SPEF network. Reduced-only models cannot
# reliably preserve wire loads as cell replacements invalidate their reductions.
# Same-family drive variants preserve the Boolean/sequential cell function;
# the final exported netlist must still pass the independent functional replay.
set gyro_block [ord::get_db_block]
set gyro_families [dict create]
foreach lib [[ord::get_db] getLibs] {
    foreach master [$lib getMasters] {
        set name [$master getName]
        if {[regexp {^(sky130_fd_sc_hd__.+)_([0-9]+)$} $name -> family strength]} {
            dict lappend gyro_families $family $strength
        }
    }
}

proc gyro_signal_driver {pin} {
    set slash [string last / $pin]
    if {$slash < 0} { return "NULL" }
    set name [string range $pin 0 $slash-1]
    if {[string match clkbuf_*_clk $name] || [string match gyro_clock* $name]} {
        return "NULL"
    }
    set inst [$::gyro_block findInst $name]
    if {$inst == "NULL"} { error "Unknown reported instance $name" }
    set iterm [$inst findITerm [string range $pin $slash+1 end]]
    if {$iterm == "NULL"} { error "Unknown reported pin $pin" }
    if {[$iterm getIoType] ne "OUTPUT" || [[$iterm getNet] getSigType] eq "CLOCK"} {
        return "NULL"
    }
    return $iterm
}

set gyro_target_slew [expr {0.6 * $::env(MAX_TRANSITION_CONSTRAINT)}]
set_max_transition $gyro_target_slew [current_design]
set gyro_changes [open $::env(STEP_DIR)/targeted-sizing.tsv w]
puts $gyro_changes "round\tinstance\told_cell\tnew_cell\tslew_ns"
for {set round 0} {$round < 16} {incr round} {
    set report $::env(STEP_DIR)/targeted-sizing-$round.rpt
    report_check_types -max_slew -violators -digits 6 -no_line_splits > $report
    set f [open $report r]; set lines [split [read $f] "\n"]; close $f
    set requests [dict create]
    foreach line $lines {
        if {![regexp {^(\S+)\s+([0-9.]+)\s+([0-9.]+)\s+[-0-9.]+\s+\(VIOLATED\)} $line -> pin limit slew]} { continue }
        set iterm [gyro_signal_driver $pin]
        if {$iterm == "NULL"} { continue }
        set name [[$iterm getInst] getName]
        if {![dict exists $requests $name] || $slew > [dict get $requests $name]} {
            dict set requests $name $slew
        }
    }
    set changed 0
    dict for {name slew} $requests {
        set inst [$gyro_block findInst $name]
        set cell [[$inst getMaster] getName]
        if {![regexp {^(sky130_fd_sc_hd__.+)_([0-9]+)$} $cell -> family strength]} {
            error "Unsupported cell family for targeted sizing: $cell"
        }
        set sizes [lsort -integer -unique [dict get $gyro_families $family]]
        set target [expr {max(0.5,$strength)*max(1.6,$slew/$gyro_target_slew*1.1)}]
        set selected [lindex $sizes end]
        foreach size $sizes {
            if {$size >= $target} { set selected $size; break }
        }
        if {$selected <= $strength} { continue }
        replace_cell $name ${family}_$selected
        puts $gyro_changes "$round\t$name\t$cell\t${family}_$selected\t$slew"
        incr changed
    }
    puts "TARGETED_SIZING round=$round violating_drivers=[dict size $requests] resized=$changed"
    if {$changed == 0} { break }
    if {$round == 15} { error "Targeted sizing did not converge in 16 rounds" }
}
close $gyro_changes

# Capture only the remaining overloaded signal nets before changing topology.
# These are the nets that require buffering rather than another drive variant.
# Pin capacitance varies across PVT. Leave 20% load headroom while optimizing
# at the slow corner; the independent nine-corner check uses the original limit.
set_max_capacitance [expr {0.8 * $::env(MAX_CAPACITANCE_CONSTRAINT)}] [current_design]
set report $::env(STEP_DIR)/targeted-buffer-nets.rpt
report_check_types -max_slew -max_capacitance -violators -digits 6 -no_line_splits > $report
set f [open $report r]; set lines [split [read $f] "\n"]; close $f
set gyro_buffer_drivers [dict create]
foreach line $lines {
    if {![regexp {^(\S+)\s+[0-9.]+\s+[0-9.]+\s+[-0-9.]+\s+\(VIOLATED\)} $line -> pin]} { continue }
    set iterm [gyro_signal_driver $pin]
    if {$iterm == "NULL"} { continue }
    dict set gyro_buffer_drivers [[$iterm getNet] getName] $pin
}
set_max_transition $::env(MAX_TRANSITION_CONSTRAINT) [current_design]
set_max_capacitance $::env(MAX_CAPACITANCE_CONSTRAINT) [current_design]
report_check_types -max_slew -max_capacitance -violators -digits 6 -no_line_splits > $::env(STEP_DIR)/electrical-after-sizing.rpt
puts "TARGETED_BUFFER_NETS [dict size $gyro_buffer_drivers]"

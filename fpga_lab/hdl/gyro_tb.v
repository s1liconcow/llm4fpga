`timescale 1ns/1ps
module gyro_tb;
    reg clk=0, rst=1, in_valid=0, out_ready=0, cfg_valid=0;
    reg signed [17:0] adc_a=0, adc_b=0;
    reg [10:0] cfg_addr=0;
    reg signed [23:0] cfg_wdata=0;
    wire in_ready, out_valid, cfg_ready, cfg_error;
    wire signed [17:0] actuator_x, actuator_y, rate_i, rate_q, agc_gain;
    // Physical tools may reorder the top-level port list.
    gyro_core dut(.clk(clk), .rst(rst), .in_valid(in_valid), .in_ready(in_ready),
                  .adc_a(adc_a), .adc_b(adc_b), .out_valid(out_valid), .out_ready(out_ready),
                  .actuator_x(actuator_x), .actuator_y(actuator_y), .rate_i(rate_i),
                  .rate_q(rate_q), .agc_gain(agc_gain), .cfg_valid(cfg_valid),
                  .cfg_ready(cfg_ready), .cfg_addr(cfg_addr), .cfg_wdata(cfg_wdata),
                  .cfg_error(cfg_error));
    always #20 clk=~clk;
    integer cycle=0, accepted_cycle=0, outputs=0, source, trace, count, cmd, x, y, z, watchdog;
    reg pending=0, stalled=0;
    reg [89:0] held;
    wire [89:0] bundle={actuator_x,actuator_y,rate_i,rate_q,agc_gain};
    always @(negedge clk) out_ready <= cycle%79 >= 19;
    always @(posedge clk) begin
        cycle=cycle+1;
        if (cycle > 4000000) $fatal(1,"Global timeout");
        if (rst) begin stalled=0; pending=0; end
        else begin
            if (stalled && (out_valid !== 1 || bundle !== held)) $fatal(1,"Unstable stalled output");
            if (in_valid && in_ready) begin
                if (pending) $fatal(1,"Overlapping input transaction");
                accepted_cycle=cycle; pending=1;
            end
            if (out_valid && out_ready) begin
                if (!pending) $fatal(1,"Unexpected output");
                if ((^bundle) === 1'bx) $fatal(1,"Unknown output");
                $fdisplay(trace,"%0d %0d %0d %0d %0d %0d %0d",cycle,
                          cycle-accepted_cycle,actuator_x,actuator_y,rate_i,rate_q,agc_gain);
                outputs=outputs+1; pending=0;
            end
            stalled=out_valid && !out_ready; held=bundle;
        end
    end
    task idle_ready;
        begin
            watchdog=0;
            while (!cfg_ready) begin
                @(negedge clk); watchdog=watchdog+1;
                if(watchdog>2000) $fatal(1,"Ready timeout");
            end
        end
    endtask
    task reset_core;
        begin
            rst=1; in_valid=0; cfg_valid=0;
            repeat(3) @(negedge clk);
            rst=0;
            @(negedge clk); idle_ready;
        end
    endtask
    initial begin
        source=$fopen("stimulus.txt","r"); trace=$fopen("trace.txt","w");
        if(!source || !trace) $fatal(1,"Missing stimulus/trace");
        @(negedge clk);
        while (!$feof(source)) begin
            count=$fscanf(source,"%d %d %d %d\n",cmd,x,y,z);
            if(count!=4) $fatal(1,"Bad stimulus");
            case(cmd)
                0: begin if (!rst) idle_ready; reset_core; end
                1: begin
                    idle_ready;
                    cfg_valid=1; cfg_addr=x; cfg_wdata=y;
                    @(negedge clk); cfg_valid=0;
                    if(cfg_error) $fatal(1,"Valid config rejected");
                end
                2: begin
                    idle_ready;
                    in_valid=1; adc_a=x; adc_b=y;
                    @(negedge clk); in_valid=0;
                    // Deliberately request a config write while busy: must not accept it.
                    cfg_valid=1; cfg_addr=11'h400; cfg_wdata=0;
                    repeat(3) begin
                        if(cfg_ready) $fatal(1,"Config accepted during a sample");
                        @(negedge clk);
                    end
                    cfg_valid=0;
                end
                3: repeat(x) @(negedge clk);
                4: begin
                    idle_ready;
                    adc_a=x; adc_b=y; in_valid=1;
                    @(negedge clk); in_valid=0;
                    repeat(20) @(negedge clk);
                    reset_core;
                end
                5: begin
                    idle_ready;
                    cfg_valid=1; cfg_addr=11'h7ff; cfg_wdata=0;
                    @(negedge clk); cfg_valid=0;
                    if(!cfg_error) $fatal(1,"Invalid config address accepted");
                end
                default: $fatal(1,"Unknown command");
            endcase
        end
        idle_ready;
        repeat(10) @(negedge clk);
        $display("HARNESS_DONE outputs=%0d cycles=%0d",outputs,cycle);
        $fclose(trace); $finish;
    end
endmodule

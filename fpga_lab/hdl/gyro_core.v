// Programmable seven-FIR MEMS gyro controller. See docs/gyro-design.md.
// Verilog-2005, one shared signed multiplier, clock enables, no gated clocks.
`default_nettype none
module gyro_core (
    input wire clk, input wire rst,
    input wire in_valid, output wire in_ready,
    input wire signed [17:0] adc_a, adc_b,
    output reg out_valid, input wire out_ready,
    output reg signed [17:0] actuator_x, actuator_y, rate_i, rate_q, agc_gain,
    input wire cfg_valid, output wire cfg_ready,
    input wire [10:0] cfg_addr, input wire signed [23:0] cfg_wdata,
    output reg cfg_error
);
    localparam CLEAR=0, IDLE=1, EXEC=2, FIR_LOAD=3, FIR_MAC=4,
               SCALAR_MUL=5, SCALAR_COMMIT=6, FIR_PRIME=7, FIR_DRAIN=8;
    localparam MUL=0, ADD=1, ABS=2, FIR=3, ERROR_OP=4, INTEGRATE=5, PI=6, OUTPUT_OP=7;
    reg [3:0] state;
    reg [5:0] pc;
    reg [9:0] clear_addr;
    reg signed [21:0] r [0:31];
    reg signed [17:0] coeff [0:895];
    reg signed [21:0] history [0:895];
    reg [6:0] head [0:6];
    reg [6:0] last_tap [0:6];
    reg signed [21:0] reference, u_min, u_max;
    reg signed [23:0] integral, i_min, i_max;
    reg [2:0] bank;
    reg [6:0] tap, read_index;
    reg [4:0] fir_dest;
    reg signed [48:0] accumulator;
    reg signed [21:0] sample_reg;
    reg signed [17:0] coefficient_reg;
    reg [2:0] op, filter_id;
    reg [4:0] dst, src_a, src_b;
    reg signed [21:0] mul_a, mul_b;
    reg signed [21:0] scalar_a, scalar_b;
    reg signed [43:0] product_reg;
    wire signed [43:0] product = mul_a * mul_b;
    wire signed [48:0] product_ext = {{5{product_reg[43]}},product_reg};
    wire signed [48:0] fir_sum = accumulator + product_ext;
    wire signed [48:0] a_ext = {{27{r[src_a][21]}},r[src_a]};
    wire signed [48:0] b_ext = {{27{r[src_b][21]}},r[src_b]};
    wire signed [48:0] integral_ext = {{25{integral[23]}},integral};
    wire signed [48:0] integral_next = integral_ext + (product_ext >>> 16);
    wire signed [48:0] i_min_ext = {{25{i_min[23]}},i_min};
    wire signed [48:0] i_max_ext = {{25{i_max[23]}},i_max};
    wire signed [48:0] pi_sum = integral_ext + {{27{r[7][21]}},r[7]};
    wire signed [21:0] pi_value = sat22(pi_sum);
    wire signed [48:0] ref_ext = {{27{reference[21]}},reference};
    wire signed [48:0] env_ext = {{27{r[5][21]}},r[5]};
    wire [9:0] bank_base = {bank,7'b0};
    wire [6:0] next_index = read_index - 7'd1;
    assign cfg_ready = state == IDLE && !out_valid;
    assign in_ready = cfg_ready && !cfg_valid;

    function signed [21:0] sat22;
        input signed [48:0] value;
        begin
            if (value > 49'sd2097151) sat22 = 22'sd2097151;
            else if (value < -49'sd2097152) sat22 = -22'sd2097152;
            else sat22 = value[21:0];
        end
    endfunction
    function signed [17:0] sat18;
        input signed [21:0] value;
        begin
            if (value > 22'sd131071) sat18 = 18'sd131071;
            else if (value < -22'sd131072) sat18 = -18'sd131072;
            else sat18 = value[17:0];
        end
    endfunction

    // Fixed algorithm schedule. Register 22..31 = K1..K8,Kp,Ki.
    always @* begin
        op=MUL; dst=20; src_a=0; src_b=22; filter_id=0;
        case (pc)
             0: begin op=MUL; dst=20; src_a=0; src_b=22; end
             1: begin op=MUL; dst=21; src_a=1; src_b=23; end
             2: begin op=ADD; dst=2; src_a=20; src_b=21; end
             3: begin op=MUL; dst=20; src_a=0; src_b=26; end
             4: begin op=MUL; dst=21; src_a=1; src_b=27; end
             5: begin op=ADD; dst=3; src_a=20; src_b=21; end
             6: begin op=FIR; dst=4; src_a=2; filter_id=0; end
             7: begin op=ABS; dst=20; src_a=4; end
             8: begin op=FIR; dst=5; src_a=20; filter_id=1; end
             9: begin op=ERROR_OP; dst=6; end
            10: begin op=MUL; dst=7; src_a=6; src_b=30; end
            11: begin op=INTEGRATE; src_a=6; src_b=31; end
            12: begin op=PI; dst=9; end
            13: begin op=MUL; dst=10; src_a=4; src_b=9; end
            14: begin op=FIR; dst=11; src_a=3; filter_id=2; end
            15: begin op=FIR; dst=12; src_a=11; filter_id=3; end
            16: begin op=FIR; dst=13; src_a=2; filter_id=4; end
            17: begin op=MUL; dst=14; src_a=12; src_b=2; end
            18: begin op=MUL; dst=15; src_a=12; src_b=13; end
            19: begin op=FIR; dst=16; src_a=14; filter_id=5; end
            20: begin op=FIR; dst=17; src_a=15; filter_id=6; end
            21: begin op=MUL; dst=20; src_a=10; src_b=24; end
            22: begin op=MUL; dst=21; src_a=11; src_b=28; end
            23: begin op=ADD; dst=18; src_a=20; src_b=21; end
            24: begin op=MUL; dst=20; src_a=10; src_b=25; end
            25: begin op=MUL; dst=21; src_a=11; src_b=29; end
            26: begin op=ADD; dst=19; src_a=20; src_b=21; end
            default: op=OUTPUT_OP;
        endcase
        // Isolate multiplier inputs while idle and for nonmultiplying operations.
        mul_a=0; mul_b=0;
        if (state == FIR_MAC || state == FIR_PRIME) begin
            mul_a=sample_reg; mul_b={{4{coefficient_reg[17]}},coefficient_reg};
        end else if (state == SCALAR_MUL) begin
            mul_a=scalar_a; mul_b=scalar_b;
        end
    end

    integer j;
    always @(posedge clk) begin
        if (rst) begin
            state<=CLEAR; clear_addr<=0; pc<=0; out_valid<=0; cfg_error<=0;
            actuator_x<=0; actuator_y<=0; rate_i<=0; rate_q<=0; agc_gain<=0;
            reference<=0; integral<=0;
            i_min<=-24'sd8388608; i_max<=24'sd8388607;
            u_min<=-22'sd2097152; u_max<=22'sd2097151;
            bank<=0; tap<=0; read_index<=0; fir_dest<=0;
            accumulator<=0; sample_reg<=0; coefficient_reg<=0;
            scalar_a<=0; scalar_b<=0; product_reg<=0;
            for (j=0;j<32;j=j+1) r[j]<=0;
            for (j=0;j<7;j=j+1) begin head[j]<=0; last_tap[j]<=0; end
        end else begin
            cfg_error<=0;
            if (out_valid && out_ready) out_valid<=0;
            case (state)
                CLEAR: begin
                    // Sequential initialization avoids reset on every memory bit.
                    coeff[clear_addr]<=0; history[clear_addr]<=0;
                    if (clear_addr == 895) state<=IDLE;
                    else clear_addr<=clear_addr+1'b1;
                end
                IDLE: begin
                    if (cfg_valid && cfg_ready) begin
                        if (cfg_addr < 896) coeff[cfg_addr[9:0]]<=cfg_wdata[17:0];
                        else if (cfg_addr >= 11'h400 && cfg_addr <= 11'h409)
                            r[5'd22+cfg_addr[3:0]]<={{4{cfg_wdata[17]}},cfg_wdata[17:0]};
                        else if (cfg_addr >= 11'h420 && cfg_addr <= 11'h426)
                            last_tap[cfg_addr[2:0]]<=cfg_wdata[6:0];
                        else case (cfg_addr)
                            11'h40a: reference<=cfg_wdata[21:0];
                            11'h40b: i_min<=cfg_wdata;
                            11'h40c: i_max<=cfg_wdata;
                            11'h40d: u_min<=cfg_wdata[21:0];
                            11'h40e: u_max<=cfg_wdata[21:0];
                            default: cfg_error<=1;
                        endcase
                    end else if (in_valid && in_ready) begin
                        r[0]<={{4{adc_a[17]}},adc_a}; r[1]<={{4{adc_b[17]}},adc_b};
                        pc<=0; state<=EXEC;
                    end
                end
                EXEC: begin
                    pc<=pc+1'b1;
                    case (op)
                        MUL, INTEGRATE: begin
                            // Separate instruction decode/register reads, multiply,
                            // and saturation/writeback to close slow-corner timing.
                            scalar_a<=r[src_a]; scalar_b<=r[src_b];
                            pc<=pc; state<=SCALAR_MUL;
                        end
                        ADD: r[dst]<=sat22(a_ext+b_ext);
                        ABS: r[dst]<=sat22(a_ext < 0 ? -a_ext : a_ext);
                        ERROR_OP: r[dst]<=sat22(ref_ext-env_ext);
                        PI: begin
                            if (pi_value < u_min) r[dst]<=u_min;
                            else if (pi_value > u_max) r[dst]<=u_max;
                            else r[dst]<=pi_value;
                        end
                        FIR: begin
                            bank<=filter_id; fir_dest<=dst; tap<=0; accumulator<=0;
                            history[{filter_id,head[filter_id]}]<=r[src_a];
                            read_index<=head[filter_id];
                            state<=FIR_LOAD;
                        end
                        OUTPUT_OP: begin
                            actuator_x<=sat18(r[18]); actuator_y<=sat18(r[19]);
                            rate_i<=sat18(r[16]); rate_q<=sat18(r[17]); agc_gain<=sat18(r[9]);
                            out_valid<=1; state<=IDLE;
                        end
                        default: state<=IDLE;
                    endcase
                end
                FIR_LOAD: begin
                    sample_reg<=history[bank_base+{3'b0,read_index}];
                    coefficient_reg<=coeff[bank_base];
                    state<=FIR_PRIME;
                end
                FIR_PRIME: begin
                    product_reg<=product;
                    if (last_tap[bank] == 0) state<=FIR_DRAIN;
                    else begin
                        tap<=1; read_index<=next_index;
                        sample_reg<=history[bank_base+{3'b0,next_index}];
                        coefficient_reg<=coeff[bank_base+10'd1];
                        state<=FIR_MAC;
                    end
                end
                FIR_MAC: begin
                    // One product per cycle after priming. The accumulator uses
                    // the previous registered product while the next is computed.
                    product_reg<=product; accumulator<=fir_sum;
                    if (tap == last_tap[bank]) begin
                        state<=FIR_DRAIN;
                    end else begin
                        tap<=tap+1'b1; read_index<=next_index;
                        sample_reg<=history[bank_base+{3'b0,next_index}];
                        coefficient_reg<=coeff[bank_base+{3'b0,tap}+10'd1];
                    end
                end
                FIR_DRAIN: begin
                    r[fir_dest]<=sat22(fir_sum >>> 16);
                    head[bank]<=head[bank]+1'b1;
                    state<=EXEC;
                end
                SCALAR_MUL: begin product_reg<=product; state<=SCALAR_COMMIT; end
                SCALAR_COMMIT: begin
                    if (op == INTEGRATE) begin
                        if (integral_next < i_min_ext) integral<=i_min;
                        else if (integral_next > i_max_ext) integral<=i_max;
                        else integral<=integral_next[23:0];
                    end else r[dst]<=sat22(product_ext >>> 16);
                    pc<=pc+1'b1; state<=EXEC;
                end
                default: state<=CLEAR;
            endcase
        end
    end
endmodule
`default_nettype wire

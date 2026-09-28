module fp_bandpass_fir #(
    parameter TAP_CNT = 51,
    parameter PIPE_DEPTH = 2
) (
    input clk,
    input rst,
    input valid_in,
    input [31:0] data_in,
    output valid_out,
    output [31:0] data_out
);
    function integer ceil_log2;
        input integer n;
        integer v;
        begin
            v = n - 1;
            ceil_log2 = 0;
            while (v > 0) begin
                ceil_log2 = ceil_log2 + 1;
                v = v >> 1;
            end
        end
    endfunction

    localparam integer DEPTH = (PIPE_DEPTH < 1) ? 1 : PIPE_DEPTH;
    localparam integer LEVELS = ceil_log2(TAP_CNT);
    localparam integer LATENCY = DEPTH * (LEVELS + 1);

    reg [31:0] coeffs_mem [0:TAP_CNT-1];
    reg [31:0] samples [0:TAP_CNT-1];
    integer ci;
    initial begin
        for (ci = 0; ci < TAP_CNT; ci = ci + 1)
            coeffs_mem[ci] = 32'h00000000;
        coeffs_mem[TAP_CNT/2] = 32'h3f000000;
        coeffs_mem[TAP_CNT/2-2] = 32'hbe800000;
        coeffs_mem[TAP_CNT/2+2] = 32'hbe800000;
    end

    reg [LATENCY-1:0] valid_pipe;
    integer si, vi;
    always @(posedge clk) begin
        if (rst) begin
            valid_pipe <= 0;
            for (si = 0; si < TAP_CNT; si = si + 1)
                samples[si] <= 32'h00000000;
        end else begin
            if (valid_in) begin
                samples[0] <= data_in;
                for (si = 1; si < TAP_CNT; si = si + 1)
                    samples[si] <= samples[si-1];
            end
            valid_pipe[0] <= valid_in;
            for (vi = 1; vi < LATENCY; vi = vi + 1)
                valid_pipe[vi] <= valid_pipe[vi-1];
        end
    end

    // Flattened wire indexing also works with Verilog-2005 frontends.
    wire [31:0] tree_data [0:(LEVELS+1)*TAP_CNT-1];
    genvar tap, level, node;
    generate
        for (tap = 0; tap < TAP_CNT; tap = tap + 1) begin : multipliers
            wire [31:0] sample_value;
            if (tap == 0) begin : newest
                assign sample_value = data_in;
            end else begin : historical
                assign sample_value = samples[tap-1];
            end
            fp_bandpass_fir_operator #(.OP(1), .DEPTH(DEPTH)) mul (
                .clk(clk), .rst(rst), .a(sample_value),
                .b(coeffs_mem[tap]), .q(tree_data[tap])
            );
        end
        for (level = 1; level <= LEVELS; level = level + 1) begin : tree_levels
            localparam integer PREVIOUS_NODES =
                (TAP_CNT + (1 << (level-1)) - 1) >> (level-1);
            localparam integer NODES =
                (TAP_CNT + (1 << level) - 1) >> level;
            for (node = 0; node < NODES; node = node + 1) begin : adders
                if (2*node+1 < PREVIOUS_NODES) begin : pair
                    fp_bandpass_fir_operator #(.OP(0), .DEPTH(DEPTH)) add (
                        .clk(clk), .rst(rst),
                        .a(tree_data[(level-1)*TAP_CNT+2*node]),
                        .b(tree_data[(level-1)*TAP_CNT+2*node+1]),
                        .q(tree_data[level*TAP_CNT+node])
                    );
                end else begin : unpaired
                    fp_bandpass_fir_operator #(.OP(2), .DEPTH(DEPTH)) delay_op (
                        .clk(clk), .rst(rst),
                        .a(tree_data[(level-1)*TAP_CNT+2*node]),
                        .b(32'b0), .q(tree_data[level*TAP_CNT+node])
                    );
                end
            end
        end
    endgenerate

    assign valid_out = valid_pipe[LATENCY-1];
    assign data_out = tree_data[LEVELS*TAP_CNT];
endmodule

// Preserve operator boundaries to prevent costly cross-tap resource sharing.
(* keep_hierarchy = "yes" *)
module fp_bandpass_fir_operator #(
    parameter OP = 0,
    parameter DEPTH = 2
) (
    input clk,
    input rst,
    input [31:0] a,
    input [31:0] b,
    output [31:0] q
);
    // Fixed-distance mux stages avoid generic variable-shift/mask operators.
    function [26:0] shift_jam;
        input [26:0] value;
        input signed [10:0] distance;
        reg [26:0] s;
        begin
            s = value;
            if (distance <= 0) begin
                shift_jam = value;
            end else if (distance >= 27) begin
                shift_jam = {26'b0, |value};
            end else begin
                if (distance[4]) s = {16'b0, s[26:17], |s[16:0]};
                if (distance[3]) s = {8'b0, s[26:9], |s[8:0]};
                if (distance[2]) s = {4'b0, s[26:5], |s[4:0]};
                if (distance[1]) s = {2'b0, s[26:3], |s[2:0]};
                if (distance[0]) s = {1'b0, s[26:2], |s[1:0]};
                shift_jam = s;
            end
        end
    endfunction

    function [31:0] pack_float;
        input sign_bit;
        input signed [10:0] exponent_in;
        input [26:0] significand_in;
        reg signed [10:0] exponent_value;
        reg [26:0] significand;
        reg [24:0] rounded;
        reg [7:0] encoded_exponent;
        begin
            exponent_value = exponent_in;
            significand = significand_in;
            encoded_exponent = 0;
            if (exponent_value < -11'sd126) begin
                significand = shift_jam(significand, -11'sd126-exponent_value);
                exponent_value = -11'sd126;
            end
            rounded = {1'b0, significand[26:3]};
            if (significand[2] &&
                (significand[1] || significand[0] || significand[3]))
                rounded = rounded + 25'd1;
            if (rounded[24]) begin
                rounded = {1'b0, rounded[24:1]};
                exponent_value = exponent_value + 11'sd1;
            end
            if (exponent_value > 11'sd127)
                pack_float = {sign_bit, 8'hff, 23'b0};
            else if (rounded == 0)
                pack_float = {sign_bit, 31'b0};
            else if ((exponent_value == -11'sd126) && !rounded[23])
                pack_float = {sign_bit, 8'h00, rounded[22:0]};
            else begin
                encoded_exponent = exponent_value + 11'sd127;
                pack_float = {sign_bit, encoded_exponent, rounded[22:0]};
            end
        end
    endfunction

    function [31:0] fp_multiply;
        input [31:0] x;
        input [31:0] y;
        reg x_nan, y_nan, x_inf, y_inf, x_zero, y_zero;
        reg sign_result;
        reg [23:0] mx, my;
        reg [4:0] shift_x, shift_y;
        reg [47:0] product;
        reg [26:0] significand;
        reg signed [10:0] ex, ey, exponent_value;
        integer i;
        begin
            x_nan = (&x[30:23]) && (|x[22:0]);
            y_nan = (&y[30:23]) && (|y[22:0]);
            x_inf = (&x[30:23]) && !(|x[22:0]);
            y_inf = (&y[30:23]) && !(|y[22:0]);
            x_zero = !(|x[30:0]);
            y_zero = !(|y[30:0]);
            sign_result = x[31] ^ y[31];
            mx = {(x[30:23] != 0), x[22:0]};
            my = {(y[30:23] != 0), y[22:0]};
            ex = (x[30:23] == 0) ? -11'sd126 :
                 ($signed({3'b0, x[30:23]}) - 11'sd127);
            ey = (y[30:23] == 0) ? -11'sd126 :
                 ($signed({3'b0, y[30:23]}) - 11'sd127);
            shift_x = 0;
            shift_y = 0;
            for (i = 0; i < 24; i = i + 1) begin
                if (mx[i]) shift_x = 23-i;
                if (my[i]) shift_y = 23-i;
            end
            mx = mx << shift_x;
            my = my << shift_y;
            ex = ex - $signed({6'b0, shift_x});
            ey = ey - $signed({6'b0, shift_y});
            product = mx * my;
            exponent_value = ex + ey;
            if (product[47]) begin
                significand = product[47:21];
                significand[0] = significand[0] | (|product[20:0]);
                exponent_value = exponent_value + 11'sd1;
            end else begin
                significand = product[46:20];
                significand[0] = significand[0] | (|product[19:0]);
            end
            if (x_nan || y_nan || (x_inf && y_zero) || (y_inf && x_zero))
                fp_multiply = 32'h7fc00000;
            else if (x_inf || y_inf)
                fp_multiply = {sign_result, 8'hff, 23'b0};
            else if (x_zero || y_zero)
                fp_multiply = {sign_result, 31'b0};
            else
                fp_multiply = pack_float(sign_result, exponent_value, significand);
        end
    endfunction

    function [31:0] fp_add;
        input [31:0] x;
        input [31:0] y;
        reg x_nan, y_nan, x_inf, y_inf;
        reg [23:0] mx, my;
        reg [26:0] large_sig, small_sig, significand;
        reg [27:0] sum;
        reg sign_result;
        reg [4:0] normalize_shift;
        reg signed [10:0] ex, ey, exponent_value, distance;
        integer i;
        begin
            x_nan = (&x[30:23]) && (|x[22:0]);
            y_nan = (&y[30:23]) && (|y[22:0]);
            x_inf = (&x[30:23]) && !(|x[22:0]);
            y_inf = (&y[30:23]) && !(|y[22:0]);
            mx = {(x[30:23] != 0), x[22:0]};
            my = {(y[30:23] != 0), y[22:0]};
            ex = (x[30:23] == 0) ? -11'sd126 :
                 ($signed({3'b0, x[30:23]}) - 11'sd127);
            ey = (y[30:23] == 0) ? -11'sd126 :
                 ($signed({3'b0, y[30:23]}) - 11'sd127);
            if ((ex > ey) || ((ex == ey) && (mx >= my))) begin
                large_sig = {mx, 3'b0};
                small_sig = {my, 3'b0};
                exponent_value = ex;
                distance = ex - ey;
                sign_result = x[31];
            end else begin
                large_sig = {my, 3'b0};
                small_sig = {mx, 3'b0};
                exponent_value = ey;
                distance = ey - ex;
                sign_result = y[31];
            end
            small_sig = shift_jam(small_sig, distance);
            sum = 0;
            if (x[31] == y[31]) begin
                sum = {1'b0, large_sig} + {1'b0, small_sig};
                if (sum[27]) begin
                    significand = {sum[27:2], (sum[1] | sum[0])};
                    exponent_value = exponent_value + 11'sd1;
                end else begin
                    significand = sum[26:0];
                end
            end else begin
                significand = large_sig - small_sig;
                if (significand == 0) sign_result = 1'b0;
            end
            normalize_shift = 0;
            for (i = 0; i < 27; i = i + 1)
                if (significand[i]) normalize_shift = 26-i;
            significand = significand << normalize_shift;
            exponent_value = exponent_value - $signed({6'b0, normalize_shift});
            if (x_nan || y_nan || (x_inf && y_inf && (x[31] != y[31])))
                fp_add = 32'h7fc00000;
            else if (x_inf)
                fp_add = {x[31], 8'hff, 23'b0};
            else if (y_inf)
                fp_add = {y[31], 8'hff, 23'b0};
            else
                fp_add = pack_float(sign_result, exponent_value, significand);
        end
    endfunction

    wire [31:0] result;
    generate
        if (OP == 1) begin : multiply_operation
            assign result = fp_multiply(a, b);
        end else if (OP == 0) begin : add_operation
            assign result = fp_add(a, b);
        end else begin : bypass_operation
            assign result = a;
        end
    endgenerate

    reg [31:0] pipe_data [0:DEPTH-1];
    integer p;
    always @(posedge clk) begin
        if (rst) begin
            for (p = 0; p < DEPTH; p = p + 1)
                pipe_data[p] <= 32'b0;
        end else begin
            pipe_data[0] <= result;
            for (p = 1; p < DEPTH; p = p + 1)
                pipe_data[p] <= pipe_data[p-1];
        end
    end
    assign q = pipe_data[DEPTH-1];
endmodule

module fp_highpass_fir #(
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

    localparam LEVELS = ceil_log2(TAP_CNT);
    localparam LEAVES = 1 << LEVELS;
    localparam DEPTH = (PIPE_DEPTH < 1) ? 1 : PIPE_DEPTH;
    localparam LATENCY = DEPTH * (LEVELS + 1);

    reg [31:0] coeffs_mem [0:TAP_CNT-1];
    reg [31:0] delay_line [0:TAP_CNT-1];
    reg [LATENCY-1:0] valid_pipe;
    integer i;
    integer c;
    wire [31:0] tree [1:2*LEAVES-1];

    function [31:0] coefficient;
        input integer numerator;
        input integer denominator;
        input negative;
        reg [63:0] q, truncated, remainder_bits, halfway;
        integer k, j, shift_count, biased_exp;
        begin
            q = (64'd1 * numerator << 48) / denominator;
            k = 0;
            for (j = 0; j < 64; j = j + 1)
                if (q[j]) k = j;
            shift_count = k - 23;
            truncated = q >> shift_count;
            remainder_bits = q & ((64'd1 << shift_count) - 1);
            halfway = 64'd1 << (shift_count - 1);
            if ((remainder_bits > halfway) ||
                ((remainder_bits == halfway) && truncated[0]))
                truncated = truncated + 1;
            biased_exp = k - 48 + 127;
            if (truncated[24]) begin
                truncated = truncated >> 1;
                biased_exp = biased_exp + 1;
            end
            coefficient = {negative, biased_exp[7:0], truncated[22:0]};
        end
    endfunction

    // Default coefficients: spectral inversion of a moving average.
    // Application coefficients can be preloaded into coeffs_mem.
    initial begin
        for (c = 0; c < TAP_CNT; c = c + 1)
            if (c == (TAP_CNT-1)/2)
                coeffs_mem[c] = coefficient(TAP_CNT-1, TAP_CNT, 1'b0);
            else
                coeffs_mem[c] = coefficient(1, TAP_CNT, 1'b1);
    end

    always @(posedge clk) begin
        if (rst) begin
            valid_pipe <= {LATENCY{1'b0}};
            for (i = 0; i < TAP_CNT; i = i + 1)
                delay_line[i] <= 32'b0;
        end else begin
            // Every accepted input has an output, including the
            // initial zero-extended convolution samples.
            valid_pipe[0] <= valid_in;
            for (i = 1; i < LATENCY; i = i + 1)
                valid_pipe[i] <= valid_pipe[i-1];
            if (valid_in) begin
                delay_line[0] <= data_in;
                for (i = 1; i < TAP_CNT; i = i + 1)
                    delay_line[i] <= delay_line[i-1];
            end
        end
    end

    genvar g;
    generate
        for (g = 0; g < LEAVES; g = g + 1) begin : products
            if (g < TAP_CNT) begin : active_tap
                wire [31:0] sample_value;
                if (g == 0) begin : newest
                    assign sample_value = data_in;
                end else begin : historical
                    assign sample_value = delay_line[g-1];
                end
                fp_highpass_fir_operator #(.MULTIPLY(1), .DEPTH(DEPTH)) mul (
                    .clk(clk), .rst(rst), .a(sample_value),
                    .b(coeffs_mem[g]), .y(tree[LEAVES+g])
                );
            end else begin : padding
                assign tree[LEAVES+g] = 32'b0;
            end
        end
        for (g = 1; g < LEAVES; g = g + 1) begin : sums
            fp_highpass_fir_operator #(.MULTIPLY(0), .DEPTH(DEPTH)) add (
                .clk(clk), .rst(rst), .a(tree[2*g]),
                .b(tree[2*g+1]), .y(tree[g])
            );
        end
    endgenerate

    assign data_out = tree[1];
    assign valid_out = valid_pipe[LATENCY-1];
endmodule

module fp_highpass_fir_operator #(
    parameter MULTIPLY = 0,
    parameter DEPTH = 2
) (
    input clk,
    input rst,
    input [31:0] a,
    input [31:0] b,
    output [31:0] y
);
    // Round magnitude * 2**base_exp using round-to-nearest, ties-to-even.
    function [31:0] pack_float;
        input sign_value;
        input [277:0] magnitude;
        input integer base_exp;
        integer highest, exponent_value, shift_count, j;
        reg guard_bit, sticky_bit;
        reg [277:0] shifted;
        reg [24:0] rounded;
        reg [7:0] encoded_exp;
        begin
            highest = -1;
            for (j = 0; j < 278; j = j + 1)
                if (magnitude[j]) highest = j;
            pack_float = {sign_value, 31'b0};
            shifted = 0;
            rounded = 0;
            guard_bit = 0;
            sticky_bit = 0;
            encoded_exp = 0;
            exponent_value = 0;
            shift_count = 0;
            if (highest >= 0) begin
                exponent_value = highest + base_exp;
                if (exponent_value < -126)
                    shift_count = -149 - base_exp;
                else
                    shift_count = highest - 23;
                if (shift_count > 0) begin
                    shifted = magnitude >> shift_count;
                    if (shift_count <= 278)
                        guard_bit = magnitude[shift_count-1];
                    for (j = 0; j < 278; j = j + 1)
                        if (j < shift_count-1)
                            sticky_bit = sticky_bit | magnitude[j];
                end else begin
                    shifted = magnitude << (-shift_count);
                end
                rounded = {1'b0, shifted[23:0]};
                if (guard_bit && (sticky_bit || rounded[0]))
                    rounded = rounded + 25'd1;
                if (exponent_value < -126) begin
                    if (rounded[23])
                        pack_float = {sign_value, 8'd1, 23'b0};
                    else
                        pack_float = {sign_value, 8'd0, rounded[22:0]};
                end else begin
                    if (rounded[24]) begin
                        rounded = rounded >> 1;
                        exponent_value = exponent_value + 1;
                    end
                    if (exponent_value > 127)
                        pack_float = {sign_value, 8'hff, 23'b0};
                    else begin
                        encoded_exp = exponent_value + 127;
                        pack_float = {sign_value, encoded_exp, rounded[22:0]};
                    end
                end
            end
        end
    endfunction

    function [31:0] multiply_float;
        input [31:0] x, z;
        reg [23:0] mx, mz;
        reg [47:0] product;
        integer ex, ez;
        reg s;
        begin
            s = x[31] ^ z[31];
            mx = {(x[30:23] != 0), x[22:0]};
            mz = {(z[30:23] != 0), z[22:0]};
            ex = (x[30:23] == 0) ? -149 : (x[30:23] - 150);
            ez = (z[30:23] == 0) ? -149 : (z[30:23] - 150);
            product = mx * mz;
            if (((x[30:23] == 255) && (x[22:0] != 0)) ||
                ((z[30:23] == 255) && (z[22:0] != 0)))
                multiply_float = 32'h7fc00000;
            else if (((x[30:23] == 255) && (z[30:0] == 0)) ||
                     ((z[30:23] == 255) && (x[30:0] == 0)))
                multiply_float = 32'h7fc00000;
            else if ((x[30:23] == 255) || (z[30:23] == 255))
                multiply_float = {s, 8'hff, 23'b0};
            else
                multiply_float = pack_float(s, {{230{1'b0}}, product}, ex + ez);
        end
    endfunction

    function [31:0] add_float;
        input [31:0] x, z;
        reg [277:0] mx, mz, magnitude;
        reg s;
        integer sx, sz;
        begin
            // Exact alignment in units of the smallest binary32 subnormal.
            mx = {{254{1'b0}}, (x[30:23] != 0), x[22:0]};
            mz = {{254{1'b0}}, (z[30:23] != 0), z[22:0]};
            sx = (x[30:23] == 0) ? 0 : x[30:23] - 1;
            sz = (z[30:23] == 0) ? 0 : z[30:23] - 1;
            mx = mx << sx;
            mz = mz << sz;
            magnitude = 0;
            s = 0;
            if (x[31] == z[31]) begin
                magnitude = mx + mz;
                s = x[31];
            end else if (mx > mz) begin
                magnitude = mx - mz;
                s = x[31];
            end else if (mz > mx) begin
                magnitude = mz - mx;
                s = z[31];
            end
            if (((x[30:23] == 255) && (x[22:0] != 0)) ||
                ((z[30:23] == 255) && (z[22:0] != 0)))
                add_float = 32'h7fc00000;
            else if ((x[30:23] == 255) && (z[30:23] == 255) &&
                     (x[31] != z[31]))
                add_float = 32'h7fc00000;
            else if (x[30:23] == 255)
                add_float = x;
            else if (z[30:23] == 255)
                add_float = z;
            else
                add_float = pack_float(s, magnitude, -149);
        end
    endfunction

    wire [31:0] result;
    reg [31:0] pipeline [0:DEPTH-1];
    integer p;
    generate
        if (MULTIPLY != 0) begin : multiply_operation
            assign result = multiply_float(a, b);
        end else begin : add_operation
            assign result = add_float(a, b);
        end
    endgenerate
    always @(posedge clk) begin
        if (rst) begin
            for (p = 0; p < DEPTH; p = p + 1)
                pipeline[p] <= 32'b0;
        end else begin
            pipeline[0] <= result;
            for (p = 1; p < DEPTH; p = p + 1)
                pipeline[p] <= pipeline[p-1];
        end
    end
    assign y = pipeline[DEPTH-1];
endmodule

module fft64_streaming #(
    parameter DATA_W = 16,
    parameter POINTS = 64,
    parameter GROWTH = 4
) (
    input clk,
    input rst,
    input [DATA_W-1:0] real_in,
    input [DATA_W-1:0] imag_in,
    input valid_in,
    input last_in,
    output reg [DATA_W+GROWTH-1:0] real_out,
    output reg [DATA_W+GROWTH-1:0] imag_out,
    output reg valid_out,
    output reg last_out,
    output reg done
);
    localparam OUT_W = DATA_W + GROWTH;
    localparam STAGES = $clog2(POINTS);
    localparam CW = (STAGES > 0) ? STAGES : 1;

    function integer quarter_sine64;
        input integer index;
        begin
            case (index)
                0: quarter_sine64 = 0;
                1: quarter_sine64 = 3212;
                2: quarter_sine64 = 6393;
                3: quarter_sine64 = 9512;
                4: quarter_sine64 = 12540;
                5: quarter_sine64 = 15447;
                6: quarter_sine64 = 18205;
                7: quarter_sine64 = 20788;
                8: quarter_sine64 = 23170;
                9: quarter_sine64 = 25330;
                10: quarter_sine64 = 27246;
                11: quarter_sine64 = 28899;
                12: quarter_sine64 = 30274;
                13: quarter_sine64 = 31357;
                14: quarter_sine64 = 32138;
                15: quarter_sine64 = 32610;
                default: quarter_sine64 = 32768;
            endcase
        end
    endfunction

    function integer sine_q15;
        input integer phase;
        integer p, quadrant, offset, magnitude, j;
        reg signed [63:0] x, x_squared, term, accumulator;
        begin
            p = phase % POINTS;
            if (p < 0) p = p + POINTS;
            if (POINTS == 2) begin
                sine_q15 = 0;
            end else begin
                quadrant = p / (POINTS / 4);
                offset = p % (POINTS / 4);
                if (quadrant == 1 || quadrant == 3)
                    offset = POINTS / 4 - offset;
                if (POINTS == 64) begin
                    magnitude = quarter_sine64(offset);
                end else begin
                    x = (64'sd6746518852 * offset) / POINTS;
                    x_squared = (x * x) >>> 30;
                    term = x;
                    accumulator = x;
                    for (j = 1; j < 9; j = j + 1) begin
                        term = -((term * x_squared) >>> 30);
                        term = term / ((2*j) * (2*j+1));
                        accumulator = accumulator + term;
                    end
                    magnitude = (accumulator + 64'sd16384) >>> 15;
                    if (magnitude > 32768) magnitude = 32768;
                end
                if (quadrant >= 2)
                    sine_q15 = -magnitude;
                else
                    sine_q15 = magnitude;
            end
        end
    endfunction

    function [15:0] twiddle;
        input integer exponent;
        input integer imaginary_part;
        integer value;
        begin
            if (POINTS == 2) begin
                if (imaginary_part) value = 0;
                else value = 32767;
            end else if (imaginary_part)
                value = -sine_q15(exponent);
            else
                value = sine_q15(exponent + POINTS/4);
            if (value > 32767) value = 32767;
            if (value < -32768) value = -32768;
            twiddle = value;
        end
    endfunction

    function [CW-1:0] reverse_bits;
        input [CW-1:0] index;
        integer k;
        begin
            reverse_bits = 0;
            for (k = 0; k < STAGES; k = k + 1)
                reverse_bits[k] = index[STAGES-1-k];
        end
    endfunction

    // Acquire settled input tuples on the falling edge. The FFT and its
    // output interface advance on rising edges. No simulation delays are used.
    reg signed [DATA_W-1:0] sampled_real, sampled_imag;
    reg sampled_valid, sampled_last;
    always @(negedge clk) begin
        if (rst) begin
            sampled_real <= 0;
            sampled_imag <= 0;
            sampled_valid <= 1'b0;
            sampled_last <= 1'b0;
        end else begin
            sampled_real <= real_in;
            sampled_imag <= imag_in;
            sampled_valid <= valid_in;
            sampled_last <= last_in;
        end
    end

    reg signed [OUT_W-1:0] input_real [0:POINTS-1];
    reg signed [OUT_W-1:0] input_imag [0:POINTS-1];
    reg [CW-1:0] input_count;
    reg frame_ready;
    reg [STAGES-1:0] pipeline_valid;
    wire [STAGES:0] stage_valid;
    assign stage_valid = {pipeline_valid, frame_ready};

    wire signed [OUT_W-1:0] stage_real [0:STAGES][0:POINTS-1];
    wire signed [OUT_W-1:0] stage_imag [0:STAGES][0:POINTS-1];

    // A frame contains exactly POINTS valid samples; last_in is its
    // redundant final-sample marker. Invalid cycles pause collection.
    integer v;
    always @(posedge clk) begin
        if (rst) begin
            input_count <= 0;
            frame_ready <= 1'b0;
            pipeline_valid <= 0;
        end else begin
            frame_ready <= 1'b0;
            pipeline_valid[0] <= frame_ready;
            for (v = 1; v < STAGES; v = v + 1)
                pipeline_valid[v] <= pipeline_valid[v-1];
            if (sampled_valid) begin
                input_real[input_count] <= sampled_real;
                input_imag[input_count] <= sampled_imag;
                if (input_count == POINTS-1) begin
                    input_count <= 0;
                    frame_ready <= 1'b1;
                end else begin
                    input_count <= input_count + 1'b1;
                end
            end
        end
    end

    genvar n, s, b;
    generate
        for (n = 0; n < POINTS; n = n + 1) begin : input_connections
            assign stage_real[0][n] = input_real[n];
            assign stage_imag[0][n] = input_imag[n];
        end
        // Spatial radix-2 DIF stages run concurrently with collection and
        // serialization. Each stage has a dedicated register bank.
        for (s = 0; s < STAGES; s = s + 1) begin : fft_stages
            localparam SPAN = POINTS >> s;
            localparam HALF = SPAN >> 1;
            for (b = 0; b < POINTS/2; b = b + 1) begin : butterflies
                localparam A = (b / HALF) * SPAN + (b % HALF);
                localparam B = A + HALF;
                localparam EXPONENT = (b % HALF) * (POINTS / SPAN);
                localparam signed [15:0] WR = twiddle(EXPONENT, 0);
                localparam signed [15:0] WI = twiddle(EXPONENT, 1);
                wire signed [OUT_W-1:0] yr, yi, zr, zi;
                reg signed [OUT_W-1:0] yr_q, yi_q, zr_q, zi_q;

                fft64_dif_butterfly #(
                    .W(OUT_W), .SCALE(s < 3), .WR(WR), .WI(WI)
                ) butterfly (
                    .ar(stage_real[s][A]), .ai(stage_imag[s][A]),
                    .br(stage_real[s][B]), .bi(stage_imag[s][B]),
                    .yr(yr), .yi(yi), .zr(zr), .zi(zi)
                );

                always @(posedge clk) begin
                    if (!rst && stage_valid[s]) begin
                        yr_q <= yr;
                        yi_q <= yi;
                        zr_q <= zr;
                        zi_q <= zi;
                    end
                end
                assign stage_real[s+1][A] = yr_q;
                assign stage_imag[s+1][A] = yi_q;
                assign stage_real[s+1][B] = zr_q;
                assign stage_imag[s+1][B] = zi_q;
            end
        end
    endgenerate

    // The final register bank is also the reorder buffer. On consecutive
    // frames, its replacement coincides with reading the old final sample.
    reg output_active;
    reg [CW-1:0] output_count;
    wire [CW-1:0] output_address;
    assign output_address = reverse_bits(output_count);

    always @(posedge clk) begin
        if (rst) begin
            real_out <= 0;
            imag_out <= 0;
            valid_out <= 1'b0;
            last_out <= 1'b0;
            done <= 1'b0;
            output_active <= 1'b0;
            output_count <= 0;
        end else begin
            valid_out <= 1'b0;
            last_out <= 1'b0;
            done <= 1'b0;
            if (stage_valid[STAGES]) begin
                real_out <= stage_real[STAGES][0];
                imag_out <= stage_imag[STAGES][0];
                valid_out <= 1'b1;
                output_active <= 1'b1;
                output_count <= 1;
            end else if (output_active) begin
                real_out <= stage_real[STAGES][output_address];
                imag_out <= stage_imag[STAGES][output_address];
                valid_out <= 1'b1;
                if (output_count == POINTS-1) begin
                    last_out <= 1'b1;
                    done <= 1'b1;
                    output_active <= 1'b0;
                    output_count <= 0;
                end else begin
                    output_count <= output_count + 1'b1;
                end
            end
        end
    end
endmodule

module fft64_dif_butterfly #(
    parameter W = 20,
    parameter SCALE = 0,
    parameter signed [15:0] WR = 16'sh7fff,
    parameter signed [15:0] WI = 16'sh0000
) (
    input signed [W-1:0] ar,
    input signed [W-1:0] ai,
    input signed [W-1:0] br,
    input signed [W-1:0] bi,
    output signed [W-1:0] yr,
    output signed [W-1:0] yi,
    output signed [W-1:0] zr,
    output signed [W-1:0] zi
);
    wire signed [W:0] sum_r, sum_i, diff_r, diff_i;
    assign sum_r = $signed({ar[W-1], ar}) + $signed({br[W-1], br});
    assign sum_i = $signed({ai[W-1], ai}) + $signed({bi[W-1], bi});
    assign diff_r = $signed({ar[W-1], ar}) - $signed({br[W-1], br});
    assign diff_i = $signed({ai[W-1], ai}) - $signed({bi[W-1], bi});

    function signed [W+2:0] multiply_rne;
        input signed [W:0] x;
        input signed [15:0] coefficient;
        reg signed [W+16:0] product;
        reg signed [W+16:0] integral;
        begin
            product = x * coefficient;
            integral = product >>> 15;
            if ((product[14:0] > 15'd16384) ||
                ((product[14:0] == 15'd16384) && integral[0]))
                integral = integral + 1'b1;
            multiply_rne = integral;
        end
    endfunction

    localparam signed [W+2:0] MAXIMUM = {4'b0000, {(W-1){1'b1}}};
    localparam signed [W+2:0] MINIMUM = {4'b1111, {(W-1){1'b0}}};

    function signed [W-1:0] scale_and_saturate;
        input signed [W+2:0] x;
        reg signed [W+2:0] scaled;
        begin
            scaled = x;
            if (SCALE) begin
                scaled = x >>> 1;
                if (x[0] && scaled[0])
                    scaled = scaled + 1'b1;
            end
            if (scaled > MAXIMUM)
                scale_and_saturate = MAXIMUM[W-1:0];
            else if (scaled < MINIMUM)
                scale_and_saturate = MINIMUM[W-1:0];
            else
                scale_and_saturate = scaled[W-1:0];
        end
    endfunction

    wire signed [W+2:0] rr, ii, ri, ir;
    wire signed [W+2:0] rotated_r, rotated_i;
    wire signed [W+2:0] extended_sum_r, extended_sum_i;
    assign rr = multiply_rne(diff_r, WR);
    assign ii = multiply_rne(diff_i, WI);
    assign ri = multiply_rne(diff_r, WI);
    assign ir = multiply_rne(diff_i, WR);
    assign rotated_r = rr - ii;
    assign rotated_i = ri + ir;
    assign extended_sum_r = {{2{sum_r[W]}}, sum_r};
    assign extended_sum_i = {{2{sum_i[W]}}, sum_i};
    assign yr = scale_and_saturate(extended_sum_r);
    assign yi = scale_and_saturate(extended_sum_i);
    assign zr = scale_and_saturate(rotated_r);
    assign zi = scale_and_saturate(rotated_i);
endmodule

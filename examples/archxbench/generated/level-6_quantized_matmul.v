module qgemm #(
    parameter VLEN = 8,
    parameter K = 64,
    parameter FP_W = 32,
    parameter QBW = 8,
    parameter ACC_W = 32,
    parameter SCALE_W = 16,
    parameter SCALE_Q = 15
) (
    input clk,
    input rst,
    input [VLEN*K*FP_W-1:0] A_fp,
    input [K*VLEN*FP_W-1:0] B_fp,
    input [SCALE_W-1:0] scale_A,
    input [SCALE_W-1:0] scale_B,
    input [QBW-1:0] zp_A,
    input [QBW-1:0] zp_B,
    input start,
    output reg [VLEN*VLEN*FP_W-1:0] C_fp,
    output reg done
);
    localparam DIV_W = (SCALE_W + QBW + 26 > 64) ?
                       SCALE_W + QBW + 26 : 64;
    localparam OUT_W = ACC_W + 2*SCALE_W + 1;
    localparam IDLE = 2'd0, RUN = 2'd1, OUTPUT_RESULT = 2'd2;

    reg [1:0] state;
    reg [VLEN*K*FP_W-1:0] A_hold;
    reg [K*VLEN*FP_W-1:0] B_hold;
    reg [SCALE_W-1:0] sa, sb;
    reg [QBW-1:0] za, zb;
    reg signed [QBW-1:0] A_q [0:VLEN-1];
    reg signed [QBW-1:0] B_q [0:VLEN-1];
    reg signed [ACC_W-1:0] C_acc [0:VLEN*VLEN-1];
    reg quant_valid, quant_last;
    integer k_index;
    integer i, j;

    // Decode FP32 and divide its exact binary value by the fixed-point scale.
    // Round to nearest, ties to even, then add the signed zero point and clamp.
    function [QBW-1:0] quantize;
        input [31:0] fp;
        input [SCALE_W-1:0] scale;
        input [QBW-1:0] zp;
        reg [23:0] mant;
        reg [DIV_W-1:0] numerator, denominator, quotient, remainder;
        reg [DIV_W-1:0] limit_mag;
        reg signed [DIV_W:0] signed_value, signed_zp;
        reg signed [DIV_W:0] qmin, qmax;
        integer shift_count, right_count;
        begin
            numerator = 0;
            denominator = 0;
            quotient = 0;
            remainder = 0;
            limit_mag = {{(DIV_W-1){1'b0}}, 1'b1} << QBW;
            qmax = ({{DIV_W{1'b0}}, 1'b1} << (QBW-1)) - 1;
            qmin = -(qmax + 1);
            signed_zp = {{(DIV_W+1-QBW){zp[QBW-1]}}, zp};
            if (fp[30:23] == 0) begin
                mant = {1'b0, fp[22:0]};
                shift_count = SCALE_Q - 149;
            end else begin
                mant = {1'b1, fp[22:0]};
                shift_count = fp[30:23];
                shift_count = shift_count - 150 + SCALE_Q;
            end

            if (scale == 0) begin
                quotient = 0;
            end else if (fp[30:23] == 8'hff) begin
                if (fp[22:0] == 0)
                    quotient = limit_mag;
                else
                    quotient = 0;
            end else if (mant != 0) begin
                numerator = mant;
                denominator = scale;
                if (shift_count >= 0) begin
                    if (shift_count >= DIV_W-24) begin
                        quotient = limit_mag;
                    end else begin
                        numerator = numerator << shift_count;
                        quotient = numerator / denominator;
                        remainder = numerator % denominator;
                        if ((remainder > denominator-remainder) ||
                            ((remainder == denominator-remainder) && quotient[0]))
                            quotient = quotient + 1'b1;
                    end
                end else begin
                    right_count = -shift_count;
                    if (right_count >= DIV_W) begin
                        quotient = 0;
                    end else begin
                        denominator = denominator << right_count;
                        if ((denominator >> right_count) != scale) begin
                            quotient = 0;
                        end else begin
                            quotient = numerator / denominator;
                            remainder = numerator % denominator;
                            if ((remainder > denominator-remainder) ||
                                ((remainder == denominator-remainder) && quotient[0]))
                                quotient = quotient + 1'b1;
                        end
                    end
                end
            end
            if (quotient > limit_mag)
                quotient = limit_mag;
            signed_value = $signed({1'b0, quotient});
            if (fp[31])
                signed_value = -signed_value;
            signed_value = signed_value + signed_zp;
            if (signed_value > qmax)
                quantize = qmax[QBW-1:0];
            else if (signed_value < qmin)
                quantize = qmin[QBW-1:0];
            else
                quantize = signed_value[QBW-1:0];
        end
    endfunction

    function signed [2*QBW+1:0] centered_product;
        input [QBW-1:0] a, b, az, bz;
        reg signed [QBW:0] da, db;
        begin
            da = $signed({a[QBW-1], a}) - $signed({az[QBW-1], az});
            db = $signed({b[QBW-1], b}) - $signed({bz[QBW-1], bz});
            centered_product = da * db;
        end
    endfunction

    // Convert the exact scaled accumulator to IEEE-754 binary32 with RNE.
    function [31:0] dequantize;
        input signed [ACC_W-1:0] accumulator;
        input [SCALE_W-1:0] scale_a, scale_b;
        reg sign_bit;
        reg [ACC_W-1:0] absolute_acc;
        reg [OUT_W-1:0] magnitude, shifted;
        reg [24:0] rounded;
        reg guard_bit, sticky_bit;
        reg [7:0] exponent_bits;
        integer top_bit, exponent_value, shift_count, bit_index;
        begin
            sign_bit = accumulator[ACC_W-1];
            absolute_acc = accumulator;
            if (sign_bit)
                absolute_acc = (~absolute_acc) + 1'b1;
            magnitude = absolute_acc;
            magnitude = magnitude * scale_a;
            magnitude = magnitude * scale_b;
            shifted = 0;
            rounded = 0;
            guard_bit = 0;
            sticky_bit = 0;
            exponent_bits = 0;
            top_bit = -1;
            exponent_value = 0;
            shift_count = 0;
            dequantize = 32'b0;
            for (bit_index = 0; bit_index < OUT_W; bit_index = bit_index+1)
                if (magnitude[bit_index])
                    top_bit = bit_index;
            if (top_bit >= 0) begin
                exponent_value = top_bit - 2*SCALE_Q;
                if (exponent_value >= -126) begin
                    shift_count = top_bit - 23;
                    if (shift_count > 0) begin
                        shifted = magnitude >> shift_count;
                        guard_bit = magnitude[shift_count-1];
                        for (bit_index = 0; bit_index < OUT_W; bit_index = bit_index+1)
                            if (bit_index < shift_count-1)
                                sticky_bit = sticky_bit | magnitude[bit_index];
                    end else begin
                        shifted = magnitude << (-shift_count);
                    end
                    rounded = {1'b0, shifted[23:0]};
                    if (guard_bit && (sticky_bit || rounded[0]))
                        rounded = rounded + 1'b1;
                    if (rounded[24]) begin
                        rounded = rounded >> 1;
                        exponent_value = exponent_value + 1;
                    end
                    if (exponent_value > 127) begin
                        dequantize = {sign_bit, 8'hff, 23'b0};
                    end else begin
                        exponent_bits = exponent_value + 127;
                        dequantize = {sign_bit, exponent_bits, rounded[22:0]};
                    end
                end else begin
                    shift_count = 2*SCALE_Q - 149;
                    if (shift_count > 0) begin
                        shifted = magnitude >> shift_count;
                        if (shift_count <= OUT_W)
                            guard_bit = magnitude[shift_count-1];
                        for (bit_index = 0; bit_index < OUT_W; bit_index = bit_index+1)
                            if (bit_index < shift_count-1)
                                sticky_bit = sticky_bit | magnitude[bit_index];
                    end else begin
                        shifted = magnitude << (-shift_count);
                    end
                    rounded = {1'b0, shifted[23:0]};
                    if (guard_bit && (sticky_bit || rounded[0]))
                        rounded = rounded + 1'b1;
                    if (rounded[23])
                        dequantize = {sign_bit, 8'h01, 23'b0};
                    else
                        dequantize = {sign_bit, 8'h00, rounded[22:0]};
                end
            end
        end
    endfunction

    always @(posedge clk) begin
        if (rst) begin
            state <= IDLE;
            C_fp <= 0;
            done <= 0;
            k_index <= 0;
            quant_valid <= 0;
            quant_last <= 0;
            sa <= 0;
            sb <= 0;
            za <= 0;
            zb <= 0;
            for (i = 0; i < VLEN*VLEN; i = i+1)
                C_acc[i] <= 0;
        end else begin
            done <= 0;
            case (state)
                IDLE: begin
                    quant_valid <= 0;
                    if (start) begin
                        A_hold <= A_fp;
                        B_hold <= B_fp;
                        sa <= scale_A;
                        sb <= scale_B;
                        za <= zp_A;
                        zb <= zp_B;
                        k_index <= 0;
                        quant_last <= 0;
                        for (i = 0; i < VLEN*VLEN; i = i+1)
                            C_acc[i] <= 0;
                        state <= RUN;
                    end
                end
                RUN: begin
                    quant_valid <= 0;
                    if (k_index < K) begin
                        for (i = 0; i < VLEN; i = i+1) begin
                            A_q[i] <= quantize(A_hold[(i*K+k_index)*FP_W +: 32], sa, za);
                            B_q[i] <= quantize(B_hold[(k_index*VLEN+i)*FP_W +: 32], sb, zb);
                        end
                        quant_valid <= 1;
                        quant_last <= (k_index == K-1);
                        k_index <= k_index + 1;
                    end
                    if (quant_valid) begin
                        for (i = 0; i < VLEN; i = i+1)
                            for (j = 0; j < VLEN; j = j+1)
                                C_acc[i*VLEN+j] <= C_acc[i*VLEN+j] +
                                    centered_product(A_q[i], B_q[j], za, zb);
                        if (quant_last)
                            state <= OUTPUT_RESULT;
                    end
                end
                OUTPUT_RESULT: begin
                    for (i = 0; i < VLEN*VLEN; i = i+1)
                        C_fp[i*FP_W +: FP_W] <= dequantize(C_acc[i], sa, sb);
                    done <= 1;
                    quant_valid <= 0;
                    state <= IDLE;
                end
                default: begin
                    state <= IDLE;
                    quant_valid <= 0;
                end
            endcase
        end
    end
endmodule

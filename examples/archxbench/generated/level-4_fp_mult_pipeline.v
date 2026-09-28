module fp_mult_pipeline #(
    parameter LATENCY = 5
) (
    input clk,
    input rst,
    input [31:0] a,
    input [31:0] b,
    input valid_in,
    output [31:0] result,
    output valid_out
);

    function [67:0] unpack_operands;
        input [67:0] din;
        reg [31:0] x, y;
        reg sx, nx, ny, ix, iy, zx, zy;
        reg [23:0] mx, my;
        begin
            x = din[63:32];
            y = din[31:0];
            sx = x[31] ^ y[31];
            nx = (&x[30:23]) && (|x[22:0]);
            ny = (&y[30:23]) && (|y[22:0]);
            ix = (&x[30:23]) && !(|x[22:0]);
            iy = (&y[30:23]) && !(|y[22:0]);
            zx = !(|x[30:0]);
            zy = !(|y[30:0]);
            mx = {(|x[30:23]), x[22:0]};
            my = {(|y[30:23]), y[22:0]};
            unpack_operands = {
                sx,
                (nx || ny || (ix && zy) || (iy && zx)),
                (ix || iy),
                (zx || zy),
                x[30:23], y[30:23], mx, my
            };
        end
    endfunction

    function [67:0] multiply_significands;
        input [67:0] din;
        reg signed [10:0] ex, ey, exponent;
        reg [47:0] product;
        begin
            ex = (din[63:56] == 0) ? 11'sd1 :
                 $signed({3'b000, din[63:56]});
            ey = (din[55:48] == 0) ? 11'sd1 :
                 $signed({3'b000, din[55:48]});
            exponent = ex + ey - 11'sd127;
            product = din[47:24] * din[23:0];
            multiply_significands = {
                5'b0, din[67:64], exponent, product
            };
        end
    endfunction

    function [67:0] normalize_product;
        input [67:0] din;
        reg [47:0] product, aligned_product, shifted_product;
        reg signed [10:0] exponent;
        reg [23:0] mantissa;
        reg guard_bit, round_bit, sticky_bit, discarded;
        integer i, highest, shift_count;
        begin
            product = din[47:0];
            exponent = $signed(din[58:48]);
            highest = 0;
            for (i = 0; i < 48; i = i + 1)
                if (product[i])
                    highest = i;

            aligned_product = product << (47 - highest);
            exponent = exponent + highest - 46;
            shifted_product = aligned_product;
            discarded = 1'b0;
            shift_count = 0;

            // Align directly to the subnormal quantum before rounding.
            // Jam every discarded bit into the sticky-bit region.
            if (exponent < 11'sd1) begin
                shift_count = 1 - exponent;
                for (i = 0; i < 48; i = i + 1)
                    if (i < shift_count)
                        discarded = discarded | aligned_product[i];
                if (shift_count >= 48)
                    shifted_product = 48'b0;
                else
                    shifted_product = aligned_product >> shift_count;
                shifted_product[0] = shifted_product[0] | discarded;
                exponent = 11'sd0;
            end

            mantissa = shifted_product[47:24];
            guard_bit = shifted_product[23];
            round_bit = shifted_product[22];
            sticky_bit = |shifted_product[21:0];
            normalize_product = {
                26'b0, din[62:59], exponent, mantissa,
                guard_bit, round_bit, sticky_bit
            };
        end
    endfunction

    function [67:0] round_product;
        input [67:0] din;
        reg [24:0] rounded;
        reg [23:0] mantissa;
        reg signed [10:0] exponent;
        reg increment;
        begin
            exponent = $signed(din[37:27]);
            mantissa = din[26:3];
            increment = din[2] && (din[1] || din[0] || mantissa[0]);
            rounded = {1'b0, mantissa} + {24'b0, increment};
            if (rounded[24]) begin
                mantissa = rounded[24:1];
                exponent = exponent + 11'sd1;
            end else begin
                mantissa = rounded[23:0];
                // Rounding a subnormal can produce the minimum normal.
                if ((exponent == 11'sd0) && rounded[23])
                    exponent = 11'sd1;
            end
            round_product = {29'b0, din[41:38], exponent, mantissa};
        end
    endfunction

    function [67:0] pack_result;
        input [67:0] din;
        reg sign_bit;
        reg signed [10:0] exponent;
        reg [31:0] packed_result;
        begin
            sign_bit = din[38];
            exponent = $signed(din[34:24]);
            if (din[37])
                packed_result = 32'h7fc00000;
            else if (din[36])
                packed_result = {sign_bit, 8'hff, 23'b0};
            else if (din[35])
                packed_result = {sign_bit, 31'b0};
            else if (exponent >= 11'sd255)
                packed_result = {sign_bit, 8'hff, 23'b0};
            else
                packed_result = {sign_bit, exponent[7:0], din[22:0]};
            pack_result = {36'b0, packed_result};
        end
    endfunction

    wire [67:0] stage_data [0:5];
    wire stage_valid [0:5];
    assign stage_data[0] = {4'b0, a, b};
    assign stage_valid[0] = valid_in;

    genvar g;
    generate
        for (g = 0; g < 5; g = g + 1) begin : logical_stages
            wire [67:0] next_data;
            if (g == 0) begin : unpack_stage
                assign next_data = unpack_operands(stage_data[g]);
            end else if (g == 1) begin : multiply_stage
                assign next_data = multiply_significands(stage_data[g]);
            end else if (g == 2) begin : normalize_stage
                assign next_data = normalize_product(stage_data[g]);
            end else if (g == 3) begin : rounding_stage
                assign next_data = round_product(stage_data[g]);
            end else begin : packing_stage
                assign next_data = pack_result(stage_data[g]);
            end

            // For LATENCY 1..4, combine adjacent logical stages.
            if ((LATENCY >= 5) ||
                (((g + 1) * LATENCY / 5) > (g * LATENCY / 5))) begin : registered_stage
                reg [67:0] data_reg;
                reg valid_reg;
                always @(posedge clk) begin
                    if (rst) begin
                        data_reg <= 68'b0;
                        valid_reg <= 1'b0;
                    end else begin
                        data_reg <= next_data;
                        valid_reg <= stage_valid[g];
                    end
                end
                assign stage_data[g + 1] = data_reg;
                assign stage_valid[g + 1] = valid_reg;
            end else begin : combined_stage
                assign stage_data[g + 1] = next_data;
                assign stage_valid[g + 1] = stage_valid[g];
            end
        end

        if (LATENCY > 5) begin : additional_stages
            reg [31:0] data_delay [0:LATENCY-6];
            reg valid_delay [0:LATENCY-6];
            integer k;
            always @(posedge clk) begin
                if (rst) begin
                    for (k = 0; k < LATENCY-5; k = k + 1) begin
                        data_delay[k] <= 32'b0;
                        valid_delay[k] <= 1'b0;
                    end
                end else begin
                    data_delay[0] <= stage_data[5][31:0];
                    valid_delay[0] <= stage_valid[5];
                    for (k = 1; k < LATENCY-5; k = k + 1) begin
                        data_delay[k] <= data_delay[k-1];
                        valid_delay[k] <= valid_delay[k-1];
                    end
                end
            end
            assign result = data_delay[LATENCY-6];
            assign valid_out = valid_delay[LATENCY-6];
        end else begin : direct_output
            assign result = stage_data[5][31:0];
            assign valid_out = stage_valid[5];
        end
    endgenerate
endmodule

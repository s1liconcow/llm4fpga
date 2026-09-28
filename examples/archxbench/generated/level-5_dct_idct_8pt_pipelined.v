module dct1d_8_pipeline #(
    parameter DATA_W = 12,
    parameter COEFF_W = 16,
    parameter OUT_W = 18
) (
    input clk,
    input rst,
    input signed [OUT_W-1:0] sample_in,
    input valid_in,
    input mode,
    input [2:0] index,
    output reg signed [OUT_W-1:0] coeff_out,
    output reg valid_out,
    output reg [2:0] index_out
);
    localparam PROD_W = OUT_W + COEFF_W;
    localparam ACC_W = PROD_W + 3;

    // Orthonormal DCT matrix: round(alpha[k] * cos(...) * 32768).
    function signed [COEFF_W-1:0] matrix_coeff;
        input [2:0] k;
        input [2:0] n;
        begin
            case ({k,n})
                6'd0: matrix_coeff = 16'sd11585;
                6'd1: matrix_coeff = 16'sd11585;
                6'd2: matrix_coeff = 16'sd11585;
                6'd3: matrix_coeff = 16'sd11585;
                6'd4: matrix_coeff = 16'sd11585;
                6'd5: matrix_coeff = 16'sd11585;
                6'd6: matrix_coeff = 16'sd11585;
                6'd7: matrix_coeff = 16'sd11585;
                6'd8: matrix_coeff = 16'sd16069;
                6'd9: matrix_coeff = 16'sd13623;
                6'd10: matrix_coeff = 16'sd9102;
                6'd11: matrix_coeff = 16'sd3196;
                6'd12: matrix_coeff = -16'sd3196;
                6'd13: matrix_coeff = -16'sd9102;
                6'd14: matrix_coeff = -16'sd13623;
                6'd15: matrix_coeff = -16'sd16069;
                6'd16: matrix_coeff = 16'sd15137;
                6'd17: matrix_coeff = 16'sd6270;
                6'd18: matrix_coeff = -16'sd6270;
                6'd19: matrix_coeff = -16'sd15137;
                6'd20: matrix_coeff = -16'sd15137;
                6'd21: matrix_coeff = -16'sd6270;
                6'd22: matrix_coeff = 16'sd6270;
                6'd23: matrix_coeff = 16'sd15137;
                6'd24: matrix_coeff = 16'sd13623;
                6'd25: matrix_coeff = -16'sd3196;
                6'd26: matrix_coeff = -16'sd16069;
                6'd27: matrix_coeff = -16'sd9102;
                6'd28: matrix_coeff = 16'sd9102;
                6'd29: matrix_coeff = 16'sd16069;
                6'd30: matrix_coeff = 16'sd3196;
                6'd31: matrix_coeff = -16'sd13623;
                6'd32: matrix_coeff = 16'sd11585;
                6'd33: matrix_coeff = -16'sd11585;
                6'd34: matrix_coeff = -16'sd11585;
                6'd35: matrix_coeff = 16'sd11585;
                6'd36: matrix_coeff = 16'sd11585;
                6'd37: matrix_coeff = -16'sd11585;
                6'd38: matrix_coeff = -16'sd11585;
                6'd39: matrix_coeff = 16'sd11585;
                6'd40: matrix_coeff = 16'sd9102;
                6'd41: matrix_coeff = -16'sd16069;
                6'd42: matrix_coeff = 16'sd3196;
                6'd43: matrix_coeff = 16'sd13623;
                6'd44: matrix_coeff = -16'sd13623;
                6'd45: matrix_coeff = -16'sd3196;
                6'd46: matrix_coeff = 16'sd16069;
                6'd47: matrix_coeff = -16'sd9102;
                6'd48: matrix_coeff = 16'sd6270;
                6'd49: matrix_coeff = -16'sd15137;
                6'd50: matrix_coeff = 16'sd15137;
                6'd51: matrix_coeff = -16'sd6270;
                6'd52: matrix_coeff = -16'sd6270;
                6'd53: matrix_coeff = 16'sd15137;
                6'd54: matrix_coeff = -16'sd15137;
                6'd55: matrix_coeff = 16'sd6270;
                6'd56: matrix_coeff = 16'sd3196;
                6'd57: matrix_coeff = -16'sd9102;
                6'd58: matrix_coeff = 16'sd13623;
                6'd59: matrix_coeff = -16'sd16069;
                6'd60: matrix_coeff = 16'sd16069;
                6'd61: matrix_coeff = -16'sd13623;
                6'd62: matrix_coeff = 16'sd9102;
                6'd63: matrix_coeff = -16'sd3196;
                default: matrix_coeff = 0;
            endcase
        end
    endfunction

    reg signed [PROD_W-1:0] products [0:7];
    reg signed [ACC_W-1:0] accumulators [0:7];
    reg signed [ACC_W-1:0] results [0:7];
    reg product_valid;
    reg [2:0] product_index;
    reg output_active;
    reg [2:0] output_position;

    genvar g;
    generate
        for (g = 0; g < 8; g = g + 1) begin : lanes
            localparam [2:0] LANE = g;
            wire signed [COEFF_W-1:0] coefficient;
            wire signed [ACC_W-1:0] extended_product;
            wire signed [ACC_W-1:0] next_sum;

            assign coefficient = mode ? matrix_coeff(index, LANE)
                                      : matrix_coeff(LANE, index);
            assign extended_product = {{3{products[g][PROD_W-1]}}, products[g]};
            assign next_sum = accumulators[g] + extended_product;

            always @(posedge clk) begin
                if (rst) begin
                    products[g] <= 0;
                    accumulators[g] <= 0;
                    results[g] <= 0;
                end else begin
                    if (valid_in)
                        products[g] <= sample_in * coefficient;

                    if (product_valid) begin
                        if (product_index == 3'd0)
                            accumulators[g] <= extended_product;
                        else
                            accumulators[g] <= next_sum;

                        if (product_index == 3'd7)
                            results[g] <= next_sum;
                    end
                end
            end
        end
    endgenerate

    always @(posedge clk) begin
        if (rst) begin
            product_valid <= 1'b0;
            product_index <= 3'd0;
            output_active <= 1'b0;
            output_position <= 3'd0;
            coeff_out <= 0;
            valid_out <= 1'b0;
            index_out <= 3'd0;
        end else begin
            product_valid <= valid_in;
            if (valid_in)
                product_index <= index;

            valid_out <= output_active;
            if (output_active) begin
                // Assignment truncates the shifted signed accumulator to OUT_W.
                coeff_out <= $signed(results[output_position]) >>> 15;
                index_out <= output_position;
                if (output_position == 3'd7) begin
                    output_active <= 1'b0;
                    output_position <= 3'd0;
                end else begin
                    output_position <= output_position + 3'd1;
                end
            end

            // This takes priority when completing and serializing blocks overlap.
            if (product_valid && product_index == 3'd7) begin
                output_active <= 1'b1;
                output_position <= 3'd0;
            end
        end
    end
endmodule

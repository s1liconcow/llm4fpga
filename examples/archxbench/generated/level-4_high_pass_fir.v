module highpass_fir #(
    parameter DATA_W = 18,
    parameter TAP_CNT = 51,
    parameter GAIN_W = 4
) (
    input clk,
    input rst,
    input valid_in,
    input signed [DATA_W-1:0] data_in,
    output reg valid_out,
    output reg signed [DATA_W+GAIN_W-1:0] data_out
);

    reg signed [DATA_W-1:0] coeffs_mem [0:TAP_CNT-1];

    localparam PROD_W = 2 * DATA_W;
    localparam ACC_W = PROD_W + $clog2(TAP_CNT) + 1;
    localparam signed [ACC_W-1:0] ROUND_BIAS = 16384;

    reg signed [DATA_W-1:0] history [0:TAP_CNT-2];
    reg signed [PROD_W-1:0] products [0:TAP_CNT-1];
    reg signed [ACC_W-1:0] sum_comb;
    reg signed [ACC_W-1:0] accumulator;
    wire signed [ACC_W-1:0] rounded_acc;
    reg products_valid;
    reg accumulator_valid;
    integer i;
    integer j;

    assign rounded_acc = accumulator + ROUND_BIAS;

    always @* begin
        sum_comb = {ACC_W{1'b0}};
        for (j = 0; j < TAP_CNT; j = j + 1) begin
            sum_comb = sum_comb +
                {{(ACC_W-PROD_W){products[j][PROD_W-1]}}, products[j]};
        end
    end

    always @(posedge clk) begin
        if (rst) begin
            products_valid <= 1'b0;
            accumulator_valid <= 1'b0;
            valid_out <= 1'b0;
            accumulator <= {ACC_W{1'b0}};
            data_out <= {(DATA_W+GAIN_W){1'b0}};
            for (i = 0; i < TAP_CNT-1; i = i + 1)
                history[i] <= {DATA_W{1'b0}};
            for (i = 0; i < TAP_CNT; i = i + 1)
                products[i] <= {PROD_W{1'b0}};
        end else begin
            products_valid <= valid_in;
            accumulator_valid <= products_valid;
            valid_out <= accumulator_valid;

            if (valid_in) begin
                products[0] <= data_in * coeffs_mem[0];
                for (i = 1; i < TAP_CNT; i = i + 1)
                    products[i] <= history[i-1] * coeffs_mem[i];

                history[0] <= data_in;
                for (i = 1; i < TAP_CNT-1; i = i + 1)
                    history[i] <= history[i-1];
            end

            if (products_valid)
                accumulator <= sum_comb;

            if (accumulator_valid)
                data_out <= rounded_acc >>> 15;
        end
    end
endmodule

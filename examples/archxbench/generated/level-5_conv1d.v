module conv1d #(
    parameter DATA_W = 8,
    parameter KERNEL_SIZE = 5,
    parameter GAIN_W = 4,
    // Signed 32-bit coefficient k is stored at COEFFS[32*k +: 32].
    parameter [32*KERNEL_SIZE-1:0] COEFFS =
        {32'sd2, 32'sd8, 32'sd12, 32'sd8, 32'sd2}
) (
    input clk,
    input rst,
    input valid_in,
    input [DATA_W-1:0] data_in,
    output valid_out,
    output [DATA_W+GAIN_W-1:0] data_out
);
    localparam OUT_W = DATA_W + GAIN_W;
    localparam LOG_K = (KERNEL_SIZE > 1) ? $clog2(KERNEL_SIZE) : 0;
    localparam PRODUCT_ACC_W = DATA_W + 33 + LOG_K;
    localparam REQUIRED_ACC_W = DATA_W + GAIN_W + LOG_K;
    localparam ACC_W = (PRODUCT_ACC_W > REQUIRED_ACC_W)
                    ? PRODUCT_ACC_W : REQUIRED_ACC_W;
    localparam STAGES = (KERNEL_SIZE > 1) ? KERNEL_SIZE-1 : 1;

    reg [DATA_W-1:0] shift_register [0:KERNEL_SIZE-1];
    reg [OUT_W-1:0] data_pipeline [0:STAGES-1];
    reg [STAGES-1:0] valid_pipeline;
    wire [DATA_W-1:0] incoming_sample;
    reg signed [ACC_W-1:0] mac_next;
    integer k;
    integer j;

    assign incoming_sample = valid_in ? data_in : {DATA_W{1'b0}};
    assign valid_out = valid_pipeline[STAGES-1];
    assign data_out = data_pipeline[STAGES-1];

    // Compute the causal, zero-padded window including this input.
    // Tap zero is the oldest sample in the resulting window.
    always @* begin
        mac_next = {ACC_W{1'b0}};
        for (k = 0; k < KERNEL_SIZE-1; k = k + 1) begin
            mac_next = mac_next
                + $signed({1'b0, shift_register[k+1]})
                * $signed(COEFFS[32*k +: 32]);
        end
        mac_next = mac_next
            + $signed({1'b0, incoming_sample})
            * $signed(COEFFS[32*(KERNEL_SIZE-1) +: 32]);
    end

    always @(posedge clk) begin
        if (rst) begin
            for (j = 0; j < KERNEL_SIZE; j = j + 1)
                shift_register[j] <= {DATA_W{1'b0}};
            for (j = 0; j < STAGES; j = j + 1)
                data_pipeline[j] <= {OUT_W{1'b0}};
            valid_pipeline <= {STAGES{1'b0}};
        end else begin
            for (j = 0; j < KERNEL_SIZE-1; j = j + 1)
                shift_register[j] <= shift_register[j+1];
            shift_register[KERNEL_SIZE-1] <= incoming_sample;

            data_pipeline[0] <= mac_next >>> GAIN_W;
            valid_pipeline[0] <= valid_in;
            for (j = 1; j < STAGES; j = j + 1) begin
                data_pipeline[j] <= data_pipeline[j-1];
                valid_pipeline[j] <= valid_pipeline[j-1];
            end
        end
    end
endmodule

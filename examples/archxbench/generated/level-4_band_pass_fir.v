module bandpass_fir #(
    parameter DATA_W  = 18,
    parameter TAP_CNT = 51,
    parameter GAIN_W  = 4
) (
    input                             clk,
    input                             rst,
    input                             valid_in,
    input signed [DATA_W-1:0]         data_in,
    output reg                        valid_out,
    output reg signed [DATA_W+GAIN_W-1:0] data_out
);

    function integer ceil_log2;
        input integer value;
        integer v;
        begin
            v = value - 1;
            ceil_log2 = 0;
            while (v > 0) begin
                ceil_log2 = ceil_log2 + 1;
                v = v >> 1;
            end
        end
    endfunction

    localparam LEVELS = ceil_log2(TAP_CNT);
    localparam LEAVES = (1 << LEVELS);
    localparam REQUIRED_ACC_W = 2*DATA_W + LEVELS;
    localparam ACC_W = (REQUIRED_ACC_W < 16) ? 16 : REQUIRED_ACC_W;
    localparam signed [ACC_W:0] ROUND_BIAS = 16384;

    reg signed [DATA_W-1:0] coeffs_mem [0:TAP_CNT-1];
    reg signed [DATA_W-1:0] history [0:TAP_CNT-2];

    // Balanced, fully registered reduction tree; index 1 is its root.
    reg signed [ACC_W-1:0] tree [1:2*LEAVES-1];
    wire signed [2*DATA_W-1:0] products [0:TAP_CNT-1];
    reg [LEVELS:0] valid_pipe;
    wire signed [ACC_W:0] rounded;

    assign rounded = $signed({tree[1][ACC_W-1], tree[1]}) + ROUND_BIAS;

    genvar g;
    generate
        for (g = 0; g < TAP_CNT; g = g + 1) begin : multipliers
            if (g == 0) begin : current_sample
                assign products[g] = $signed(data_in) * $signed(coeffs_mem[g]);
            end else begin : previous_sample
                assign products[g] = $signed(history[g-1]) * $signed(coeffs_mem[g]);
            end
        end
    endgenerate

    integer i;
    always @(posedge clk) begin
        if (rst) begin
            valid_out <= 1'b0;
            data_out <= 0;
            valid_pipe <= 0;
            for (i = 0; i < TAP_CNT-1; i = i + 1)
                history[i] <= 0;
            for (i = 1; i < 2*LEAVES; i = i + 1)
                tree[i] <= 0;
        end else begin
            // Invalid cycles insert pipeline bubbles without consuming samples.
            if (valid_in) begin
                history[0] <= data_in;
                for (i = 1; i < TAP_CNT-1; i = i + 1)
                    history[i] <= history[i-1];
            end

            for (i = 0; i < TAP_CNT; i = i + 1) begin
                if (valid_in)
                    tree[LEAVES+i] <= products[i];
                else
                    tree[LEAVES+i] <= 0;
            end
            for (i = TAP_CNT; i < LEAVES; i = i + 1)
                tree[LEAVES+i] <= 0;

            for (i = 1; i < LEAVES; i = i + 1)
                tree[i] <= tree[2*i] + tree[2*i+1];

            valid_pipe[0] <= valid_in;
            for (i = 1; i <= LEVELS; i = i + 1)
                valid_pipe[i] <= valid_pipe[i-1];

            valid_out <= valid_pipe[LEVELS];
            if (valid_pipe[LEVELS])
                data_out <= rounded >>> 15;
        end
    end
endmodule

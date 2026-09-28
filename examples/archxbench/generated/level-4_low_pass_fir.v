module lowpass_fir #(
    parameter DATA_W  = 18,
    parameter TAP_CNT = 51,
    parameter GAIN_W  = 4
) (
    input                                 clk,
    input                                 rst,
    input                                 valid_in,
    input  signed [DATA_W-1:0]             data_in,
    output reg                            valid_out,
    output reg signed [DATA_W+GAIN_W-1:0] data_out
);

    function integer clog2;
        input integer value;
        integer v;
        begin
            v = value - 1;
            clog2 = 0;
            while (v > 0) begin
                clog2 = clog2 + 1;
                v = v >> 1;
            end
        end
    endfunction

    localparam integer LEVELS = clog2(TAP_CNT);
    localparam integer LEAVES = (1 << LEVELS);
    localparam integer ACC_W = 2*DATA_W + LEVELS;
    localparam integer ROUND_W = ((ACC_W + 1) > 16) ? (ACC_W + 1) : 16;
    localparam signed [ROUND_W-1:0] ROUND_BIAS = 16384;

    reg signed [DATA_W-1:0] coeffs_mem [0:TAP_CNT-1];
    reg signed [DATA_W-1:0] history [0:TAP_CNT-2];

    // Binary heap: root at zero, registered products at the leaves.
    reg signed [ACC_W-1:0] sum_pipe [0:2*LEAVES-2];
    reg [LEVELS:0] valid_pipe;

    integer i;
    always @(posedge clk) begin
        if (rst) begin
            for (i = 0; i < TAP_CNT-1; i = i + 1)
                history[i] <= 0;
        end else begin
            // Clock-based history implements window[k] from k clocks ago.
            history[0] <= data_in;
            for (i = 1; i < TAP_CNT-1; i = i + 1)
                history[i] <= history[i-1];
        end
    end

    genvar k;
    generate
        for (k = 0; k < LEAVES; k = k + 1) begin : product_stage
            if (k < TAP_CNT) begin : active_tap
                wire signed [DATA_W-1:0] sample_value;
                wire signed [2*DATA_W-1:0] product_value;
                if (k == 0) begin : current_sample
                    assign sample_value = data_in;
                end else begin : delayed_sample
                    assign sample_value = history[k-1];
                end
                assign product_value = coeffs_mem[k] * sample_value;
                always @(posedge clk) begin
                    if (rst)
                        sum_pipe[LEAVES-1+k] <= 0;
                    else
                        sum_pipe[LEAVES-1+k] <= product_value;
                end
            end else begin : padding_tap
                always @(posedge clk)
                    sum_pipe[LEAVES-1+k] <= 0;
            end
        end

        for (k = 0; k < LEAVES-1; k = k + 1) begin : adder_stage
            always @(posedge clk) begin
                if (rst)
                    sum_pipe[k] <= 0;
                else
                    sum_pipe[k] <= sum_pipe[2*k+1] + sum_pipe[2*k+2];
            end
        end
    endgenerate

    wire signed [ROUND_W-1:0] extended_acc;
    wire signed [ROUND_W-1:0] rounded_acc;
    assign extended_acc = sum_pipe[0];
    assign rounded_acc = extended_acc + ROUND_BIAS;

    integer s;
    always @(posedge clk) begin
        if (rst) begin
            valid_pipe <= 0;
            valid_out <= 1'b0;
            data_out <= 0;
        end else begin
            valid_pipe[0] <= valid_in;
            for (s = 1; s <= LEVELS; s = s + 1)
                valid_pipe[s] <= valid_pipe[s-1];
            valid_out <= valid_pipe[LEVELS];
            if (valid_pipe[LEVELS])
                data_out <= rounded_acc >>> 15;
        end
    end
endmodule

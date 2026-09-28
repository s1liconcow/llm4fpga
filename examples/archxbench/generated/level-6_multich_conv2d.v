module multich_conv2d #(
    parameter CIN = 3,
    parameter COUT = 8,
    parameter K = 3,
    parameter H = 128,
    parameter W = 128,
    parameter DATA_W = 8,
    parameter BIAS_W = 16,
    parameter OUT_W = 16
)(
    input clk, rst,
    input [DATA_W-1:0] pixel_in,
    input valid_in,
    input last_in,
    input [COUT*CIN*K*K*DATA_W-1:0] kernel,
    input [COUT*BIAS_W-1:0] bias,
    output [OUT_W-1:0] pixel_out,
    output valid_out,
    output done
);
    function integer clog2;
        input integer n;
        integer v;
        begin
            v = n-1;
            clog2 = 0;
            while (v > 0) begin
                clog2 = clog2+1;
                v = v >> 1;
            end
        end
    endfunction

    localparam integer PAD = K/2;
    localparam integer PH = H+2*PAD;
    localparam integer PW = W+2*PAD;
    localparam integer TERMS = CIN*K*K;
    localparam integer LEVELS = clog2(TERMS+1);
    localparam integer LEAVES = 1 << LEVELS;
    localparam integer DOT_W = 2*DATA_W+clog2(TERMS)+1;
    localparam integer SUM_W = ((DOT_W > BIAS_W) ? DOT_W : BIAS_W)+1;
    localparam integer ACC_W = (SUM_W > OUT_W) ? SUM_W : OUT_W;
    localparam integer BANK_ROWS = (PH+K-1)/K;
    localparam integer BANK_COLS = (PW+K-1)/K;
    localparam integer BANK_DEPTH = BANK_ROWS*BANK_COLS;
    localparam integer IC_W = (CIN > 1) ? clog2(CIN) : 1;
    localparam integer OC_W = (COUT > 1) ? clog2(COUT) : 1;
    localparam integer IR_W = (PH > 1) ? clog2(PH) : 1;
    localparam integer IX_W = (PW > 1) ? clog2(PW) : 1;
    localparam integer OR_W = (H > 1) ? clog2(H) : 1;
    localparam integer OX_W = (W > 1) ? clog2(W) : 1;
    localparam [1:0] LOAD = 0, RUN = 1, DRAIN = 2;
    localparam signed [ACC_W-1:0] MAX_OUT =
        {{(ACC_W-OUT_W){1'b0}}, 1'b0, {(OUT_W-1){1'b1}}};
    localparam signed [ACC_W-1:0] MIN_OUT =
        {{(ACC_W-OUT_W){1'b1}}, 1'b1, {(OUT_W-1){1'b0}}};

    reg [1:0] state;
    reg [IC_W-1:0] in_channel;
    reg [IR_W-1:0] in_row;
    reg [IX_W-1:0] in_col;
    reg [OC_W-1:0] out_channel;
    reg [OR_W-1:0] out_row;
    reg [OX_W-1:0] out_col;
    reg [LEVELS:0] valid_pipe;
    reg [LEVELS:0] final_pipe;
    reg signed [ACC_W-1:0] mac_tree [1:2*LEAVES-1];
    wire [DATA_W-1:0] window [0:TERMS-1];

    // Pixels are unsigned; coefficients and biases are two's-complement.
    function signed [ACC_W-1:0] multiply;
        input [DATA_W-1:0] px;
        input [DATA_W-1:0] wt;
        reg signed [DATA_W:0] positive_pixel;
        reg signed [DATA_W-1:0] signed_weight;
        reg signed [2*DATA_W:0] product;
        begin
            positive_pixel = {1'b0, px};
            signed_weight = wt;
            product = positive_pixel * signed_weight;
            multiply = product;
        end
    endfunction

    // Each spatial residue has its own memory. A K-by-K window uses
    // exactly one read from each bank, avoiding a many-port frame RAM.
    // Bank order rotates with the window origin; coefficients follow it.
    genvar ch, br, bc;
    generate
        for (ch = 0; ch < CIN; ch = ch+1) begin: channels
            for (br = 0; br < K; br = br+1) begin: row_banks
                for (bc = 0; bc < K; bc = bc+1) begin: col_banks
                    localparam integer TERM = ch*K*K+br*K+bc;
                    reg [DATA_W-1:0] image_mem [0:BANK_DEPTH-1];
                    wire [31:0] row_rem;
                    wire [31:0] col_rem;
                    wire [31:0] dy;
                    wire [31:0] dx;
                    wire [31:0] read_addr;
                    wire [31:0] write_addr;
                    wire [31:0] weight_number;
                    wire [DATA_W-1:0] weight_value;

                    assign row_rem = out_row % K;
                    assign col_rem = out_col % K;
                    assign dy = (br >= row_rem) ? br-row_rem : br+K-row_rem;
                    assign dx = (bc >= col_rem) ? bc-col_rem : bc+K-col_rem;
                    assign read_addr =
                        (out_row/K + ((br < row_rem) ? 1 : 0))*BANK_COLS
                        + out_col/K + ((bc < col_rem) ? 1 : 0);
                    assign write_addr = (in_row/K)*BANK_COLS + in_col/K;
                    assign window[TERM] = image_mem[read_addr];
                    assign weight_number = out_channel*TERMS+ch*K*K+dy*K+dx;
                    // Tensor element zero occupies the most significant slice.
                    assign weight_value = kernel[
                        (COUT*TERMS-1-weight_number)*DATA_W +: DATA_W];

                    always @(posedge clk) begin
                        if (!rst && state == LOAD && valid_in &&
                            in_channel == ch && in_row % K == br && in_col % K == bc)
                            image_mem[write_addr] <= pixel_in;
                        if (!rst && state == RUN)
                            mac_tree[LEAVES+TERM] <= multiply(window[TERM], weight_value);
                    end
                end
            end
        end
    endgenerate

    wire signed [BIAS_W-1:0] selected_bias;
    assign selected_bias = bias[(COUT-1-out_channel)*BIAS_W +: BIAS_W];
    assign valid_out = valid_pipe[LEVELS];
    assign done = valid_pipe[LEVELS] && final_pipe[LEVELS];
    assign pixel_out = (mac_tree[1] > MAX_OUT) ? MAX_OUT[OUT_W-1:0] :
                       (mac_tree[1] < MIN_OUT) ? MIN_OUT[OUT_W-1:0] :
                       mac_tree[1][OUT_W-1:0];

    integer stage, node, i;
    always @(posedge clk) begin
        if (rst) begin
            state <= LOAD;
            in_channel <= 0;
            in_row <= 0;
            in_col <= 0;
            out_channel <= 0;
            out_row <= 0;
            out_col <= 0;
            valid_pipe <= 0;
            final_pipe <= 0;
        end else begin
            valid_pipe[0] <= (state == RUN);
            final_pipe[0] <= (state == RUN) &&
                (out_channel == COUT-1) && (out_row == H-1) && (out_col == W-1);
            for (stage = 1; stage <= LEVELS; stage = stage+1) begin
                valid_pipe[stage] <= valid_pipe[stage-1];
                final_pipe[stage] <= final_pipe[stage-1];
            end
            for (node = 1; node < LEAVES; node = node+1)
                mac_tree[node] <= mac_tree[2*node] + mac_tree[2*node+1];

            if (state == RUN) begin
                mac_tree[LEAVES+TERMS] <= selected_bias;
                for (i = TERMS+1; i < LEAVES; i = i+1)
                    mac_tree[LEAVES+i] <= 0;
            end

            case (state)
                LOAD: begin
                    if (valid_in) begin
                        if (in_col == PW-1) begin
                            in_col <= 0;
                            if (in_row == PH-1) begin
                                in_row <= 0;
                                if (in_channel == CIN-1) begin
                                    in_channel <= 0;
                                    out_channel <= 0;
                                    out_row <= 0;
                                    out_col <= 0;
                                    state <= RUN;
                                end else
                                    in_channel <= in_channel+1'b1;
                            end else
                                in_row <= in_row+1'b1;
                        end else
                            in_col <= in_col+1'b1;
                    end
                end
                RUN: begin
                    if (out_col == W-1) begin
                        out_col <= 0;
                        if (out_row == H-1) begin
                            out_row <= 0;
                            if (out_channel == COUT-1)
                                state <= DRAIN;
                            else
                                out_channel <= out_channel+1'b1;
                        end else
                            out_row <= out_row+1'b1;
                    end else
                        out_col <= out_col+1'b1;
                end
                DRAIN: begin
                    if (done) begin
                        state <= LOAD;
                        in_channel <= 0;
                        in_row <= 0;
                        in_col <= 0;
                    end
                end
                default: state <= LOAD;
            endcase
        end
    end
endmodule
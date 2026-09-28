module conv3d #(
    parameter K1 = 5,
    parameter K2 = 5,
    parameter K3 = 5,
    parameter D = 5,
    parameter H = 32,
    parameter W = 32,
    parameter DATA_W = 8,
    parameter OUT_W = DATA_W + $clog2(K1*K2*K3)
) (
    input clk,
    input rst,
    input [DATA_W-1:0] voxel_in,
    input valid_in,
    input [K1*K2*K3*DATA_W-1:0] kernel,
    input last_in,
    output [OUT_W-1:0] voxel_out,
    output valid_out,
    output done
);
    localparam FRAME_SIZE = H*W;
    localparam N = D*FRAME_SIZE;
    localparam TAPS = K1*K2*K3;
    localparam LEVELS = $clog2(TAPS);
    localparam LEAVES = 2**LEVELS;
    localparam ROW_RADIUS = K2/2;
    localparam COL_RADIUS = K3/2;
    localparam LOOKAHEAD = (K2-1-ROW_RADIUS)*W + K3-1-COL_RADIUS;
    localparam DEPTH_LENGTH = (K1 > 1) ? (K1-1)*FRAME_SIZE : 1;
    localparam LINE_LENGTH = (K2 > 1) ? (K2-1)*W : 1;
    localparam COLUMN_LENGTH = (K3 > 1) ? K3-1 : 1;
    localparam COUNT_W = (N > 1) ? $clog2(N+1) : 1;
    localparam TOKEN_W = (N+LOOKAHEAD > 1) ? $clog2(N+LOOKAHEAD+1) : 1;
    localparam DEPTH_W = (D > 1) ? $clog2(D) : 1;
    localparam ROW_W = (H > 1) ? $clog2(H) : 1;
    localparam COL_W = (W > 1) ? $clog2(W) : 1;

    // Fixed-address shift delays avoid large circular-buffer read muxes.
    // Successive FRAME_SIZE segments are the K1-1 depth frame buffers.
    reg [DATA_W-1:0] depth_frame_buffers [0:DEPTH_LENGTH-1];
    wire [DATA_W-1:0] depth_sample [0:K1-1];
    wire [DATA_W-1:0] row_sample [0:K1*K2-1];
    wire [DATA_W-1:0] raw_window [0:TAPS-1];
    reg [DATA_W-1:0] stencil_window [0:TAPS-1];

    reg [COUNT_W-1:0] input_count;
    reg [COUNT_W-1:0] output_count;
    reg [TOKEN_W-1:0] token_count;
    reg [DEPTH_W-1:0] output_depth;
    reg [ROW_W-1:0] output_row;
    reg [COL_W-1:0] output_column;
    reg input_ended;
    reg window_valid;
    reg window_last;

    wire accept_input;
    wire flush_token;
    wire advance;
    wire issue_window;
    wire [DATA_W-1:0] input_sample;

    assign accept_input = !rst && !input_ended &&
                          (input_count < N) && valid_in;
    assign flush_token = !rst && input_ended &&
                         (token_count < N+LOOKAHEAD);
    assign advance = accept_input || flush_token;
    assign input_sample = accept_input ? voxel_in : {DATA_W{1'b0}};
    assign issue_window = advance && (token_count >= LOOKAHEAD) &&
                          (output_count < N);

    always @(posedge clk) begin
        if (rst) begin
            input_count <= 0;
            output_count <= 0;
            token_count <= 0;
            output_depth <= 0;
            output_row <= 0;
            output_column <= 0;
            input_ended <= 0;
            window_valid <= 0;
            window_last <= 0;
        end else begin
            window_valid <= issue_window;
            window_last <= issue_window && (output_count == N-1);
            if (accept_input) begin
                input_count <= input_count + 1'b1;
                if (last_in || input_count == N-1)
                    input_ended <= 1'b1;
            end
            if (advance)
                token_count <= token_count + 1'b1;
            if (issue_window) begin
                output_count <= output_count + 1'b1;
                if (output_column == W-1) begin
                    output_column <= 0;
                    if (output_row == H-1) begin
                        output_row <= 0;
                        if (output_depth != D-1)
                            output_depth <= output_depth + 1'b1;
                    end else
                        output_row <= output_row + 1'b1;
                end else
                    output_column <= output_column + 1'b1;
            end
        end
    end

    genvar d, r, c, t, level, node;
    generate
        if (K1 > 1) begin : depth_delays
            integer i;
            always @(posedge clk) begin
                if (advance) begin
                    depth_frame_buffers[0] <= input_sample;
                    for (i = 1; i < DEPTH_LENGTH; i = i+1)
                        depth_frame_buffers[i] <= depth_frame_buffers[i-1];
                end
            end
        end

        for (d = 0; d < K1; d = d+1) begin : planes
            if (d == K1-1) begin : current_plane
                assign depth_sample[d] = input_sample;
            end else begin : delayed_plane
                assign depth_sample[d] =
                    depth_frame_buffers[(K1-1-d)*FRAME_SIZE-1];
            end

            // K2-1 line delays retain the complete K2-row window;
            // the output center trails the stream by K2/2 rows.
            if (K2 > 1) begin : lines
                reg [DATA_W-1:0] spatial_line_buffers [0:LINE_LENGTH-1];
                integer i;
                always @(posedge clk) begin
                    if (advance) begin
                        spatial_line_buffers[0] <= depth_sample[d];
                        for (i = 1; i < LINE_LENGTH; i = i+1)
                            spatial_line_buffers[i] <= spatial_line_buffers[i-1];
                    end
                end
                for (r = 0; r < K2-1; r = r+1) begin : delayed_rows
                    assign row_sample[d*K2+r] =
                        spatial_line_buffers[(K2-1-r)*W-1];
                end
            end
            assign row_sample[d*K2+K2-1] = depth_sample[d];

            for (r = 0; r < K2; r = r+1) begin : rows
                if (K3 > 1) begin : columns
                    reg [DATA_W-1:0] column_history [0:COLUMN_LENGTH-1];
                    integer i;
                    always @(posedge clk) begin
                        if (advance) begin
                            column_history[0] <= row_sample[d*K2+r];
                            for (i = 1; i < COLUMN_LENGTH; i = i+1)
                                column_history[i] <= column_history[i-1];
                        end
                    end
                    for (c = 0; c < K3-1; c = c+1) begin : delayed_columns
                        assign raw_window[(d*K2+r)*K3+c] =
                            column_history[K3-2-c];
                    end
                end
                assign raw_window[(d*K2+r)*K3+K3-1] = row_sample[d*K2+r];

                for (c = 0; c < K3; c = c+1) begin : window_cells
                    localparam INDEX = (d*K2+r)*K3+c;
                    wire inside_volume;
                    assign inside_volume =
                        (output_depth >= K1-1-d) &&
                        (output_row + r >= ROW_RADIUS) &&
                        (output_row + r < H+ROW_RADIUS) &&
                        (output_column + c >= COL_RADIUS) &&
                        (output_column + c < W+COL_RADIUS);
                    always @(posedge clk) begin
                        if (rst)
                            stencil_window[INDEX] <= 0;
                        else if (issue_window) begin
                            if (inside_volume)
                                stencil_window[INDEX] <= raw_window[INDEX];
                            else
                                stencil_window[INDEX] <= 0;
                        end
                    end
                end
            end
        end
    endgenerate

    // Flattened, fixed-index tree: one product stage and LEVELS sum stages.
    wire [OUT_W-1:0] mac_tree [0:(LEVELS+1)*LEAVES-1];
    reg [LEVELS:0] valid_pipeline;
    reg [LEVELS:0] last_pipeline;
    integer v;
    always @(posedge clk) begin
        if (rst) begin
            valid_pipeline <= 0;
            last_pipeline <= 0;
        end else begin
            valid_pipeline[0] <= window_valid;
            last_pipeline[0] <= window_last;
            for (v = 1; v <= LEVELS; v = v+1) begin
                valid_pipeline[v] <= valid_pipeline[v-1];
                last_pipeline[v] <= last_pipeline[v-1];
            end
        end
    end

    generate
        for (t = 0; t < LEAVES; t = t+1) begin : products
            if (t < TAPS) begin : used
                wire [2*DATA_W-1:0] full_product;
                reg [OUT_W-1:0] product_register;
                assign full_product = stencil_window[t] *
                                      kernel[t*DATA_W +: DATA_W];
                always @(posedge clk) begin
                    if (rst)
                        product_register <= 0;
                    else
                        product_register <= full_product;
                end
                assign mac_tree[t] = product_register;
            end else begin : padding
                assign mac_tree[t] = {OUT_W{1'b0}};
            end
        end
        for (level = 1; level <= LEVELS; level = level+1) begin : sums
            for (node = 0; node < LEAVES; node = node+1) begin : nodes
                if (node < (LEAVES >> level)) begin : used
                    reg [OUT_W-1:0] sum_register;
                    always @(posedge clk) begin
                        if (rst)
                            sum_register <= 0;
                        else
                            sum_register <=
                                mac_tree[(level-1)*LEAVES+2*node] +
                                mac_tree[(level-1)*LEAVES+2*node+1];
                    end
                    assign mac_tree[level*LEAVES+node] = sum_register;
                end else begin : unused_node
                    assign mac_tree[level*LEAVES+node] = {OUT_W{1'b0}};
                end
            end
        end
    endgenerate

    assign voxel_out = mac_tree[LEVELS*LEAVES];
    assign valid_out = valid_pipeline[LEVELS];
    assign done = valid_pipeline[LEVELS] && last_pipeline[LEVELS];
endmodule

module conv2d #(
    parameter DATA_W      = 8,
    parameter IMG_WIDTH   = 64,
    parameter KERNEL_SIZE = 3,
    parameter GAIN_W      = 4,
    parameter [32*KERNEL_SIZE*KERNEL_SIZE-1:0] COEFFS = {
        32'sd1, 32'sd2, 32'sd1,
        32'sd2, 32'sd4, 32'sd2,
        32'sd1, 32'sd2, 32'sd1
    }
) (
    input                          clk,
    input                          rst,
    input                          valid_in,
    input      [DATA_W-1:0]        pixel_in,
    output reg                     valid_out,
    output reg [DATA_W+GAIN_W-1:0] pixel_out
);
    localparam NUM_TAPS = KERNEL_SIZE*KERNEL_SIZE;
    localparam LINE_ROWS = (KERNEL_SIZE > 1) ? KERNEL_SIZE-1 : 1;
    localparam ACC_W = DATA_W + 33 + $clog2(NUM_TAPS);

    // Flatten row storage to avoid multidimensional memory reads in
    // continuous assignments, which crash some Icarus versions.
    reg [DATA_W-1:0] line_buffers [0:LINE_ROWS*IMG_WIDTH-1];
    reg [DATA_W-1:0] window [0:KERNEL_SIZE-1][0:KERNEL_SIZE-1];
    integer column;

    reg signed [ACC_W-1:0] accumulator;
    reg signed [DATA_W:0] sample_value;
    reg signed [31:0] coefficient;
    integer ki, kj;

    always @* begin
        accumulator = {ACC_W{1'b0}};
        sample_value = {(DATA_W+1){1'b0}};
        coefficient = 32'sd0;
        for (ki = 0; ki < KERNEL_SIZE; ki = ki + 1) begin
            for (kj = 0; kj < KERNEL_SIZE; kj = kj + 1) begin
                coefficient = $signed(COEFFS[32*(ki*KERNEL_SIZE+kj) +: 32]);
                sample_value = {(DATA_W+1){1'b0}};
                if (column >= KERNEL_SIZE-1-kj) begin
                    if (kj == KERNEL_SIZE-1) begin
                        if (ki == KERNEL_SIZE-1)
                            sample_value = $signed({1'b0, pixel_in});
                        else
                            sample_value = $signed({1'b0, line_buffers[ki*IMG_WIDTH+column]});
                    end else begin
                        sample_value = $signed({1'b0, window[ki][kj+1]});
                    end
                end
                accumulator = accumulator + sample_value * coefficient;
            end
        end
    end

    integer r, c;
    always @(posedge clk) begin
        if (rst) begin
            column <= 0;
            valid_out <= 1'b0;
            pixel_out <= {(DATA_W+GAIN_W){1'b0}};
            for (r = 0; r < LINE_ROWS*IMG_WIDTH; r = r + 1)
                line_buffers[r] <= {DATA_W{1'b0}};
            for (r = 0; r < KERNEL_SIZE; r = r + 1)
                for (c = 0; c < KERNEL_SIZE; c = c + 1)
                    window[r][c] <= {DATA_W{1'b0}};
        end else begin
            valid_out <= valid_in;
            if (valid_in) begin
                pixel_out <= accumulator >>> GAIN_W;

                for (r = 0; r < KERNEL_SIZE-2; r = r + 1)
                    line_buffers[r*IMG_WIDTH+column] <=
                        line_buffers[(r+1)*IMG_WIDTH+column];
                if (KERNEL_SIZE > 1)
                    line_buffers[(LINE_ROWS-1)*IMG_WIDTH+column] <= pixel_in;

                for (r = 0; r < KERNEL_SIZE; r = r + 1) begin
                    for (c = 0; c < KERNEL_SIZE-1; c = c + 1) begin
                        if (column == 0)
                            window[r][c] <= {DATA_W{1'b0}};
                        else
                            window[r][c] <= window[r][c+1];
                    end
                    if (r == KERNEL_SIZE-1)
                        window[r][KERNEL_SIZE-1] <= pixel_in;
                    else
                        window[r][KERNEL_SIZE-1] <= line_buffers[r*IMG_WIDTH+column];
                end

                if (column == IMG_WIDTH-1)
                    column <= 0;
                else
                    column <= column + 1;
            end
        end
    end
endmodule

module unsharp_mask #(
    parameter IMG_WIDTH  = 256,
    parameter IMG_HEIGHT = 256,
    parameter PIXEL_W    = 8,
    parameter GAIN_W     = 8
) (
    input                       clk,
    input                       rst,
    input  [PIXEL_W-1:0]         pixel_in,
    input                       valid_in,
    input  [GAIN_W-1:0]          gain,
    output [PIXEL_W-1:0]         pixel_out,
    output                      valid_out
);
    localparam SUM_W = PIXEL_W + 4;
    localparam SCALE_W = PIXEL_W + GAIN_W;
    localparam INPUT_W = 1 + GAIN_W + PIXEL_W;
    localparam FRAME_SIZE = IMG_WIDTH * IMG_HEIGHT;
    localparam [PIXEL_W-1:0] MAX_PIXEL = 255;

    // Register a complete input transaction before updating the line store.
    reg [INPUT_W-1:0] input_d;
    reg [INPUT_W-1:0] input_q;
    always @* begin
        input_d = {valid_in, gain, pixel_in};
    end
    wire accept_pixel;
    wire [PIXEL_W-1:0] accepted_pixel;
    wire [GAIN_W-1:0] accepted_gain;
    assign accept_pixel = input_q[INPUT_W-1];
    assign accepted_pixel = input_q[PIXEL_W-1:0];
    assign accepted_gain = input_q[PIXEL_W+GAIN_W-1:PIXEL_W];

    // Four rotating line banks retain the previous, current and future
    // rows while another row is being written. Storage need not be reset:
    // availability checks prevent reads of unwritten pixels.
    reg [PIXEL_W-1:0] line_buffer [0:4*IMG_WIDTH-1];
    reg [GAIN_W-1:0] gain_buffer [0:4*IMG_WIDTH-1];
    integer write_bank, write_x;
    integer read_bank, read_x, read_y;
    integer pending;
    wire issue;
    wire [31:0] frame_remaining;
    assign frame_remaining = FRAME_SIZE - read_y*IMG_WIDTH - read_x;
    assign issue = (pending > 0) &&
                   ((pending > 2*IMG_WIDTH) ||
                    (pending >= frame_remaining));

    // Asynchronous stencil reads and explicitly widened Gaussian sums.
    integer r, c;
    integer neighbor_x, neighbor_y, neighbor_bank;
    reg [PIXEL_W-1:0] stencil_pixel;
    reg [SUM_W-1:0] weighted_pixel;
    reg [SUM_W-1:0] gauss_sum;
    reg [PIXEL_W-1:0] center_pixel;
    reg [GAIN_W-1:0] center_gain;
    always @* begin
        gauss_sum = {SUM_W{1'b0}};
        stencil_pixel = {PIXEL_W{1'b0}};
        weighted_pixel = {SUM_W{1'b0}};
        center_pixel = {PIXEL_W{1'b0}};
        center_gain = {GAIN_W{1'b0}};
        neighbor_x = 0;
        neighbor_y = 0;
        neighbor_bank = 0;
        if (issue) begin
            center_pixel = line_buffer[read_bank*IMG_WIDTH + read_x];
            center_gain = gain_buffer[read_bank*IMG_WIDTH + read_x];
            for (r = 0; r < 3; r = r + 1) begin
                for (c = 0; c < 3; c = c + 1) begin
                    neighbor_x = read_x + c - 1;
                    neighbor_y = read_y + r - 1;
                    neighbor_bank = read_bank + r - 1;
                    if (neighbor_bank < 0)
                        neighbor_bank = neighbor_bank + 4;
                    else if (neighbor_bank >= 4)
                        neighbor_bank = neighbor_bank - 4;
                    stencil_pixel = {PIXEL_W{1'b0}};
                    if ((neighbor_x >= 0) &&
                        (neighbor_x < IMG_WIDTH) &&
                        (neighbor_y >= 0) &&
                        (neighbor_y < IMG_HEIGHT))
                        stencil_pixel = line_buffer[
                            neighbor_bank*IMG_WIDTH + neighbor_x];
                    weighted_pixel = {{4{1'b0}}, stencil_pixel};
                    if (r == 1)
                        weighted_pixel = weighted_pixel << 1;
                    if (c == 1)
                        weighted_pixel = weighted_pixel << 1;
                    gauss_sum = gauss_sum + weighted_pixel;
                end
            end
        end
    end

    reg valid1, valid2, valid3, valid4;
    reg [PIXEL_W-1:0] blurred1;
    reg [PIXEL_W-1:0] original1, original2, original3;
    reg [GAIN_W-1:0] gain1, gain2;
    reg signed [PIXEL_W:0] high_freq2;
    reg signed [SCALE_W-1:0] scaled3;
    reg [PIXEL_W-1:0] result4;
    wire signed [SCALE_W:0] product_full;
    wire signed [SCALE_W:0] sharpened;
    assign product_full = $signed(high_freq2) *
                          $signed({1'b0, gain2});
    assign sharpened = $signed({scaled3[SCALE_W-1], scaled3}) +
                       $signed({{(GAIN_W+1){1'b0}}, original3});

    always @(posedge clk) begin
        if (rst) begin
            input_q <= {INPUT_W{1'b0}};
            write_bank <= 0;
            write_x <= 0;
            read_bank <= 0;
            read_x <= 0;
            read_y <= 0;
            pending <= 0;
            valid1 <= 0;
            valid2 <= 0;
            valid3 <= 0;
            valid4 <= 0;
            blurred1 <= 0;
            original1 <= 0;
            original2 <= 0;
            original3 <= 0;
            gain1 <= 0;
            gain2 <= 0;
            high_freq2 <= 0;
            scaled3 <= 0;
            result4 <= 0;
        end else begin
            input_q <= input_d;

            if (accept_pixel) begin
                line_buffer[write_bank*IMG_WIDTH + write_x]
                    <= accepted_pixel;
                gain_buffer[write_bank*IMG_WIDTH + write_x]
                    <= accepted_gain;
                if (write_x == IMG_WIDTH-1) begin
                    write_x <= 0;
                    if (write_bank == 3)
                        write_bank <= 0;
                    else
                        write_bank <= write_bank + 1;
                end else begin
                    write_x <= write_x + 1;
                end
            end

            case ({accept_pixel, issue})
                2'b10: pending <= pending + 1;
                2'b01: pending <= pending - 1;
                default: pending <= pending;
            endcase

            valid1 <= issue;
            if (issue) begin
                blurred1 <= gauss_sum[SUM_W-1:4];
                original1 <= center_pixel;
                gain1 <= center_gain;
                if (read_x == IMG_WIDTH-1) begin
                    read_x <= 0;
                    if (read_bank == 3)
                        read_bank <= 0;
                    else
                        read_bank <= read_bank + 1;
                    if (read_y == IMG_HEIGHT-1)
                        read_y <= 0;
                    else
                        read_y <= read_y + 1;
                end else begin
                    read_x <= read_x + 1;
                end
            end

            valid2 <= valid1;
            if (valid1) begin
                high_freq2 <= $signed({1'b0, original1}) -
                              $signed({1'b0, blurred1});
                original2 <= original1;
                gain2 <= gain1;
            end

            valid3 <= valid2;
            if (valid2) begin
                scaled3 <= product_full[SCALE_W-1:0];
                original3 <= original2;
            end

            valid4 <= valid3;
            if (valid3) begin
                if (sharpened < 0)
                    result4 <= {PIXEL_W{1'b0}};
                else if (sharpened > 255)
                    result4 <= MAX_PIXEL;
                else
                    result4 <= sharpened[PIXEL_W-1:0];
            end
        end
    end

    assign pixel_out = result4;
    assign valid_out = valid4;
endmodule

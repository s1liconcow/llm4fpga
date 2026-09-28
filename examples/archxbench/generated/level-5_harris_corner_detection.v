module harris_corner #(
    parameter IMG_WIDTH = 128,
    parameter IMG_HEIGHT = 128,
    parameter PIXEL_W = 8,
    parameter GRAD_W = 16,
    parameter RESP_W = 32,
    parameter K_W = 8
) (
    input clk,
    input rst,
    input [PIXEL_W-1:0] pixel_in,
    input valid_in,
    input signed [RESP_W-1:0] threshold,
    input [K_W-1:0] k_param,
    output reg is_corner,
    output reg valid_out
);
    localparam DEPTH = 4*IMG_WIDTH;
    localparam PW = 2*GRAD_W;
    localparam SW = PW+4;
    localparam GW = (GRAD_W > PIXEL_W+4) ? GRAD_W : PIXEL_W+4;
    localparam TW = SW+1;
    localparam QW = 2*TW;
    localparam MW = QW+K_W;
    localparam RW = MW+2;

    // Four rotating line banks retain both lookahead and preceding rows.
    reg [PIXEL_W-1:0] pixel_lines [0:3][0:IMG_WIDTH-1];
    reg [PW-1:0] xx_lines [0:3][0:IMG_WIDTH-1];
    reg [PW-1:0] yy_lines [0:3][0:IMG_WIDTH-1];
    reg signed [PW-1:0] xy_lines [0:3][0:IMG_WIDTH-1];

    integer pixel_write, pixel_head, product_write, product_head;
    integer pixel_count, product_count;
    integer sobel_row, sobel_col, gauss_row, gauss_col;
    integer sobel_ahead, gauss_ahead;
    reg sobel_fire, gauss_fire;

    function integer wrap_index;
        input integer value;
        begin
            if (value < 0)
                wrap_index = value + DEPTH;
            else if (value >= DEPTH)
                wrap_index = value - DEPTH;
            else
                wrap_index = value;
        end
    endfunction

    always @* begin
        sobel_ahead = 0;
        if (sobel_row < IMG_HEIGHT-1)
            sobel_ahead = IMG_WIDTH;
        if (sobel_col < IMG_WIDTH-1)
            sobel_ahead = sobel_ahead + 1;
        gauss_ahead = 0;
        if (gauss_row < IMG_HEIGHT-1)
            gauss_ahead = IMG_WIDTH;
        if (gauss_col < IMG_WIDTH-1)
            gauss_ahead = gauss_ahead + 1;
        sobel_fire = (pixel_count > sobel_ahead);
        gauss_fire = (product_count > gauss_ahead);
    end

    integer sy, sx, si;
    reg signed [GW-1:0] sample_pixel, gx_acc, gy_acc;
    reg signed [GRAD_W-1:0] gx, gy;
    reg [PW-1:0] product_xx, product_yy;
    reg signed [PW-1:0] product_xy;

    always @* begin
        gx_acc = 0;
        gy_acc = 0;
        sample_pixel = 0;
        si = 0;
        for (sy = -1; sy <= 1; sy = sy+1) begin
            for (sx = -1; sx <= 1; sx = sx+1) begin
                sample_pixel = 0;
                if ((sobel_row+sy >= 0) &&
                    (sobel_row+sy < IMG_HEIGHT) &&
                    (sobel_col+sx >= 0) &&
                    (sobel_col+sx < IMG_WIDTH)) begin
                    si = wrap_index(pixel_head + sy*IMG_WIDTH + sx);
                    sample_pixel = $signed({1'b0,
                        pixel_lines[si/IMG_WIDTH][si%IMG_WIDTH]});
                end
                if (sx == -1) begin
                    if (sy == 0) gx_acc = gx_acc - (sample_pixel <<< 1);
                    else gx_acc = gx_acc - sample_pixel;
                end
                if (sx == 1) begin
                    if (sy == 0) gx_acc = gx_acc + (sample_pixel <<< 1);
                    else gx_acc = gx_acc + sample_pixel;
                end
                if (sy == -1) begin
                    if (sx == 0) gy_acc = gy_acc - (sample_pixel <<< 1);
                    else gy_acc = gy_acc - sample_pixel;
                end
                if (sy == 1) begin
                    if (sx == 0) gy_acc = gy_acc + (sample_pixel <<< 1);
                    else gy_acc = gy_acc + sample_pixel;
                end
            end
        end
        gx = gx_acc;
        gy = gy_acc;
        product_xx = gx * gx;
        product_yy = gy * gy;
        product_xy = gx * gy;
    end

    integer ay, ax, ai, weight_shift;
    reg [SW-1:0] xx_sum, yy_sum, xx_tap, yy_tap;
    reg signed [SW-1:0] xy_sum, xy_tap;
    reg [SW-1:0] smooth_xx, smooth_yy;
    reg signed [SW-1:0] smooth_xy;
    reg smooth_valid;

    always @* begin
        xx_sum = 0;
        yy_sum = 0;
        xy_sum = 0;
        xx_tap = 0;
        yy_tap = 0;
        xy_tap = 0;
        ai = 0;
        weight_shift = 0;
        for (ay = -1; ay <= 1; ay = ay+1) begin
            for (ax = -1; ax <= 1; ax = ax+1) begin
                xx_tap = 0;
                yy_tap = 0;
                xy_tap = 0;
                if ((gauss_row+ay >= 0) &&
                    (gauss_row+ay < IMG_HEIGHT) &&
                    (gauss_col+ax >= 0) &&
                    (gauss_col+ax < IMG_WIDTH)) begin
                    ai = wrap_index(product_head + ay*IMG_WIDTH + ax);
                    xx_tap = xx_lines[ai/IMG_WIDTH][ai%IMG_WIDTH];
                    yy_tap = yy_lines[ai/IMG_WIDTH][ai%IMG_WIDTH];
                    xy_tap = $signed(xy_lines[ai/IMG_WIDTH][ai%IMG_WIDTH]);
                end
                weight_shift = 0;
                if (ay == 0) weight_shift = weight_shift + 1;
                if (ax == 0) weight_shift = weight_shift + 1;
                xx_sum = xx_sum + (xx_tap << weight_shift);
                yy_sum = yy_sum + (yy_tap << weight_shift);
                xy_sum = xy_sum + (xy_tap <<< weight_shift);
            end
        end
    end

    reg [TW-1:0] trace_value;
    reg [QW-1:0] trace_squared;
    reg [MW-1:0] weighted_trace;
    reg [2*SW-1:0] diagonal_product;
    reg signed [2*SW-1:0] cross_squared;
    reg signed [RW-1:0] full_response;
    reg signed [RESP_W-1:0] response;
    reg response_valid;

    always @* begin
        trace_value = {1'b0, smooth_xx} + {1'b0, smooth_yy};
        trace_squared = trace_value * trace_value;
        weighted_trace = k_param * trace_squared;
        diagonal_product = smooth_xx * smooth_yy;
        cross_squared = smooth_xy * smooth_xy;
        full_response = $signed({1'b0, diagonal_product})
                      - $signed(cross_squared)
                      - $signed({1'b0, (weighted_trace >> 8)});
    end

    always @(posedge clk) begin
        if (rst) begin
            pixel_write <= 0;
            pixel_head <= 0;
            product_write <= 0;
            product_head <= 0;
            pixel_count <= 0;
            product_count <= 0;
            sobel_row <= 0;
            sobel_col <= 0;
            gauss_row <= 0;
            gauss_col <= 0;
            smooth_xx <= 0;
            smooth_yy <= 0;
            smooth_xy <= 0;
            smooth_valid <= 0;
            response <= 0;
            response_valid <= 0;
            is_corner <= 0;
            valid_out <= 0;
        end else begin
            case ({valid_in, sobel_fire})
                2'b10: pixel_count <= pixel_count + 1;
                2'b01: pixel_count <= pixel_count - 1;
                default: pixel_count <= pixel_count;
            endcase
            case ({sobel_fire, gauss_fire})
                2'b10: product_count <= product_count + 1;
                2'b01: product_count <= product_count - 1;
                default: product_count <= product_count;
            endcase

            if (valid_in) begin
                pixel_lines[pixel_write/IMG_WIDTH][pixel_write%IMG_WIDTH]
                    <= pixel_in;
                pixel_write <= wrap_index(pixel_write+1);
            end

            if (sobel_fire) begin
                xx_lines[product_write/IMG_WIDTH][product_write%IMG_WIDTH]
                    <= product_xx;
                yy_lines[product_write/IMG_WIDTH][product_write%IMG_WIDTH]
                    <= product_yy;
                xy_lines[product_write/IMG_WIDTH][product_write%IMG_WIDTH]
                    <= product_xy;
                product_write <= wrap_index(product_write+1);
                pixel_head <= wrap_index(pixel_head+1);
                if (sobel_col == IMG_WIDTH-1) begin
                    sobel_col <= 0;
                    if (sobel_row == IMG_HEIGHT-1) sobel_row <= 0;
                    else sobel_row <= sobel_row+1;
                end else sobel_col <= sobel_col+1;
            end

            smooth_valid <= gauss_fire;
            if (gauss_fire) begin
                smooth_xx <= xx_sum >> 12;
                smooth_yy <= yy_sum >> 12;
                smooth_xy <= xy_sum >>> 12;
                product_head <= wrap_index(product_head+1);
                if (gauss_col == IMG_WIDTH-1) begin
                    gauss_col <= 0;
                    if (gauss_row == IMG_HEIGHT-1) gauss_row <= 0;
                    else gauss_row <= gauss_row+1;
                end else gauss_col <= gauss_col+1;
            end

            response_valid <= smooth_valid;
            if (smooth_valid) response <= full_response;
            valid_out <= response_valid;
            if (response_valid) is_corner <= (response > threshold);
            else is_corner <= 0;
        end
    end
endmodule

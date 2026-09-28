module pe (
    input [31:0] north_in, west_in,
    input clk, rst,
    output reg [31:0] south_out, east_out,
    output reg [63:0] result
);
    wire signed [63:0] product;
    assign product = $signed(north_in) * $signed(west_in);

    always @(posedge clk) begin
        if (rst) begin
            south_out <= 32'd0;
            east_out  <= 32'd0;
            result    <= 64'd0;
        end else begin
            south_out <= north_in;
            east_out  <= west_in;
            result    <= result + product;
        end
    end
endmodule

module systolic_matrix_mult (
    input [31:0] a_west0, a_west1, a_west2, a_west3,
    input [31:0] b_north0, b_north1, b_north2, b_north3,
    input clk, rst,
    output reg done,
    output [63:0] result0, result1, result2, result3,
    output [63:0] result4, result5, result6, result7,
    output [63:0] result8, result9, result10, result11,
    output [63:0] result12, result13, result14, result15
);
    wire [31:0] a_flow [0:3][0:4];
    wire [31:0] b_flow [0:4][0:3];
    wire [63:0] results [0:15];
    reg [3:0] cycle_count;

    assign a_flow[0][0] = a_west0;
    assign a_flow[1][0] = a_west1;
    assign a_flow[2][0] = a_west2;
    assign a_flow[3][0] = a_west3;
    assign b_flow[0][0] = b_north0;
    assign b_flow[0][1] = b_north1;
    assign b_flow[0][2] = b_north2;
    assign b_flow[0][3] = b_north3;

    genvar i, j;
    generate
        for (i = 0; i < 4; i = i + 1) begin : rows
            for (j = 0; j < 4; j = j + 1) begin : columns
                pe u_pe (
                    .north_in(b_flow[i][j]),
                    .west_in(a_flow[i][j]),
                    .clk(clk),
                    .rst(rst),
                    .south_out(b_flow[i+1][j]),
                    .east_out(a_flow[i][j+1]),
                    .result(results[i*4+j])
                );
            end
        end
    endgenerate

    assign result0  = results[0];
    assign result1  = results[1];
    assign result2  = results[2];
    assign result3  = results[3];
    assign result4  = results[4];
    assign result5  = results[5];
    assign result6  = results[6];
    assign result7  = results[7];
    assign result8  = results[8];
    assign result9  = results[9];
    assign result10 = results[10];
    assign result11 = results[11];
    assign result12 = results[12];
    assign result13 = results[13];
    assign result14 = results[14];
    assign result15 = results[15];

    // Conservative completion includes two additional pipeline-drain clocks.
    // Saturation keeps done asserted until the next synchronous reset.
    always @(posedge clk) begin
        if (rst) begin
            cycle_count <= 4'd0;
            done <= 1'b0;
        end else if (!done) begin
            if (cycle_count == 4'd11) begin
                cycle_count <= 4'd12;
                done <= 1'b1;
            end else begin
                cycle_count <= cycle_count + 4'd1;
            end
        end
    end
endmodule

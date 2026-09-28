module fp_adder_pipeline #(
    parameter LATENCY = 5
) (
    input clk,
    input rst,
    input [31:0] a,
    input [31:0] b,
    input add_sub,
    input valid_in,
    output [31:0] result,
    output valid_out
);
    // Five arithmetic stages. Register boundaries are distributed across
    // these stages to support any positive LATENCY.
    localparam D1 = LATENCY / 5;
    localparam D2 = (2 * LATENCY) / 5 - LATENCY / 5;
    localparam D3 = (3 * LATENCY) / 5 - (2 * LATENCY) / 5;
    localparam D4 = (4 * LATENCY) / 5 - (3 * LATENCY) / 5;
    localparam D5 = LATENCY - (4 * LATENCY) / 5;

    // Stage 1: unpack, apply subtraction to B's sign, classify specials.
    function [98:0] unpack;
        input [31:0] aa;
        input [31:0] bb;
        input sub;
        reg [98:0] t;
        reg sa, sb;
        reg na, nb, ia, ib;
        reg [31:0] special_value;
        begin
            t = 99'b0;
            sa = aa[31];
            sb = bb[31] ^ sub;
            na = (&aa[30:23]) && (|aa[22:0]);
            nb = (&bb[30:23]) && (|bb[22:0]);
            ia = (&aa[30:23]) && !(|aa[22:0]);
            ib = (&bb[30:23]) && !(|bb[22:0]);
            special_value = 32'b0;
            if (na || nb)
                special_value = 32'h7fc00000;
            else if (ia && ib && (sa != sb))
                special_value = 32'h7fc00000;
            else if (ia)
                special_value = {sa, 8'hff, 23'b0};
            else if (ib)
                special_value = {sb, 8'hff, 23'b0};

            t[23:0] = {(|aa[30:23]), aa[22:0]};
            t[47:24] = {(|bb[30:23]), bb[22:0]};
            t[55:48] = (aa[30:23] == 0) ? 8'b0 : aa[30:23] - 8'd1;
            t[63:56] = (bb[30:23] == 0) ? 8'b0 : bb[30:23] - 8'd1;
            t[64] = sa;
            t[65] = sb;
            t[66] = na || nb || ia || ib;
            t[98:67] = special_value;
            unpack = t;
        end
    endfunction

    // Stage 2: exact alignment onto a common 2^-149 binary grid.
    // 278 bits include the carry from adding two largest finite values.
    function [590:0] align_operands;
        input [98:0] u;
        reg [590:0] t;
        begin
            t = 591'b0;
            t[277:0] = {254'b0, u[23:0]} << u[55:48];
            t[555:278] = {254'b0, u[47:24]} << u[63:56];
            t[556] = u[64];
            t[557] = u[65];
            t[558] = u[66];
            t[590:559] = u[98:67];
            align_operands = t;
        end
    endfunction

    // Stage 3: magnitude arithmetic and exact-zero sign selection.
    function [311:0] add_magnitudes;
        input [590:0] u;
        reg [311:0] t;
        reg [277:0] x, y;
        begin
            t = 312'b0;
            x = u[277:0];
            y = u[555:278];
            if (u[556] == u[557]) begin
                t[277:0] = x + y;
                t[278] = u[556];
            end else if (x > y) begin
                t[277:0] = x - y;
                t[278] = u[556];
            end else if (y > x) begin
                t[277:0] = y - x;
                t[278] = u[557];
            end else begin
                t[277:0] = 278'b0;
                t[278] = 1'b0;
            end
            t[279] = u[558];
            t[311:280] = u[590:559];
            add_magnitudes = t;
        end
    endfunction

    // Stage 4: leading-one detection, normalization, guard and sticky.
    function [67:0] normalize;
        input [311:0] u;
        reg [67:0] t;
        reg [277:0] mag, shifted;
        integer highest;
        integer distance;
        integer j;
        begin
            t = 68'b0;
            mag = u[277:0];
            shifted = 278'b0;
            highest = -1;
            distance = 0;
            for (j = 0; j < 278; j = j + 1)
                if (mag[j]) highest = j;

            if (highest < 23) begin
                // Zero and subnormal sums are already exact on this grid.
                t[23:0] = mag[23:0];
                t[31:24] = 8'b0;
            end else begin
                distance = highest - 23;
                shifted = mag >> distance;
                t[23:0] = shifted[23:0];
                t[31:24] = highest - 22;
                if (distance > 0) begin
                    t[33] = mag[distance - 1];
                    for (j = 0; j < 278; j = j + 1)
                        if (j < distance - 1)
                            t[34] = t[34] | mag[j];
                end
            end
            t[32] = u[278];
            t[35] = u[279];
            t[67:36] = u[311:280];
            normalize = t;
        end
    endfunction

    // Stage 5: round to nearest, ties to even, and encode.
    function [31:0] round_pack;
        input [67:0] u;
        reg [24:0] rounded;
        reg [7:0] exponent;
        reg increment;
        begin
            increment = u[33] && (u[34] || u[0]);
            rounded = {1'b0, u[23:0]} + {{24{1'b0}}, increment};
            exponent = u[31:24];
            if (u[35]) begin
                round_pack = u[67:36];
            end else if (exponent == 8'hff) begin
                round_pack = {u[32], 8'hff, 23'b0};
            end else begin
                if (rounded[24]) begin
                    rounded = rounded >> 1;
                    exponent = exponent + 8'd1;
                end else if ((exponent == 0) && rounded[23]) begin
                    exponent = 8'd1;
                end
                if (exponent == 8'hff)
                    round_pack = {u[32], 8'hff, 23'b0};
                else
                    round_pack = {u[32], exponent, rounded[22:0]};
            end
        end
    endfunction

    wire [98:0] s1_comb, s1;
    wire [590:0] s2_comb, s2;
    wire [311:0] s3_comb, s3;
    wire [67:0] s4_comb, s4;
    wire [31:0] s5_comb;
    wire v1, v2, v3, v4;

    assign s1_comb = unpack(a, b, add_sub);
    assign s2_comb = align_operands(s1);
    assign s3_comb = add_magnitudes(s2);
    assign s4_comb = normalize(s3);
    assign s5_comb = round_pack(s4);

    fp_adder_pipeline_delay #(.WIDTH(99), .DEPTH(D1)) p1 (
        .clk(clk), .rst(rst), .din(s1_comb), .vin(valid_in),
        .dout(s1), .vout(v1)
    );
    fp_adder_pipeline_delay #(.WIDTH(591), .DEPTH(D2)) p2 (
        .clk(clk), .rst(rst), .din(s2_comb), .vin(v1),
        .dout(s2), .vout(v2)
    );
    fp_adder_pipeline_delay #(.WIDTH(312), .DEPTH(D3)) p3 (
        .clk(clk), .rst(rst), .din(s3_comb), .vin(v2),
        .dout(s3), .vout(v3)
    );
    fp_adder_pipeline_delay #(.WIDTH(68), .DEPTH(D4)) p4 (
        .clk(clk), .rst(rst), .din(s4_comb), .vin(v3),
        .dout(s4), .vout(v4)
    );
    fp_adder_pipeline_delay #(.WIDTH(32), .DEPTH(D5)) p5 (
        .clk(clk), .rst(rst), .din(s5_comb), .vin(v4),
        .dout(result), .vout(valid_out)
    );
endmodule

module fp_adder_pipeline_delay #(
    parameter WIDTH = 32,
    parameter DEPTH = 1
) (
    input clk,
    input rst,
    input [WIDTH-1:0] din,
    input vin,
    output [WIDTH-1:0] dout,
    output vout
);
    generate
        if (DEPTH == 0) begin : bypass
            assign dout = din;
            assign vout = vin;
        end else begin : registered_delay
            reg [WIDTH-1:0] data_pipe [0:DEPTH-1];
            reg [DEPTH-1:0] valid_pipe;
            integer i;
            always @(posedge clk) begin
                if (rst) begin
                    valid_pipe <= {DEPTH{1'b0}};
                    for (i = 0; i < DEPTH; i = i + 1)
                        data_pipe[i] <= {WIDTH{1'b0}};
                end else begin
                    data_pipe[0] <= din;
                    valid_pipe[0] <= vin;
                    for (i = 1; i < DEPTH; i = i + 1) begin
                        data_pipe[i] <= data_pipe[i-1];
                        valid_pipe[i] <= valid_pipe[i-1];
                    end
                end
            end
            assign dout = data_pipe[DEPTH-1];
            assign vout = valid_pipe[DEPTH-1];
        end
    endgenerate
endmodule

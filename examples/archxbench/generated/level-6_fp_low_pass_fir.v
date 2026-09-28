module fp_lowpass_fir #(
    parameter TAP_CNT = 51,
    parameter PIPE_DEPTH = 2
) (
    input clk,
    input rst,
    input valid_in,
    input [31:0] data_in,
    output valid_out,
    output [31:0] data_out
);
    function integer clog2;
        input integer n;
        integer t;
        begin
            t = n - 1;
            clog2 = 0;
            while (t > 0) begin
                clog2 = clog2 + 1;
                t = t >> 1;
            end
        end
    endfunction

    function [31:0] reciprocal_coefficient;
        input integer n;
        integer c, p;
        reg [63:0] numerator, q, rem_value;
        reg [7:0] exponent_field;
        begin
            c = 0;
            p = 1;
            while (p < n) begin
                p = p << 1;
                c = c + 1;
            end
            numerator = 64'd1 << (23+c);
            q = numerator / n;
            rem_value = numerator % n;
            if (((rem_value << 1) > n) ||
                (((rem_value << 1) == n) && q[0]))
                q = q + 1;
            exponent_field = 127-c;
            if (q[24]) begin
                q = q >> 1;
                exponent_field = exponent_field + 1'b1;
            end
            reciprocal_coefficient = {1'b0, exponent_field, q[22:0]};
        end
    endfunction

    localparam integer LEVELS = clog2(TAP_CNT);
    localparam integer LEAVES = 1 << LEVELS;
    localparam integer DEPTH = (PIPE_DEPTH < 1) ? 1 : PIPE_DEPTH;
    localparam integer PAD = TAP_CNT - 1;
    localparam integer LATENCY = PAD + DEPTH * (LEVELS + 1);

    // Preloadable coefficient memory. Default ROM is a moving-average LPF.
    reg [31:0] coeffs_mem [0:TAP_CNT-1];
    reg [31:0] sample [0:TAP_CNT-1];
    integer ci;
    initial begin
        for (ci = 0; ci < TAP_CNT; ci = ci + 1)
            coeffs_mem[ci] = reciprocal_coefficient(TAP_CNT);
    end

    integer si;
    always @(posedge clk) begin
        if (rst) begin
            for (si = 0; si < TAP_CNT; si = si + 1)
                sample[si] <= 32'b0;
        end else if (valid_in) begin
            sample[0] <= data_in;
            for (si = 1; si < TAP_CNT; si = si + 1)
                sample[si] <= sample[si-1];
        end
    end

    wire [31:0] tree [0:2*LEAVES-2];
    genvar tap, node;
    generate
        for (tap = 0; tap < LEAVES; tap = tap + 1) begin: multipliers
            if (tap < TAP_CNT) begin: active_tap
                wire [31:0] operand;
                wire [31:0] product;
                reg [31:0] pipe [0:DEPTH-1];
                integer d;
                if (tap == 0) begin: current_sample
                    assign operand = data_in;
                end else begin: previous_sample
                    assign operand = sample[tap-1];
                end
                fir_binary32_mul arithmetic (
                    .a(operand), .b(coeffs_mem[tap]), .y(product)
                );
                always @(posedge clk) begin
                    if (rst) begin
                        for (d = 0; d < DEPTH; d = d + 1)
                            pipe[d] <= 32'b0;
                    end else begin
                        pipe[0] <= product;
                        for (d = 1; d < DEPTH; d = d + 1)
                            pipe[d] <= pipe[d-1];
                    end
                end
                assign tree[LEAVES-1+tap] = pipe[DEPTH-1];
            end else begin: padding_tap
                assign tree[LEAVES-1+tap] = 32'b0;
            end
        end
        for (node = 0; node < LEAVES-1; node = node + 1) begin: adders
            wire [31:0] sum;
            reg [31:0] pipe [0:DEPTH-1];
            integer d;
            fir_binary32_add arithmetic (
                .a(tree[2*node+1]), .b(tree[2*node+2]), .y(sum)
            );
            always @(posedge clk) begin
                if (rst) begin
                    for (d = 0; d < DEPTH; d = d + 1)
                        pipe[d] <= 32'b0;
                end else begin
                    pipe[0] <= sum;
                    for (d = 1; d < DEPTH; d = d + 1)
                        pipe[d] <= pipe[d-1];
                end
            end
            assign tree[node] = pipe[DEPTH-1];
        end
    endgenerate

    reg [31:0] output_delay [0:PAD-1];
    reg [LATENCY-1:0] valid_pipe;
    integer oi;
    always @(posedge clk) begin
        if (rst) begin
            valid_pipe <= 0;
            for (oi = 0; oi < PAD; oi = oi + 1)
                output_delay[oi] <= 0;
        end else begin
            valid_pipe <= {valid_pipe[LATENCY-2:0], valid_in};
            output_delay[0] <= tree[0];
            for (oi = 1; oi < PAD; oi = oi + 1)
                output_delay[oi] <= output_delay[oi-1];
        end
    end
    assign valid_out = valid_pipe[LATENCY-1];
    assign data_out = output_delay[PAD-1];
endmodule

// Compact logarithmic normalization, shared across operator instances.
module fir_normalize24 (
    input [23:0] x,
    output [23:0] mantissa,
    output [4:0] distance
);
    wire s16 = (x[23:8] == 0);
    wire [23:0] v16 = s16 ? {x[7:0],16'b0} : x;
    wire s8 = (v16[23:16] == 0);
    wire [23:0] v8 = s8 ? {v16[15:0],8'b0} : v16;
    wire s4 = (v8[23:20] == 0);
    wire [23:0] v4 = s4 ? {v8[19:0],4'b0} : v8;
    wire s2 = (v4[23:22] == 0);
    wire [23:0] v2 = s2 ? {v4[21:0],2'b0} : v4;
    wire s1 = !v2[23];
    assign mantissa = s1 ? {v2[22:0],1'b0} : v2;
    assign distance = {s16,s8,s4,s2,s1};
endmodule

// Right shift with sticky-bit accumulation. Five small mux layers replace
// wide variable masks and per-bit, integer-width comparison loops.
module fir_shift_jam27 (
    input [26:0] x,
    input [10:0] distance,
    output [26:0] y
);
    wire [26:0] v16 = distance[4] ?
        {16'b0,x[26:17],|x[16:0]} : x;
    wire [26:0] v8 = distance[3] ?
        {8'b0,v16[26:9],|v16[8:0]} : v16;
    wire [26:0] v4 = distance[2] ?
        {4'b0,v8[26:5],|v8[4:0]} : v8;
    wire [26:0] v2 = distance[1] ?
        {2'b0,v4[26:3],|v4[2:0]} : v4;
    wire [26:0] v1 = distance[0] ?
        {1'b0,v2[26:2],|v2[1:0]} : v2;
    assign y = (distance >= 11'd27) ? {26'b0,|x} : v1;
endmodule

// The leading significand bit is bit 26; bits 2:0 are guard/round/sticky.
module fir_binary32_pack (
    input sign_bit,
    input signed [10:0] exponent,
    input [26:0] significand,
    output reg [31:0] y
);
    wire tiny = (exponent < -11'sd126);
    wire [10:0] shift_amount = tiny ? (-11'sd126 - exponent) : 11'd0;
    wire [26:0] shifted;
    fir_shift_jam27 underflow_shift (
        .x(significand), .distance(shift_amount), .y(shifted)
    );
    reg [24:0] rounded;
    reg signed [10:0] e;
    reg [7:0] biased;
    always @* begin
        e = tiny ? -11'sd126 : exponent;
        rounded = {1'b0,shifted[26:3]};
        if (shifted[2] && (shifted[1] || shifted[0] || rounded[0]))
            rounded = rounded + 25'd1;
        if (rounded[24]) begin
            rounded = rounded >> 1;
            e = e + 11'sd1;
        end
        biased = e + 11'sd127;
        if (rounded == 0)
            y = {sign_bit,31'b0};
        else if (e > 11'sd127)
            y = {sign_bit,8'hff,23'b0};
        else if ((e == -11'sd126) && !rounded[23])
            y = {sign_bit,8'b0,rounded[22:0]};
        else
            y = {sign_bit,biased,rounded[22:0]};
    end
endmodule

module fir_binary32_mul (
    input [31:0] a,
    input [31:0] b,
    output reg [31:0] y
);
    wire sign_bit = a[31] ^ b[31];
    wire [23:0] raw_a = {(a[30:23] != 0),a[22:0]};
    wire [23:0] raw_b = {(b[30:23] != 0),b[22:0]};
    wire [23:0] ma, mb;
    wire [4:0] da, db;
    fir_normalize24 normalize_a (.x(raw_a), .mantissa(ma), .distance(da));
    fir_normalize24 normalize_b (.x(raw_b), .mantissa(mb), .distance(db));
    wire signed [10:0] ea = (a[30:23] == 0) ? -11'sd126 :
        ($signed({3'b0,a[30:23]}) - 11'sd127);
    wire signed [10:0] eb = (b[30:23] == 0) ? -11'sd126 :
        ($signed({3'b0,b[30:23]}) - 11'sd127);
    wire [47:0] product = ma * mb;
    wire signed [10:0] exponent = ea + eb - $signed({6'b0,da}) -
        $signed({6'b0,db}) + (product[47] ? 11'sd1 : 11'sd0);
    wire [26:0] significand = product[47] ?
        {product[47:22],|product[21:0]} :
        {product[46:21],|product[20:0]};
    wire [31:0] packed_value;
    fir_binary32_pack pack_result (
        .sign_bit(sign_bit), .exponent(exponent),
        .significand(significand), .y(packed_value)
    );
    always @* begin
        if (((a[30:23] == 8'hff) && (a[22:0] != 0)) ||
            ((b[30:23] == 8'hff) && (b[22:0] != 0)))
            y = 32'h7fc00000;
        else if (((a[30:23] == 8'hff) && (b[30:0] == 0)) ||
                 ((b[30:23] == 8'hff) && (a[30:0] == 0)))
            y = 32'h7fc00000;
        else if ((a[30:23] == 8'hff) || (b[30:23] == 8'hff))
            y = {sign_bit,8'hff,23'b0};
        else if ((a[30:0] == 0) || (b[30:0] == 0))
            y = {sign_bit,31'b0};
        else
            y = packed_value;
    end
endmodule

module fir_binary32_add (
    input [31:0] a,
    input [31:0] b,
    output reg [31:0] y
);
    wire [23:0] raw_a = {(a[30:23] != 0),a[22:0]};
    wire [23:0] raw_b = {(b[30:23] != 0),b[22:0]};
    wire [23:0] ma, mb;
    wire [4:0] da, db;
    fir_normalize24 normalize_a (.x(raw_a), .mantissa(ma), .distance(da));
    fir_normalize24 normalize_b (.x(raw_b), .mantissa(mb), .distance(db));
    wire signed [10:0] base_ea = (a[30:23] == 0) ? -11'sd126 :
        ($signed({3'b0,a[30:23]}) - 11'sd127);
    wire signed [10:0] base_eb = (b[30:23] == 0) ? -11'sd126 :
        ($signed({3'b0,b[30:23]}) - 11'sd127);
    wire signed [10:0] ea = base_ea - $signed({6'b0,da});
    wire signed [10:0] eb = base_eb - $signed({6'b0,db});
    wire swap_ab = (ea < eb) || ((ea == eb) && (ma < mb));
    wire [26:0] large_sig = swap_ab ? {mb,3'b0} : {ma,3'b0};
    wire [26:0] small_sig = swap_ab ? {ma,3'b0} : {mb,3'b0};
    wire signed [10:0] large_e = swap_ab ? eb : ea;
    wire [10:0] distance = swap_ab ? (eb-ea) : (ea-eb);
    wire large_sign = swap_ab ? b[31] : a[31];
    wire same_sign = (a[31] == b[31]);
    wire [26:0] aligned_sig;
    fir_shift_jam27 align_small (
        .x(small_sig), .distance(distance), .y(aligned_sig)
    );
    wire [27:0] sum_sig = {1'b0,large_sig} + {1'b0,aligned_sig};
    wire [26:0] difference_sig = large_sig - aligned_sig;

    wire s16 = (difference_sig[26:11] == 0);
    wire [26:0] v16 = s16 ? {difference_sig[10:0],16'b0} : difference_sig;
    wire s8 = (v16[26:19] == 0);
    wire [26:0] v8 = s8 ? {v16[18:0],8'b0} : v16;
    wire s4 = (v8[26:23] == 0);
    wire [26:0] v4 = s4 ? {v8[22:0],4'b0} : v8;
    wire s2 = (v4[26:25] == 0);
    wire [26:0] v2 = s2 ? {v4[24:0],2'b0} : v4;
    wire s1 = !v2[26];
    wire [26:0] normalized_difference = s1 ? {v2[25:0],1'b0} : v2;
    wire [4:0] normalization_distance = {s16,s8,s4,s2,s1};

    wire [26:0] significand = same_sign ?
        (sum_sig[27] ? {sum_sig[27:2],|sum_sig[1:0]} : sum_sig[26:0]) :
        normalized_difference;
    wire signed [10:0] exponent = same_sign ?
        (large_e + (sum_sig[27] ? 11'sd1 : 11'sd0)) :
        (large_e - $signed({6'b0,normalization_distance}));
    wire result_sign = (!same_sign && (difference_sig == 0)) ? 1'b0 : large_sign;
    wire [31:0] packed_value;
    fir_binary32_pack pack_result (
        .sign_bit(result_sign), .exponent(exponent),
        .significand(significand), .y(packed_value)
    );
    always @* begin
        if (((a[30:23] == 8'hff) && (a[22:0] != 0)) ||
            ((b[30:23] == 8'hff) && (b[22:0] != 0)))
            y = 32'h7fc00000;
        else if ((a[30:23] == 8'hff) && (b[30:23] == 8'hff) && !same_sign)
            y = 32'h7fc00000;
        else if (a[30:23] == 8'hff)
            y = a;
        else if (b[30:23] == 8'hff)
            y = b;
        else if ((a[30:0] == 0) && (b[30:0] == 0))
            y = {a[31] & b[31],31'b0};
        else if (a[30:0] == 0)
            y = b;
        else if (b[30:0] == 0)
            y = a;
        else
            y = packed_value;
    end
endmodule

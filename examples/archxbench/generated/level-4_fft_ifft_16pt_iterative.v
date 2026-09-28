module fft16_iterative #(
    parameter N = 16,
    parameter DATA_W = 12,
    parameter COEFF_W = 16,
    parameter GAIN_W = 4
) (
    input clk,
    input rst,
    input start,
    input mode,
    input [N*DATA_W-1:0] data_real_in,
    input [N*DATA_W-1:0] data_imag_in,
    output reg [N*(DATA_W+GAIN_W)-1:0] data_real_out,
    output reg [N*(DATA_W+GAIN_W)-1:0] data_imag_out,
    output reg done
);
    localparam OUT_W = DATA_W + GAIN_W;
    localparam LOG_N = $clog2(N);
    localparam FRAC = COEFF_W - 1;
    localparam PROD_W = OUT_W + COEFF_W + 1;
    localparam SUM_W = PROD_W + 1;
    localparam ROM_SIZE = (N > 1) ? N/2 : 1;
    localparam [1:0] IDLE = 0, EXEC = 1, PUBLISH = 2;
    localparam signed [SUM_W-1:0] ROUND_BIAS =
        {{(SUM_W-1){1'b0}}, 1'b1} << (COEFF_W-2);

    reg [1:0] state;
    reg inverse;
    reg signed [OUT_W-1:0] real_mem [0:N-1];
    reg signed [OUT_W-1:0] imag_mem [0:N-1];
    wire signed [COEFF_W-1:0] twiddle_real [0:ROM_SIZE-1];
    wire signed [COEFF_W-1:0] twiddle_imag [0:ROM_SIZE-1];
    integer half_span, stride, base, position;
    integer i;

    // All real arithmetic below is evaluated at elaboration only.
    genvar k;
    generate
        for (k = 0; k < ROM_SIZE; k = k + 1) begin : twiddle_rom
            localparam real SCALE = 2.0 ** (COEFF_W-1);
            localparam real ANGLE = 6.28318530717958647692 * k / N;
            localparam real CR = $cos(ANGLE) * SCALE;
            localparam real CI = -$sin(ANGLE) * SCALE;
            localparam integer QR = (CR >= 0.0) ?
                $rtoi(CR + 0.5) : $rtoi(CR - 0.5);
            localparam integer QI = (CI >= 0.0) ?
                $rtoi(CI + 0.5) : $rtoi(CI - 0.5);
            localparam integer MAX_COEFF = $rtoi(SCALE - 1.0);
            localparam integer MIN_COEFF = -$rtoi(SCALE);
            assign twiddle_real[k] = (QR > MAX_COEFF) ? MAX_COEFF :
                                      (QR < MIN_COEFF) ? MIN_COEFF : QR;
            assign twiddle_imag[k] = (QI > MAX_COEFF) ? MAX_COEFF :
                                      (QI < MIN_COEFF) ? MIN_COEFF : QI;
        end
    endgenerate

    function integer reverse_index;
        input integer value;
        integer b;
        begin
            reverse_index = 0;
            for (b = 0; b < LOG_N; b = b + 1)
                reverse_index = (reverse_index << 1) |
                                ((value >> b) & 1);
        end
    endfunction

    wire [31:0] addr_a = base + position;
    wire [31:0] addr_b = base + position + half_span;
    wire [31:0] tw_addr = position * stride;
    wire signed [COEFF_W:0] wr =
        {twiddle_real[tw_addr][COEFF_W-1], twiddle_real[tw_addr]};
    wire signed [COEFF_W:0] wi_forward =
        {twiddle_imag[tw_addr][COEFF_W-1], twiddle_imag[tw_addr]};
    // Extend before conjugation so -32768 can become +32768.
    wire signed [COEFF_W:0] wi = inverse ? -wi_forward : wi_forward;
    wire signed [OUT_W-1:0] ar = real_mem[addr_a];
    wire signed [OUT_W-1:0] ai = imag_mem[addr_a];
    wire signed [OUT_W-1:0] br = real_mem[addr_b];
    wire signed [OUT_W-1:0] bi = imag_mem[addr_b];

    wire signed [PROD_W-1:0] rr = br * wr;
    wire signed [PROD_W-1:0] ii = bi * wi;
    wire signed [PROD_W-1:0] ri = br * wi;
    wire signed [PROD_W-1:0] ir = bi * wr;
    wire signed [SUM_W-1:0] product_real =
        $signed({rr[PROD_W-1], rr}) - $signed({ii[PROD_W-1], ii});
    wire signed [SUM_W-1:0] product_imag =
        $signed({ri[PROD_W-1], ri}) + $signed({ir[PROD_W-1], ir});
    wire signed [SUM_W-1:0] rounded_real = product_real + ROUND_BIAS;
    wire signed [SUM_W-1:0] rounded_imag = product_imag + ROUND_BIAS;
    wire signed [OUT_W-1:0] tr = rounded_real >>> FRAC;
    wire signed [OUT_W-1:0] ti = rounded_imag >>> FRAC;

    always @(posedge clk) begin
        if (rst) begin
            state <= IDLE;
            inverse <= 0;
            half_span <= 1;
            stride <= N/2;
            base <= 0;
            position <= 0;
            done <= 0;
            data_real_out <= 0;
            data_imag_out <= 0;
            for (i = 0; i < N; i = i + 1) begin
                real_mem[i] <= 0;
                imag_mem[i] <= 0;
            end
        end else begin
            done <= 0;
            case (state)
                IDLE: begin
                    if (start) begin
                        inverse <= mode;
                        for (i = 0; i < N; i = i + 1) begin
                            real_mem[reverse_index(i)] <=
                                $signed(data_real_in[i*DATA_W +: DATA_W]);
                            imag_mem[reverse_index(i)] <=
                                $signed(data_imag_in[i*DATA_W +: DATA_W]);
                        end
                        half_span <= 1;
                        stride <= N/2;
                        base <= 0;
                        position <= 0;
                        state <= (N == 1) ? PUBLISH : EXEC;
                    end
                end
                EXEC: begin
                    real_mem[addr_a] <= ar + tr;
                    imag_mem[addr_a] <= ai + ti;
                    real_mem[addr_b] <= ar - tr;
                    imag_mem[addr_b] <= ai - ti;
                    if (position == half_span-1) begin
                        position <= 0;
                        if (base + 2*half_span >= N) begin
                            base <= 0;
                            if (half_span == N/2) begin
                                state <= PUBLISH;
                            end else begin
                                half_span <= half_span << 1;
                                stride <= stride >> 1;
                            end
                        end else begin
                            base <= base + 2*half_span;
                        end
                    end else begin
                        position <= position + 1;
                    end
                end
                PUBLISH: begin
                    for (i = 0; i < N; i = i + 1) begin
                        data_real_out[i*OUT_W +: OUT_W] <= inverse ?
                            (real_mem[i] >>> LOG_N) : real_mem[i];
                        data_imag_out[i*OUT_W +: OUT_W] <= inverse ?
                            (imag_mem[i] >>> LOG_N) : imag_mem[i];
                    end
                    done <= 1;
                    state <= IDLE;
                end
                default: state <= IDLE;
            endcase
        end
    end
endmodule

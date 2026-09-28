module aes128_pipeline #(
    parameter PIPELINED = 1,
    parameter UNROLL = 1,
    parameter INLINE_KEY_EXP = 1
) (
    input clk,
    input rst,
    input start,
    input mode,
    input [127:0] data_in,
    input [127:0] key_in,
    input valid_in,
    output reg [127:0] data_out,
    output reg valid_out,
    output reg done
);
    localparam integer ROUNDS_PER_STAGE = (PIPELINED == 0) ? 10 :
        ((UNROLL < 1) ? 1 : ((UNROLL > 10) ? 10 : UNROLL));
    localparam integer STAGES = (10 + ROUNDS_PER_STAGE - 1) / ROUNDS_PER_STAGE;

    // Explicit lookup avoids wide variable shifts and index arithmetic.
    function [7:0] sbox;
        input [7:0] x;
        begin
            case (x)
                8'h00: sbox=8'h63; 8'h01: sbox=8'h7c;
                8'h02: sbox=8'h77; 8'h03: sbox=8'h7b;
                8'h04: sbox=8'hf2; 8'h05: sbox=8'h6b;
                8'h06: sbox=8'h6f; 8'h07: sbox=8'hc5;
                8'h08: sbox=8'h30; 8'h09: sbox=8'h01;
                8'h0a: sbox=8'h67; 8'h0b: sbox=8'h2b;
                8'h0c: sbox=8'hfe; 8'h0d: sbox=8'hd7;
                8'h0e: sbox=8'hab; 8'h0f: sbox=8'h76;
                8'h10: sbox=8'hca; 8'h11: sbox=8'h82;
                8'h12: sbox=8'hc9; 8'h13: sbox=8'h7d;
                8'h14: sbox=8'hfa; 8'h15: sbox=8'h59;
                8'h16: sbox=8'h47; 8'h17: sbox=8'hf0;
                8'h18: sbox=8'had; 8'h19: sbox=8'hd4;
                8'h1a: sbox=8'ha2; 8'h1b: sbox=8'haf;
                8'h1c: sbox=8'h9c; 8'h1d: sbox=8'ha4;
                8'h1e: sbox=8'h72; 8'h1f: sbox=8'hc0;
                8'h20: sbox=8'hb7; 8'h21: sbox=8'hfd;
                8'h22: sbox=8'h93; 8'h23: sbox=8'h26;
                8'h24: sbox=8'h36; 8'h25: sbox=8'h3f;
                8'h26: sbox=8'hf7; 8'h27: sbox=8'hcc;
                8'h28: sbox=8'h34; 8'h29: sbox=8'ha5;
                8'h2a: sbox=8'he5; 8'h2b: sbox=8'hf1;
                8'h2c: sbox=8'h71; 8'h2d: sbox=8'hd8;
                8'h2e: sbox=8'h31; 8'h2f: sbox=8'h15;
                8'h30: sbox=8'h04; 8'h31: sbox=8'hc7;
                8'h32: sbox=8'h23; 8'h33: sbox=8'hc3;
                8'h34: sbox=8'h18; 8'h35: sbox=8'h96;
                8'h36: sbox=8'h05; 8'h37: sbox=8'h9a;
                8'h38: sbox=8'h07; 8'h39: sbox=8'h12;
                8'h3a: sbox=8'h80; 8'h3b: sbox=8'he2;
                8'h3c: sbox=8'heb; 8'h3d: sbox=8'h27;
                8'h3e: sbox=8'hb2; 8'h3f: sbox=8'h75;
                8'h40: sbox=8'h09; 8'h41: sbox=8'h83;
                8'h42: sbox=8'h2c; 8'h43: sbox=8'h1a;
                8'h44: sbox=8'h1b; 8'h45: sbox=8'h6e;
                8'h46: sbox=8'h5a; 8'h47: sbox=8'ha0;
                8'h48: sbox=8'h52; 8'h49: sbox=8'h3b;
                8'h4a: sbox=8'hd6; 8'h4b: sbox=8'hb3;
                8'h4c: sbox=8'h29; 8'h4d: sbox=8'he3;
                8'h4e: sbox=8'h2f; 8'h4f: sbox=8'h84;
                8'h50: sbox=8'h53; 8'h51: sbox=8'hd1;
                8'h52: sbox=8'h00; 8'h53: sbox=8'hed;
                8'h54: sbox=8'h20; 8'h55: sbox=8'hfc;
                8'h56: sbox=8'hb1; 8'h57: sbox=8'h5b;
                8'h58: sbox=8'h6a; 8'h59: sbox=8'hcb;
                8'h5a: sbox=8'hbe; 8'h5b: sbox=8'h39;
                8'h5c: sbox=8'h4a; 8'h5d: sbox=8'h4c;
                8'h5e: sbox=8'h58; 8'h5f: sbox=8'hcf;
                8'h60: sbox=8'hd0; 8'h61: sbox=8'hef;
                8'h62: sbox=8'haa; 8'h63: sbox=8'hfb;
                8'h64: sbox=8'h43; 8'h65: sbox=8'h4d;
                8'h66: sbox=8'h33; 8'h67: sbox=8'h85;
                8'h68: sbox=8'h45; 8'h69: sbox=8'hf9;
                8'h6a: sbox=8'h02; 8'h6b: sbox=8'h7f;
                8'h6c: sbox=8'h50; 8'h6d: sbox=8'h3c;
                8'h6e: sbox=8'h9f; 8'h6f: sbox=8'ha8;
                8'h70: sbox=8'h51; 8'h71: sbox=8'ha3;
                8'h72: sbox=8'h40; 8'h73: sbox=8'h8f;
                8'h74: sbox=8'h92; 8'h75: sbox=8'h9d;
                8'h76: sbox=8'h38; 8'h77: sbox=8'hf5;
                8'h78: sbox=8'hbc; 8'h79: sbox=8'hb6;
                8'h7a: sbox=8'hda; 8'h7b: sbox=8'h21;
                8'h7c: sbox=8'h10; 8'h7d: sbox=8'hff;
                8'h7e: sbox=8'hf3; 8'h7f: sbox=8'hd2;
                8'h80: sbox=8'hcd; 8'h81: sbox=8'h0c;
                8'h82: sbox=8'h13; 8'h83: sbox=8'hec;
                8'h84: sbox=8'h5f; 8'h85: sbox=8'h97;
                8'h86: sbox=8'h44; 8'h87: sbox=8'h17;
                8'h88: sbox=8'hc4; 8'h89: sbox=8'ha7;
                8'h8a: sbox=8'h7e; 8'h8b: sbox=8'h3d;
                8'h8c: sbox=8'h64; 8'h8d: sbox=8'h5d;
                8'h8e: sbox=8'h19; 8'h8f: sbox=8'h73;
                8'h90: sbox=8'h60; 8'h91: sbox=8'h81;
                8'h92: sbox=8'h4f; 8'h93: sbox=8'hdc;
                8'h94: sbox=8'h22; 8'h95: sbox=8'h2a;
                8'h96: sbox=8'h90; 8'h97: sbox=8'h88;
                8'h98: sbox=8'h46; 8'h99: sbox=8'hee;
                8'h9a: sbox=8'hb8; 8'h9b: sbox=8'h14;
                8'h9c: sbox=8'hde; 8'h9d: sbox=8'h5e;
                8'h9e: sbox=8'h0b; 8'h9f: sbox=8'hdb;
                8'ha0: sbox=8'he0; 8'ha1: sbox=8'h32;
                8'ha2: sbox=8'h3a; 8'ha3: sbox=8'h0a;
                8'ha4: sbox=8'h49; 8'ha5: sbox=8'h06;
                8'ha6: sbox=8'h24; 8'ha7: sbox=8'h5c;
                8'ha8: sbox=8'hc2; 8'ha9: sbox=8'hd3;
                8'haa: sbox=8'hac; 8'hab: sbox=8'h62;
                8'hac: sbox=8'h91; 8'had: sbox=8'h95;
                8'hae: sbox=8'he4; 8'haf: sbox=8'h79;
                8'hb0: sbox=8'he7; 8'hb1: sbox=8'hc8;
                8'hb2: sbox=8'h37; 8'hb3: sbox=8'h6d;
                8'hb4: sbox=8'h8d; 8'hb5: sbox=8'hd5;
                8'hb6: sbox=8'h4e; 8'hb7: sbox=8'ha9;
                8'hb8: sbox=8'h6c; 8'hb9: sbox=8'h56;
                8'hba: sbox=8'hf4; 8'hbb: sbox=8'hea;
                8'hbc: sbox=8'h65; 8'hbd: sbox=8'h7a;
                8'hbe: sbox=8'hae; 8'hbf: sbox=8'h08;
                8'hc0: sbox=8'hba; 8'hc1: sbox=8'h78;
                8'hc2: sbox=8'h25; 8'hc3: sbox=8'h2e;
                8'hc4: sbox=8'h1c; 8'hc5: sbox=8'ha6;
                8'hc6: sbox=8'hb4; 8'hc7: sbox=8'hc6;
                8'hc8: sbox=8'he8; 8'hc9: sbox=8'hdd;
                8'hca: sbox=8'h74; 8'hcb: sbox=8'h1f;
                8'hcc: sbox=8'h4b; 8'hcd: sbox=8'hbd;
                8'hce: sbox=8'h8b; 8'hcf: sbox=8'h8a;
                8'hd0: sbox=8'h70; 8'hd1: sbox=8'h3e;
                8'hd2: sbox=8'hb5; 8'hd3: sbox=8'h66;
                8'hd4: sbox=8'h48; 8'hd5: sbox=8'h03;
                8'hd6: sbox=8'hf6; 8'hd7: sbox=8'h0e;
                8'hd8: sbox=8'h61; 8'hd9: sbox=8'h35;
                8'hda: sbox=8'h57; 8'hdb: sbox=8'hb9;
                8'hdc: sbox=8'h86; 8'hdd: sbox=8'hc1;
                8'hde: sbox=8'h1d; 8'hdf: sbox=8'h9e;
                8'he0: sbox=8'he1; 8'he1: sbox=8'hf8;
                8'he2: sbox=8'h98; 8'he3: sbox=8'h11;
                8'he4: sbox=8'h69; 8'he5: sbox=8'hd9;
                8'he6: sbox=8'h8e; 8'he7: sbox=8'h94;
                8'he8: sbox=8'h9b; 8'he9: sbox=8'h1e;
                8'hea: sbox=8'h87; 8'heb: sbox=8'he9;
                8'hec: sbox=8'hce; 8'hed: sbox=8'h55;
                8'hee: sbox=8'h28; 8'hef: sbox=8'hdf;
                8'hf0: sbox=8'h8c; 8'hf1: sbox=8'ha1;
                8'hf2: sbox=8'h89; 8'hf3: sbox=8'h0d;
                8'hf4: sbox=8'hbf; 8'hf5: sbox=8'he6;
                8'hf6: sbox=8'h42; 8'hf7: sbox=8'h68;
                8'hf8: sbox=8'h41; 8'hf9: sbox=8'h99;
                8'hfa: sbox=8'h2d; 8'hfb: sbox=8'h0f;
                8'hfc: sbox=8'hb0; 8'hfd: sbox=8'h54;
                8'hfe: sbox=8'hbb; 8'hff: sbox=8'h16;
                default: sbox=8'h00;
            endcase
        end
    endfunction

    function [7:0] xtime;
        input [7:0] x;
        begin
            xtime = {x[6:0], 1'b0} ^ (8'h1b & {8{x[7]}});
        end
    endfunction

    function [7:0] rcon;
        input integer round_number;
        begin
            case (round_number)
                1: rcon=8'h01;
                2: rcon=8'h02;
                3: rcon=8'h04;
                4: rcon=8'h08;
                5: rcon=8'h10;
                6: rcon=8'h20;
                7: rcon=8'h40;
                8: rcon=8'h80;
                9: rcon=8'h1b;
                10: rcon=8'h36;
                default: rcon=8'h00;
            endcase
        end
    endfunction

    function [127:0] next_key;
        input [127:0] key;
        input integer round_number;
        reg [31:0] t, w0, w1, w2, w3;
        begin
            t = {sbox(key[23:16]), sbox(key[15:8]),
                 sbox(key[7:0]), sbox(key[31:24])};
            t = t ^ {rcon(round_number), 24'h000000};
            w0 = key[127:96] ^ t;
            w1 = key[95:64] ^ w0;
            w2 = key[63:32] ^ w1;
            w3 = key[31:0] ^ w2;
            next_key = {w0, w1, w2, w3};
        end
    endfunction

    // Bytes are numbered MSB first, in AES column-major order.
    function [127:0] sub_shift;
        input [127:0] state;
        integer row, col;
        reg [127:0] result;
        begin
            result = 128'b0;
            for (col = 0; col < 4; col = col + 1)
                for (row = 0; row < 4; row = row + 1)
                    result[127 - 8*(4*col + row) -: 8] =
                        sbox(state[127 - 8*(4*((col + row)%4) + row) -: 8]);
            sub_shift = result;
        end
    endfunction

    function [127:0] mix_columns;
        input [127:0] state;
        integer col;
        reg [7:0] a, b, c, d, total;
        reg [127:0] result;
        begin
            result = 128'b0;
            for (col = 0; col < 4; col = col + 1) begin
                a = state[127 - 32*col -: 8];
                b = state[119 - 32*col -: 8];
                c = state[111 - 32*col -: 8];
                d = state[103 - 32*col -: 8];
                total = a ^ b ^ c ^ d;
                result[127 - 32*col -: 8] = a ^ total ^ xtime(a ^ b);
                result[119 - 32*col -: 8] = b ^ total ^ xtime(b ^ c);
                result[111 - 32*col -: 8] = c ^ total ^ xtime(c ^ d);
                result[103 - 32*col -: 8] = d ^ total ^ xtime(d ^ a);
            end
            mix_columns = result;
        end
    endfunction

    function [127:0] aes_round;
        input [127:0] state;
        input [127:0] round_key;
        input integer round_number;
        reg [127:0] t;
        begin
            t = sub_shift(state);
            if (round_number != 10)
                t = mix_columns(t);
            aes_round = t ^ round_key;
        end
    endfunction

    reg [127:0] state_pipe [0:STAGES-1];
    reg [127:0] key_pipe [0:STAGES-1];
    reg valid_pipe [0:STAGES-1];

    genvar stage;
    generate
        for (stage = 0; stage < STAGES; stage = stage + 1) begin : g_stage
            localparam integer FIRST_ROUND = stage*ROUNDS_PER_STAGE + 1;
            localparam integer LAST_ROUND =
                ((stage + 1)*ROUNDS_PER_STAGE > 10) ? 10 :
                (stage + 1)*ROUNDS_PER_STAGE;
            wire [127:0] stage_state_in;
            wire [127:0] stage_key_in;
            wire stage_valid_in;
            reg [127:0] state_next;
            reg [127:0] key_next;
            integer round_index;

            if (stage == 0) begin : g_input
                assign stage_state_in = data_in ^ key_in;
                assign stage_key_in = key_in;
                assign stage_valid_in = start & valid_in & ~mode;
            end else begin : g_previous
                assign stage_state_in = state_pipe[stage-1];
                assign stage_key_in = key_pipe[stage-1];
                assign stage_valid_in = valid_pipe[stage-1];
            end

            always @* begin
                state_next = stage_state_in;
                key_next = stage_key_in;
                for (round_index = FIRST_ROUND; round_index <= LAST_ROUND;
                     round_index = round_index + 1) begin
                    key_next = next_key(key_next, round_index);
                    state_next = aes_round(state_next, key_next, round_index);
                end
            end

            always @(posedge clk) begin
                if (rst) begin
                    state_pipe[stage] <= 128'b0;
                    key_pipe[stage] <= 128'b0;
                    valid_pipe[stage] <= 1'b0;
                end else begin
                    valid_pipe[stage] <= stage_valid_in;
                    if (stage_valid_in) begin
                        state_pipe[stage] <= state_next;
                        key_pipe[stage] <= key_next;
                    end
                end
            end
        end
    endgenerate

    always @* begin
        data_out = state_pipe[STAGES-1];
        valid_out = valid_pipe[STAGES-1];
        done = valid_pipe[STAGES-1];
    end
endmodule

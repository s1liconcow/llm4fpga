`timescale 1ps/1ps
module glbl;
wire GSR = 1'b0;
endmodule
module sim_top(input clk, input we, input [12:0] wa,ra, input [3:0] data, output [31:0] q);
glbl glbl();
RAMB36E1 #(.RAM_MODE("TDP"),.READ_WIDTH_A(0),.READ_WIDTH_B(4),
 .WRITE_WIDTH_A(4),.WRITE_WIDTH_B(0),.DOA_REG(0),.DOB_REG(0),
 .WRITE_MODE_A("READ_FIRST"),.WRITE_MODE_B("READ_FIRST")) mem(
 .CLKARDCLK(clk),.CLKBWRCLK(clk),.ENARDEN(1'b1),.ENBWREN(1'b1),
 .ADDRARDADDR({1'b1,wa,2'b0}),.ADDRBWRADDR({1'b1,ra,2'b0}),
 .WEA({4{we}}),.WEBWE(8'b0),.DIADI({28'b0,data}),.DIBDI(32'b0),
 .DIPADIP(4'b0),.DIPBDIP(4'b0),.DOBDO(q),
 .RSTRAMARSTRAM(1'b0),.RSTRAMB(1'b0),.RSTREGARSTREG(1'b0),.RSTREGB(1'b0),
 .REGCEAREGCE(1'b1),.REGCEB(1'b1),.CASCADEINA(1'b0),.CASCADEINB(1'b0),
 .INJECTSBITERR(1'b0),.INJECTDBITERR(1'b0));
endmodule

#include "Vsim_top.h"
#include "verilated.h"
#include <iostream>
unsigned pattern(unsigned i){unsigned x=i*0x9e3779b9u+17;x^=x>>13;x*=0x85ebca6bu;return (x^(x>>16))&15;}
int main(){Vsim_top d; auto tick=[&](){d.clk=0;d.eval();Verilated::timeInc(2500);d.clk=1;d.eval();Verilated::timeInc(2500);};
d.we=0;d.wa=0;d.ra=8000;d.data=0;tick();
for(unsigned i=0;i<8192;i++){d.we=1;d.wa=i;d.data=pattern(i);tick();}
d.we=0;
for(unsigned i=0;i<8192;i++){d.ra=i;tick();if((d.q&15)!=pattern(i)){std::cerr<<i<<" "<<d.q;return 2;}}
std::cout<<"8192 RAM words match\n";return 0;}

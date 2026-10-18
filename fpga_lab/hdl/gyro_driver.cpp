// Line-oriented persistent simulator used by the Python resonator experiment.
#include "Vgyro_core.h"
#include "verilated.h"
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
static int signed18(unsigned v) { return (v & 0x20000) ? int(v)-262144 : int(v); }
int main(int argc, char **argv) {
    Verilated::commandArgs(argc,argv);
    Vgyro_core d;
    auto tick=[&](){ d.clk=0; d.eval(); d.clk=1; d.eval(); };
    auto ready=[&](){
        for(int n=0; !d.cfg_ready; ++n) {
            if(n>1000) throw std::runtime_error("Ready timeout");
            tick();
        }
    };
    try {
        d.rst=1; d.in_valid=0; d.cfg_valid=0; d.out_ready=1;
        d.adc_a=0; d.adc_b=0; d.cfg_addr=0; d.cfg_wdata=0;
        tick(); tick(); d.rst=0; ready();
        std::cout << "READY" << std::endl;
        std::string line;
        while(std::getline(std::cin,line)) {
            std::istringstream in(line); char command; int a,b;
            if(!(in>>command>>a>>b)) throw std::runtime_error("Malformed request");
            ready();
            if(command=='C') {
                if(a<0 || a>2047 || b < -8388608 || b > 8388607)
                    throw std::runtime_error("Configuration width overflow");
                d.cfg_addr=a; d.cfg_wdata=unsigned(b)&0xffffff; d.cfg_valid=1;
                tick(); d.cfg_valid=0;
                if(d.cfg_error) throw std::runtime_error("Configuration rejected");
                std::cout << "OK" << std::endl;
            } else if(command=='S') {
                if(a < -131072 || a > 131071 || b < -131072 || b > 131071)
                    throw std::runtime_error("ADC width overflow");
                d.adc_a=unsigned(a)&0x3ffff; d.adc_b=unsigned(b)&0x3ffff;
                d.in_valid=1; tick(); d.in_valid=0;
                for(int n=0; !d.out_valid; ++n) {
                    if(n>1000) throw std::runtime_error("Output timeout");
                    tick();
                }
                std::cout << signed18(d.actuator_x) << ' ' << signed18(d.actuator_y) << ' '
                          << signed18(d.rate_i) << ' ' << signed18(d.rate_q) << ' '
                          << signed18(d.agc_gain) << std::endl;
                tick();
            } else throw std::runtime_error("Unknown request");
        }
        d.final();
    } catch(const std::exception &e) { std::cerr << e.what() << std::endl; return 2; }
    return 0;
}

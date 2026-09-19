"""Deterministic VHDL backend for the existing numerical search templates."""
from __future__ import annotations

from .structured import Design


def normalizer_vhdl(design: Design) -> str:
    table = design.table
    s, f, n = design.shift, design.fraction_bits, 1 << design.segments_log2
    knots = ', '.join(str(int(v)) for v in table)
    if design.architecture == 'pwl':
        math = f'''idx := mag / {1 << s};
      if idx >= {n} then idx := {n-1}; end if;
      offset := mag - idx * {1 << s};
      value := knots(idx) + ((knots(idx+1)-knots(idx))*offset + {(1 << (s-1)) if s and design.rounding=='nearest' else 0}) / {1 << s};'''
    else:
        math = f'''idx := (mag + {(1 << (s-1)) if s else 0}) / {1 << s};
      value := knots(idx);'''
    d = f-14
    quantize = (f'(value + {(1 << (d-1)) if d and design.rounding=="nearest" else 0}) / {1 << d}'
                if d>=0 else f'value * {1 << (-d)}')
    return f'''-- Deterministic structured-search backend, not an LLM response or XLS output.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
entity dut is
  port (clk, rst, in_valid : in std_logic;
        input_data : in std_logic_vector(11 downto 0);
        out_valid : out std_logic;
        output_data : out std_logic_vector(15 downto 0));
end;
architecture rtl of dut is
  type table_t is array (0 to {n}) of integer range 0 to {int(table.max())};
  constant knots : table_t := ({knots});
begin
  process(clk)
    variable x : integer range -2048 to 2047;
    variable mag : integer range 0 to 2048;
    variable idx : integer range 0 to {n};
    variable offset : integer range 0 to {1 << s};
    variable value : integer range 0 to {1 << f};
    variable q : integer range 0 to 16384;
  begin
    if rising_edge(clk) then
      if rst = '1' then
        out_valid <= '0'; output_data <= (others => '0');
      else
        x := to_integer(signed(input_data));
        if x < 0 then mag := -x; else mag := x; end if;
        {math}
        q := {quantize};
        if x < 0 then output_data <= std_logic_vector(to_signed(-q,16));
        else output_data <= std_logic_vector(to_signed(q,16)); end if;
        out_valid <= in_valid;
      end if;
    end if;
  end process;
end;
'''

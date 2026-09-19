library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity dut is
  port (
    clk         : in  std_logic;
    rst         : in  std_logic;
    in_valid    : in  std_logic;
    input_data  : in  std_logic_vector(11 downto 0);
    out_valid   : out std_logic;
    output_data : out std_logic_vector(15 downto 0)
  );
end entity;

architecture rtl of dut is
  type rom_t is array (0 to 128) of unsigned(17 downto 0);

  -- Elaboration-time integer construction of rounded Q4 output knots.
  -- No square-root or division hardware is instantiated.
  function make_rom return rom_t is
    variable tab       : rom_t;
    variable a         : unsigned(11 downto 0);
    variable a2        : unsigned(23 downto 0);
    variable denom     : unsigned(22 downto 0);
    variable numerator : unsigned(59 downto 0);
    variable q, trial  : unsigned(17 downto 0);
    variable square_q  : unsigned(35 downto 0);
    variable product_q : unsigned(58 downto 0);
    variable midpoint  : unsigned(18 downto 0);
    variable square_m  : unsigned(37 downto 0);
    variable product_m : unsigned(60 downto 0);
    variable round_n   : unsigned(61 downto 0);
  begin
    for i in 0 to 128 loop
      a := to_unsigned(i * 16, 12);
      a2 := a * a;
      denom := resize(a2, 23) + to_unsigned(1048576, 23);
      numerator := shift_left(resize(a2, 60), 36);
      q := (others => '0');
      for b in 17 downto 0 loop
        trial := q;
        trial(b) := '1';
        square_q := trial * trial;
        product_q := square_q * denom;
        if resize(product_q, 60) <= numerator then
          q := trial;
        end if;
      end loop;
      midpoint := shift_left(resize(q, 19), 1) + to_unsigned(1, 19);
      square_m := midpoint * midpoint;
      product_m := square_m * denom;
      round_n := shift_left(resize(a2, 62), 38);
      if resize(product_m, 62) <= round_n then
        q := q + to_unsigned(1, 18);
      end if;
      tab(i) := q;
    end loop;
    return tab;
  end function;

  constant rom : rom_t := make_rom;
  signal v1, v2, v3 : std_logic;
  signal s1, s2, s3 : std_logic;
  signal magnitude : unsigned(11 downto 0);
  signal base2     : unsigned(17 downto 0);
  signal delta2    : unsigned(12 downto 0);
  signal fraction2 : unsigned(3 downto 0);
  signal value3    : unsigned(21 downto 0);
begin
  process(clk)
    variable extended : signed(12 downto 0);
    variable idx      : natural range 0 to 255;
    variable product  : unsigned(16 downto 0);
    variable rounded  : unsigned(21 downto 0);
    variable result_m : signed(15 downto 0);
  begin
    if rising_edge(clk) then
      if rst = '1' then
        v1 <= '0';
        v2 <= '0';
        v3 <= '0';
        s1 <= '0';
        s2 <= '0';
        s3 <= '0';
        magnitude <= (others => '0');
        base2 <= (others => '0');
        delta2 <= (others => '0');
        fraction2 <= (others => '0');
        value3 <= (others => '0');
        out_valid <= '0';
        output_data <= (others => '0');
      else
        v1 <= in_valid;
        v2 <= v1;
        v3 <= v2;
        out_valid <= v3;

        if in_valid = '1' then
          extended := resize(signed(input_data), 13);
          if extended(12) = '1' then
            extended := -extended;
          end if;
          magnitude <= resize(unsigned(extended), 12);
          s1 <= input_data(11);
        end if;

        if v1 = '1' then
          idx := to_integer(magnitude(11 downto 4));
          if idx >= 128 then
            base2 <= rom(128);
            delta2 <= (others => '0');
          else
            base2 <= rom(idx);
            delta2 <= resize(rom(idx + 1) - rom(idx), 13);
          end if;
          fraction2 <= magnitude(3 downto 0);
          s2 <= s1;
        end if;

        if v2 = '1' then
          product := delta2 * fraction2;
          value3 <= shift_left(resize(base2, 22), 4)
                    + resize(product, 22);
          s3 <= s2;
        end if;

        if v3 = '1' then
          rounded := value3 + to_unsigned(128, 22);
          result_m := signed(resize(shift_right(rounded, 8), 16));
          if s3 = '1' then
            output_data <= std_logic_vector(-result_m);
          else
            output_data <= std_logic_vector(result_m);
          end if;
        end if;
      end if;
    end if;
  end process;
end architecture;

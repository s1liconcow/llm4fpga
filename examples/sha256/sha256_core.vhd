library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity dut is
  port (
    clk         : in  std_logic;
    rst         : in  std_logic;
    in_valid    : in  std_logic;
    input_data  : in  std_logic_vector(767 downto 0);
    out_valid   : out std_logic;
    output_data : out std_logic_vector(255 downto 0)
  );
end entity dut;

architecture rtl of dut is
  subtype word_t is unsigned(31 downto 0);
  type state_t is array (0 to 7) of word_t;
  type schedule_t is array (0 to 15) of word_t;
  type constants_t is array (0 to 63) of word_t;
  constant K : constants_t := (
    x"428A2F98", x"71374491", x"B5C0FBCF", x"E9B5DBA5",
    x"3956C25B", x"59F111F1", x"923F82A4", x"AB1C5ED5",
    x"D807AA98", x"12835B01", x"243185BE", x"550C7DC3",
    x"72BE5D74", x"80DEB1FE", x"9BDC06A7", x"C19BF174",
    x"E49B69C1", x"EFBE4786", x"0FC19DC6", x"240CA1CC",
    x"2DE92C6F", x"4A7484AA", x"5CB0A9DC", x"76F988DA",
    x"983E5152", x"A831C66D", x"B00327C8", x"BF597FC7",
    x"C6E00BF3", x"D5A79147", x"06CA6351", x"14292967",
    x"27B70A85", x"2E1B2138", x"4D2C6DFC", x"53380D13",
    x"650A7354", x"766A0ABB", x"81C2C92E", x"92722C85",
    x"A2BFE8A1", x"A81A664B", x"C24B8B70", x"C76C51A3",
    x"D192E819", x"D6990624", x"F40E3585", x"106AA070",
    x"19A4C116", x"1E376C08", x"2748774C", x"34B0BCB5",
    x"391C0CB3", x"4ED8AA4A", x"5B9CCA4F", x"682E6FF3",
    x"748F82EE", x"78A5636F", x"84C87814", x"8CC70208",
    x"90BEFFFA", x"A4506CEB", x"BEF9A3F7", x"C67178F2"
  );
  type phase_t is (idle, rounds, finish);
  signal phase : phase_t;
  signal round_index : natural range 0 to 63;
  signal saved_state : state_t;
  signal working : state_t;
  signal schedule : schedule_t;

  function big_sigma0(x : word_t) return word_t is
  begin
    return rotate_right(x, 2) xor rotate_right(x, 13)
           xor rotate_right(x, 22);
  end function;

  function big_sigma1(x : word_t) return word_t is
  begin
    return rotate_right(x, 6) xor rotate_right(x, 11)
           xor rotate_right(x, 25);
  end function;

  function small_sigma0(x : word_t) return word_t is
  begin
    return rotate_right(x, 7) xor rotate_right(x, 18)
           xor shift_right(x, 3);
  end function;

  function small_sigma1(x : word_t) return word_t is
  begin
    return rotate_right(x, 17) xor rotate_right(x, 19)
           xor shift_right(x, 10);
  end function;
begin
  process(clk)
    variable choose_v, majority_v : word_t;
    variable sum_hs, sum_kw, t1, t2 : word_t;
    variable next_word : word_t;
  begin
    if rising_edge(clk) then
      if rst = '1' then
        phase <= idle;
        round_index <= 0;
        out_valid <= '0';
        output_data <= (others => '0');
      else
        out_valid <= '0';
        case phase is
          when idle =>
            if in_valid = '1' then
              for i in 0 to 7 loop
                saved_state(i) <= unsigned(input_data(767-32*i downto 736-32*i));
                working(i) <= unsigned(input_data(767-32*i downto 736-32*i));
              end loop;
              for i in 0 to 15 loop
                schedule(i) <= unsigned(input_data(511-32*i downto 480-32*i));
              end loop;
              round_index <= 0;
              phase <= rounds;
            end if;

          when rounds =>
            -- working(0..7) holds a,b,c,d,e,f,g,h.
            choose_v := (working(4) and working(5)) xor
                        ((not working(4)) and working(6));
            majority_v := (working(0) and working(1)) xor
                          (working(0) and working(2)) xor
                          (working(1) and working(2));
            sum_hs := working(7) + big_sigma1(working(4));
            sum_kw := K(round_index) + schedule(0);
            t1 := (sum_hs + sum_kw) + choose_v;
            t2 := big_sigma0(working(0)) + majority_v;

            working(0) <= t1 + t2;
            working(1) <= working(0);
            working(2) <= working(1);
            working(3) <= working(2);
            working(4) <= working(3) + t1;
            working(5) <= working(4);
            working(6) <= working(5);
            working(7) <= working(6);

            -- Before round t, schedule(0) is W[t].
            -- Generate W[t+16] using fixed taps of the rolling window.
            next_word := (schedule(0) + small_sigma0(schedule(1))) +
                         (schedule(9) + small_sigma1(schedule(14)));
            for i in 0 to 14 loop
              schedule(i) <= schedule(i+1);
            end loop;
            schedule(15) <= next_word;

            if round_index = 63 then
              phase <= finish;
            else
              round_index <= round_index + 1;
            end if;

          when finish =>
            for i in 0 to 7 loop
              output_data(255-32*i downto 224-32*i) <=
                std_logic_vector(saved_state(i) + working(i));
            end loop;
            out_valid <= '1';
            phase <= idle;
        end case;
      end if;
    end if;
  end process;
end architecture rtl;

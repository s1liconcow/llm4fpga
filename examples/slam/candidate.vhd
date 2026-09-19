library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity dut is
  port (
    clk : in std_logic;
    rst : in std_logic;
    in_valid : in std_logic;
    input_data : in std_logic_vector(1155 downto 0);
    out_valid : out std_logic;
    output_data : out std_logic_vector(511 downto 0)
  );
end entity;

architecture rtl of dut is
  subtype s36 is signed(35 downto 0);
  subtype s64 is signed(63 downto 0);
  subtype s72 is signed(71 downto 0);
  subtype s73 is signed(72 downto 0);
  subtype u36 is unsigned(35 downto 0);
  subtype u64 is unsigned(63 downto 0);
  subtype u72 is unsigned(71 downto 0);
  type state_t is (idle, running, finish);
  signal state : state_t := idle;
  signal phase : natural range 0 to 6 := 0;
  signal pair_index : natural range 0 to 7 := 0;
  signal active_count : unsigned(3 downto 0);
  signal points : std_logic_vector(1023 downto 0);
  signal tx, ty, cosine, sine : signed(31 downto 0);
  signal a0, b0, a1, b1 : s36;
  signal magnitude0, magnitude1 : u72;
  signal negative0, negative1 : std_logic;
  signal product0, product1 : s72;
  signal ux, jx, jy, rx, ry : s36;
  signal h02, h12, h22, g0, g1, g2, cost : s64;

  function magnitude(x : s36) return u36 is
    variable t : u36;
  begin
    t := unsigned(x);
    if x(35) = '1' then
      t := (not t) + to_unsigned(1, 36);
    end if;
    return t;
  end function;

  function restore_sign(x : u72; negative : std_logic) return s72 is
    variable t : u72;
  begin
    t := x;
    if negative = '1' then
      t := (not x) + to_unsigned(1, 72);
    end if;
    return signed(t);
  end function;

  function rotation_round(x : s73) return s36 is
    variable t : s73;
  begin
    t := x + shift_left(to_signed(1, 73), 25);
    return signed(t(61 downto 26));
  end function;

  -- Convert positive Q56 magnitude to Q32 before restoring its sign.
  -- All sign restoration and accumulation then use exactly 64 bits.
  function quadratic_term(x : u72; negative : std_logic) return u64 is
    variable rounded : u72;
    variable result_bits : u64;
  begin
    rounded := x + shift_left(to_unsigned(1, 72), 23);
    result_bits := resize(rounded(71 downto 24), 64);
    if negative = '1' then
      result_bits := (not result_bits) + to_unsigned(1, 64);
    end if;
    return result_bits;
  end function;

  function quadratic_sum(x, y : u72; nx, ny : std_logic) return s64 is
    variable sum_bits : u64;
  begin
    sum_bits := quadratic_term(x, nx) + quadratic_term(y, ny);
    return signed(sum_bits);
  end function;

  function coordinate_q28(x : signed(31 downto 0)) return s36 is
  begin
    return shift_left(resize(x, 36), 4);
  end function;
begin
  product0 <= restore_sign(magnitude0, negative0);
  product1 <= restore_sign(magnitude1, negative1);

  process(all)
  begin
    a0 <= (others => '0');
    b0 <= (others => '0');
    a1 <= (others => '0');
    b1 <= (others => '0');
    case phase is
      when 0 =>
        a0 <= resize(cosine, 36);
        b0 <= resize(signed(points(31 downto 0)), 36);
        a1 <= resize(sine, 36);
        b1 <= resize(signed(points(63 downto 32)), 36);
      when 1 =>
        a0 <= resize(sine, 36);
        b0 <= resize(signed(points(31 downto 0)), 36);
        a1 <= resize(cosine, 36);
        b1 <= resize(signed(points(63 downto 32)), 36);
      when 3 =>
        a0 <= jx; b0 <= jx;
        a1 <= jy; b1 <= jy;
      when 4 =>
        a0 <= jx; b0 <= rx;
        a1 <= jy; b1 <= ry;
      when 5 =>
        a0 <= rx; b0 <= rx;
        a1 <= ry; b1 <= ry;
      when others => null;
    end case;
  end process;

  process(clk)
    variable uy_value : s36;
    variable rotation_value : s73;
  begin
    if rising_edge(clk) then
      if rst = '1' then
        state <= idle;
        phase <= 0;
        pair_index <= 0;
        out_valid <= '0';
        output_data <= (others => '0');
        h02 <= (others => '0');
        h12 <= (others => '0');
        h22 <= (others => '0');
        g0 <= (others => '0');
        g1 <= (others => '0');
        g2 <= (others => '0');
        cost <= (others => '0');
      else
        out_valid <= '0';
        case state is
          when idle =>
            if in_valid = '1' then
              points <= input_data(1023 downto 0);
              tx <= signed(input_data(1055 downto 1024));
              ty <= signed(input_data(1087 downto 1056));
              cosine <= signed(input_data(1119 downto 1088));
              sine <= signed(input_data(1151 downto 1120));
              active_count <= unsigned(input_data(1155 downto 1152));
              h02 <= (others => '0');
              h12 <= (others => '0');
              h22 <= (others => '0');
              g0 <= (others => '0');
              g1 <= (others => '0');
              g2 <= (others => '0');
              cost <= (others => '0');
              phase <= 0;
              pair_index <= 0;
              state <= running;
            end if;
          when running =>
            magnitude0 <= magnitude(a0) * magnitude(b0);
            magnitude1 <= magnitude(a1) * magnitude(b1);
            negative0 <= a0(35) xor b0(35);
            negative1 <= a1(35) xor b1(35);
            case phase is
              when 0 =>
                phase <= 1;
              when 1 =>
                rotation_value := signed(product0(71) & std_logic_vector(product0))
                                - signed(product1(71) & std_logic_vector(product1));
                ux <= rotation_round(rotation_value);
                phase <= 2;
              when 2 =>
                rotation_value := signed(product0(71) & std_logic_vector(product0))
                                + signed(product1(71) & std_logic_vector(product1));
                uy_value := rotation_round(rotation_value);
                jx <= -uy_value;
                jy <= ux;
                rx <= ux + coordinate_q28(tx)
                      - coordinate_q28(signed(points(95 downto 64)));
                ry <= uy_value + coordinate_q28(ty)
                      - coordinate_q28(signed(points(127 downto 96)));
                phase <= 3;
              when 3 =>
                phase <= 4;
              when 4 =>
                if to_unsigned(pair_index, 4) < active_count then
                  h22 <= signed(unsigned(h22) + unsigned(quadratic_sum(
                    magnitude0, magnitude1, negative0, negative1)));
                end if;
                phase <= 5;
              when 5 =>
                if to_unsigned(pair_index, 4) < active_count then
                  g2 <= signed(unsigned(g2) + unsigned(quadratic_sum(
                    magnitude0, magnitude1, negative0, negative1)));
                end if;
                phase <= 6;
              when 6 =>
                if to_unsigned(pair_index, 4) < active_count then
                  h02 <= h02 + shift_left(resize(jx, 64), 4);
                  h12 <= h12 + shift_left(resize(jy, 64), 4);
                  g0 <= g0 + shift_left(resize(rx, 64), 4);
                  g1 <= g1 + shift_left(resize(ry, 64), 4);
                  cost <= signed(unsigned(cost) + unsigned(quadratic_sum(
                    magnitude0, magnitude1, negative0, negative1)));
                end if;
                if pair_index = 7 then
                  state <= finish;
                else
                  points <= std_logic_vector(shift_right(unsigned(points), 128));
                  pair_index <= pair_index + 1;
                  phase <= 0;
                end if;
            end case;
          when finish =>
            output_data(63 downto 0) <= std_logic_vector(h02);
            output_data(127 downto 64) <= std_logic_vector(h12);
            output_data(191 downto 128) <= std_logic_vector(h22);
            output_data(255 downto 192) <= std_logic_vector(g0);
            output_data(319 downto 256) <= std_logic_vector(g1);
            output_data(383 downto 320) <= std_logic_vector(g2);
            output_data(447 downto 384) <= std_logic_vector(cost);
            output_data(511 downto 448) <=
              std_logic_vector(shift_left(resize(active_count, 64), 32));
            out_valid <= '1';
            state <= idle;
        end case;
      end if;
    end if;
  end process;
end architecture;

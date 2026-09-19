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
  subtype s32 is signed(31 downto 0);
  subtype s64 is signed(63 downto 0);
  type state_t is (idle, work, finish);
  signal state : state_t;
  signal phase : natural range 0 to 6;
  signal pair_index : natural range 0 to 7;
  signal active_count : unsigned(3 downto 0);
  signal points : std_logic_vector(1023 downto 0);
  signal tx, ty, cosine, sine : s32;
  signal ux, jx, jy, rx, ry : s32;
  signal a0, b0, a1, b1 : s32;
  signal product0, product1 : s64;
  signal h02, h12, h22, g0, g1, g2, cost : s64;

  function add64(a, b : s64) return s64 is
    variable aa, bb, total : signed(64 downto 0);
  begin
    aa(63 downto 0) := a;
    aa(64) := a(63);
    bb(63 downto 0) := b;
    bb(64) := b(63);
    total := aa + bb;
    return total(63 downto 0);
  end function;

  function linear_q32(a : s32) return s64 is
    variable result : s64;
  begin
    result(63 downto 44) := (others => a(31));
    result(43 downto 12) := a;
    result(11 downto 0) := (others => '0');
    return result;
  end function;

  function quadratic_q32(a, b : s64) return s64 is
    variable aa, bb, total : signed(64 downto 0);
    variable result : s64;
  begin
    -- Explicit sign extension on both operands and the scaled result.
    -- No signed right-shift/resize combination is used here.
    aa(63 downto 0) := a;
    aa(64) := a(63);
    bb(63 downto 0) := b;
    bb(64) := b(63);
    total := aa + bb;
    result(56 downto 0) := total(64 downto 8);
    result(63 downto 57) := (others => total(64));
    return result;
  end function;

  function rounded_rotation(a : signed(64 downto 0)) return s32 is
    variable rounded : signed(65 downto 0);
  begin
    rounded := resize(a, 66) + shift_left(to_signed(1, 66), 25);
    return rounded(57 downto 26);
  end function;
begin
  -- Two shared multipliers: Q30*Q16 for rotation, Q20*Q20 otherwise.
  process(all)
  begin
    a0 <= (others => '0');
    b0 <= (others => '0');
    a1 <= (others => '0');
    b1 <= (others => '0');
    case phase is
      when 0 =>
        a0 <= cosine;
        b0 <= signed(points(31 downto 0));
        a1 <= sine;
        b1 <= signed(points(63 downto 32));
      when 1 =>
        a0 <= sine;
        b0 <= signed(points(31 downto 0));
        a1 <= cosine;
        b1 <= signed(points(63 downto 32));
      when 3 =>
        a0 <= jx;
        b0 <= jx;
        a1 <= jy;
        b1 <= jy;
      when 4 =>
        a0 <= jx;
        b0 <= rx;
        a1 <= jy;
        b1 <= ry;
      when 5 =>
        a0 <= rx;
        b0 <= rx;
        a1 <= ry;
        b1 <= ry;
      when others => null;
    end case;
  end process;

  process(clk)
    variable rotation_sum : signed(64 downto 0);
    variable uy_value : s32;
    variable residual_x, residual_y : signed(33 downto 0);
  begin
    if rising_edge(clk) then
      if rst = '1' then
        state <= idle;
        phase <= 0;
        pair_index <= 0;
        active_count <= (others => '0');
        points <= (others => '0');
        tx <= (others => '0');
        ty <= (others => '0');
        cosine <= (others => '0');
        sine <= (others => '0');
        ux <= (others => '0');
        jx <= (others => '0');
        jy <= (others => '0');
        rx <= (others => '0');
        ry <= (others => '0');
        product0 <= (others => '0');
        product1 <= (others => '0');
        h02 <= (others => '0');
        h12 <= (others => '0');
        h22 <= (others => '0');
        g0 <= (others => '0');
        g1 <= (others => '0');
        g2 <= (others => '0');
        cost <= (others => '0');
        output_data <= (others => '0');
        out_valid <= '0';
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
              state <= work;
            end if;
          when work =>
            product0 <= a0 * b0;
            product1 <= a1 * b1;
            case phase is
              when 0 =>
                phase <= 1;
              when 1 =>
                rotation_sum := resize(product0, 65) - resize(product1, 65);
                ux <= rounded_rotation(rotation_sum);
                phase <= 2;
              when 2 =>
                rotation_sum := resize(product0, 65) + resize(product1, 65);
                uy_value := rounded_rotation(rotation_sum);
                jx <= resize(-resize(uy_value, 33), 32);
                jy <= ux;
                residual_x := resize(ux, 34)
                  + shift_left(resize(tx, 34), 4)
                  - shift_left(resize(signed(points(95 downto 64)), 34), 4);
                residual_y := resize(uy_value, 34)
                  + shift_left(resize(ty, 34), 4)
                  - shift_left(resize(signed(points(127 downto 96)), 34), 4);
                rx <= resize(residual_x, 32);
                ry <= resize(residual_y, 32);
                phase <= 3;
              when 3 =>
                phase <= 4;
              when 4 =>
                if to_unsigned(pair_index, 4) < active_count then
                  h22 <= add64(h22, quadratic_q32(product0, product1));
                end if;
                phase <= 5;
              when 5 =>
                if to_unsigned(pair_index, 4) < active_count then
                  g2 <= add64(g2, quadratic_q32(product0, product1));
                end if;
                phase <= 6;
              when 6 =>
                if to_unsigned(pair_index, 4) < active_count then
                  h02 <= add64(h02, linear_q32(jx));
                  h12 <= add64(h12, linear_q32(jy));
                  g0 <= add64(g0, linear_q32(rx));
                  g1 <= add64(g1, linear_q32(ry));
                  cost <= add64(cost, quadratic_q32(product0, product1));
                end if;
                points <= std_logic_vector(shift_right(unsigned(points), 128));
                phase <= 0;
                if pair_index = 7 then
                  state <= finish;
                else
                  pair_index <= pair_index + 1;
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
            output_data(511 downto 448) <= std_logic_vector(shift_left(resize(active_count, 64), 32));
            out_valid <= '1';
            state <= idle;
        end case;
      end if;
    end if;
  end process;
end architecture;

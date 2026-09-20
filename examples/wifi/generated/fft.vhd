library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity dut is
  port (
    clk : in std_logic;
    rst : in std_logic;
    in_valid : in std_logic;
    input_data : in std_logic_vector(2047 downto 0);
    out_valid : out std_logic;
    output_data : out std_logic_vector(3071 downto 0)
  );
end entity;

architecture rtl of dut is
  subtype sample_t is signed(25 downto 0);
  subtype operand_t is signed(24 downto 0);
  subtype coeff_t is signed(23 downto 0);
  subtype product_t is signed(48 downto 0);
  type memory_t is array (0 to 63) of sample_t;
  type cosine_t is array (0 to 16) of integer range 0 to 4194304;
  constant COSINE : cosine_t := (
    4194304, 4174107, 4113712, 4013699,
    3875032, 3699046, 3487436, 3242241,
    2965821, 2660838, 2330230, 1977181,
    1605091, 1217542, 818268, 411114, 0
  );

  type partial_t is record
    ll : signed(25 downto 0);
    lh : signed(24 downto 0);
    hl : signed(25 downto 0);
    hh : signed(24 downto 0);
  end record;

  function split_multiply(a : operand_t; b : coeff_t)
    return partial_t is
    variable al, ah, bl : signed(12 downto 0);
    variable bh : signed(11 downto 0);
    variable p : partial_t;
  begin
    al := signed('0' & std_logic_vector(a(11 downto 0)));
    ah := a(24 downto 12);
    bl := signed('0' & std_logic_vector(b(11 downto 0)));
    bh := b(23 downto 12);
    p.ll := al * bl;
    p.lh := al * bh;
    p.hl := ah * bl;
    p.hh := ah * bh;
    return p;
  end function;

  function join_product(p : partial_t) return product_t is
    variable cross_term : signed(26 downto 0);
    variable low_term, high_term : product_t;
  begin
    cross_term := resize(p.lh, 27) + resize(p.hl, 27);
    low_term := resize(p.ll, 49) +
                shift_left(resize(cross_term, 49), 12);
    high_term := shift_left(resize(p.hh, 49), 24);
    return low_term + high_term;
  end function;

  function reverse6(x : natural) return natural is
    variable u, v : unsigned(5 downto 0);
  begin
    u := to_unsigned(x, 6);
    for k in 0 to 5 loop
      v(k) := u(5-k);
    end loop;
    return to_integer(v);
  end function;

  function address_a(s, n : natural) return natural is
  begin
    case s is
      when 0 => return 2*n;
      when 1 => return (n/2)*4 + (n mod 2);
      when 2 => return (n/4)*8 + (n mod 4);
      when 3 => return (n/8)*16 + (n mod 8);
      when 4 => return (n/16)*32 + (n mod 16);
      when others => return n;
    end case;
  end function;

  function separation(s : natural) return natural is
  begin
    case s is
      when 0 => return 1;
      when 1 => return 2;
      when 2 => return 4;
      when 3 => return 8;
      when 4 => return 16;
      when others => return 32;
    end case;
  end function;

  function twiddle_index(s, n : natural) return natural is
  begin
    case s is
      when 0 => return 0;
      when 1 => return (n mod 2)*16;
      when 2 => return (n mod 4)*8;
      when 3 => return (n mod 8)*4;
      when 4 => return (n mod 16)*2;
      when others => return n;
    end case;
  end function;

  function cosine_coefficient(t : natural) return coeff_t is
  begin
    if t <= 16 then
      return to_signed(COSINE(t), 24);
    else
      return to_signed(-COSINE(32-t), 24);
    end if;
  end function;

  function sine_coefficient(t : natural) return coeff_t is
  begin
    if t <= 16 then
      return to_signed(-COSINE(16-t), 24);
    else
      return to_signed(-COSINE(t-16), 24);
    end if;
  end function;

  function round_product(x : signed(49 downto 0)) return sample_t is
    variable r : signed(49 downto 0);
  begin
    r := x + to_signed(2097152, 50);
    return resize(shift_right(r, 22), 26);
  end function;

  function round_output(x : sample_t) return signed is
    variable r : signed(26 downto 0);
  begin
    r := resize(x, 27) + to_signed(4, 27);
    return resize(shift_right(r, 3), 24);
  end function;

  type state_t is (idle, prime, phase_r, phase_i, drain, finish);
  signal state : state_t;
  signal mr, mi : memory_t;
  signal stage : natural range 0 to 5;
  signal butterfly : natural range 0 to 31;
  signal read_a, read_b : natural range 0 to 63;
  signal write_a, write_b : natural range 0 to 63;
  signal ar, ai, br, bi : sample_t;
  signal delayed_ar, delayed_ai, delayed_tr : sample_t;
  signal wr, wi, multiply_cx, multiply_cy : coeff_t;
  signal px, py : partial_t;
  signal joined_x, joined_y : product_t;
  signal result_real, result_imag : sample_t;
  signal pending : std_logic;
begin
  -- Exactly two shared split multipliers, used on both phases.
  multiply_cx <= wi when state = phase_i else wr;
  multiply_cy <= wr when state = phase_i else wi;
  joined_x <= join_product(px);
  joined_y <= join_product(py);
  result_real <= round_product(resize(joined_x, 50) - resize(joined_y, 50));
  result_imag <= round_product(resize(joined_x, 50) + resize(joined_y, 50));

  process(clk)
    variable n : natural range 0 to 31;
    variable a, b : natural range 0 to 63;
    variable t : natural range 0 to 31;
  begin
    if rising_edge(clk) then
      if rst = '1' then
        state <= idle;
        out_valid <= '0';
        stage <= 0;
        butterfly <= 0;
        pending <= '0';
      else
        out_valid <= '0';

        if state = phase_r or state = phase_i then
          px <= split_multiply(resize(br, 25), multiply_cx);
          py <= split_multiply(resize(bi, 25), multiply_cy);
        end if;

        -- Imaginary products belong to the previous butterfly here.
        -- Commit overlaps the next butterfly's real multiplication.
        if (state = phase_r and pending = '1') or state = drain then
          mr(write_a) <= delayed_ar + delayed_tr;
          mi(write_a) <= delayed_ai + result_imag;
          mr(write_b) <= delayed_ar - delayed_tr;
          mi(write_b) <= delayed_ai - result_imag;
          pending <= '0';
        end if;

        case state is
          when idle =>
            if in_valid = '1' then
              for k in 0 to 63 loop
                mr(reverse6(k)) <= shift_left(
                  resize(signed(input_data(32*k+15 downto 32*k)), 26), 3);
                mi(reverse6(k)) <= shift_left(
                  resize(signed(input_data(32*k+31 downto 32*k+16)), 26), 3);
              end loop;
              stage <= 0;
              butterfly <= 0;
              pending <= '0';
              state <= prime;
            end if;

          when prime =>
            butterfly <= 0;
            pending <= '0';
            state <= phase_r;

          when phase_r =>
            state <= phase_i;

          when phase_i =>
            delayed_tr <= result_real;
            delayed_ar <= ar;
            delayed_ai <= ai;
            write_a <= read_a;
            write_b <= read_b;
            pending <= '1';
            if butterfly = 31 then
              state <= drain;
            else
              butterfly <= butterfly + 1;
              state <= phase_r;
            end if;

          when drain =>
            if stage = 5 then
              state <= finish;
            else
              stage <= stage + 1;
              state <= prime;
            end if;

          when finish =>
            for k in 0 to 63 loop
              output_data(48*k+23 downto 48*k) <=
                std_logic_vector(round_output(mr(k)));
              output_data(48*k+47 downto 48*k+24) <=
                std_logic_vector(round_output(mi(k)));
            end loop;
            out_valid <= '1';
            state <= idle;
        end case;

        -- Butterflies within a stage have disjoint addresses.
        if state = prime or
           (state = phase_i and butterfly /= 31) then
          if state = prime then
            n := 0;
          else
            n := butterfly + 1;
          end if;
          a := address_a(stage, n);
          b := a + separation(stage);
          t := twiddle_index(stage, n);
          read_a <= a;
          read_b <= b;
          ar <= mr(a);
          ai <= mi(a);
          br <= mr(b);
          bi <= mi(b);
          wr <= cosine_coefficient(t);
          wi <= sine_coefficient(t);
        end if;
      end if;
    end if;
  end process;
end architecture;

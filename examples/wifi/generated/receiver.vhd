library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity wifi_viterbi is
  port (
    clk       : in  std_logic;
    rst       : in  std_logic;
    in_valid  : in  std_logic;
    in_start  : in  std_logic;
    in_last   : in  std_logic;
    pair      : in  std_logic_vector(3 downto 0);
    out_valid : out std_logic;
    out_bit   : out std_logic;
    out_last  : out std_logic
  );
end entity;

architecture rtl of wifi_viterbi is
  -- Maximum finite metric is 2*32784 = 65568.
  subtype metric_t is unsigned(16 downto 0);
  constant INF : metric_t := (others => '1');
  type metric_array_t is array (0 to 63) of metric_t;
  type history_array_t is array (0 to 63) of
    std_logic_vector(63 downto 0);
  type winner_t is record
    metric : metric_t;
    index  : natural range 0 to 63;
  end record;
  type group_array_t is array (0 to 7) of winner_t;

  signal metrics : metric_array_t;
  signal candidate0, candidate1 : metric_array_t;
  signal histories : history_array_t;
  signal groups : group_array_t;
  signal best_index : natural range 0 to 63;

  signal v1, v2, v3, v4 : std_logic;
  signal start1 : std_logic;
  signal last1, last2, last3, last4 : std_logic;
  signal depth : natural range 0 to 64;
  signal depth1, depth2, depth3, depth4 : natural range 0 to 64;
  signal flush_history : std_logic_vector(63 downto 0);
  signal flush_left : natural range 0 to 64;

  -- Called with constant arguments; parity logic becomes constants.
  function parity_mask(r : natural; mask : natural) return natural is
    variable rr : natural := r;
    variable mm : natural := mask;
    variable result_bit : natural range 0 to 1 := 0;
  begin
    for k in 0 to 6 loop
      if (rr mod 2 = 1) and (mm mod 2 = 1) then
        result_bit := 1 - result_bit;
      end if;
      rr := rr / 2;
      mm := mm / 2;
    end loop;
    return result_bit;
  end function;

  function mismatch(symbol : std_logic_vector(1 downto 0);
                    expected_bit : natural) return natural is
  begin
    if symbol = "10" then
      return 0;
    elsif expected_bit = 0 then
      if symbol = "00" then
        return 0;
      else
        return 1;
      end if;
    else
      if symbol = "01" then
        return 0;
      else
        return 1;
      end if;
    end if;
  end function;

  function branch_cost(observation : std_logic_vector(3 downto 0);
                       reg_value : natural) return natural is
  begin
    -- Decimal 91 = octal 133; decimal 121 = octal 171.
    return mismatch(observation(1 downto 0),
                    parity_mask(reg_value, 91)) +
           mismatch(observation(3 downto 2),
                    parity_mask(reg_value, 121));
  end function;

  function add_cost(base : metric_t; cost : natural) return metric_t is
  begin
    if base = INF then
      return INF;
    else
      return base + to_unsigned(cost, base'length);
    end if;
  end function;

  function better(a, b : winner_t) return winner_t is
  begin
    if a.metric < b.metric then
      return a;
    elsif b.metric < a.metric then
      return b;
    elsif a.index <= b.index then
      return a;
    else
      return b;
    end if;
  end function;

begin
  process(clk)
    variable p0, p1 : natural range 0 to 63;
    variable predecessor : natural range 0 to 63;
    variable input_bit : std_logic;
    variable reg0, reg1 : natural range 0 to 127;
    variable base0, base1 : metric_t;
    variable next_depth : natural range 0 to 64;
    variable w0, w1, w2, w3 : winner_t;
    variable w4, w5, w6, w7 : winner_t;
    variable selected : winner_t;
  begin
    if rising_edge(clk) then
      if rst = '1' then
        out_valid <= '0';
        out_bit <= '0';
        out_last <= '0';
        v1 <= '0';
        v2 <= '0';
        v3 <= '0';
        v4 <= '0';
        start1 <= '0';
        last1 <= '0';
        last2 <= '0';
        last3 <= '0';
        last4 <= '0';
        depth <= 0;
        depth1 <= 0;
        depth2 <= 0;
        depth3 <= 0;
        depth4 <= 0;
        flush_left <= 0;
        flush_history <= (others => '0');
        for s in 0 to 63 loop
          if s = 0 then
            metrics(s) <= (others => '0');
          else
            metrics(s) <= INF;
          end if;
          histories(s) <= (others => '0');
        end loop;
      else
        out_valid <= '0';
        out_last <= '0';
        v1 <= in_valid;
        v2 <= v1;
        v3 <= v2;
        v4 <= v3;

        -- Stage 1: parallel branch additions. An accepted start supplies
        -- the initial metrics directly, before processing this observation.
        if in_valid = '1' then
          start1 <= in_start;
          last1 <= in_last;
          if in_start = '1' then
            next_depth := 1;
          elsif depth < 64 then
            next_depth := depth + 1;
          else
            next_depth := 64;
          end if;
          depth <= next_depth;
          depth1 <= next_depth;
          for s in 0 to 63 loop
            p0 := (s mod 32) * 2;
            p1 := p0 + 1;
            reg0 := (s / 32) * 64 + p0;
            reg1 := reg0 + 1;
            if in_start = '1' then
              base0 := INF;
              base1 := INF;
              if p0 = 0 then
                base0 := (others => '0');
              end if;
            else
              base0 := metrics(p0);
              base1 := metrics(p1);
            end if;
            candidate0(s) <= add_cost(base0, branch_cost(pair, reg0));
            candidate1(s) <= add_cost(base1, branch_cost(pair, reg1));
          end loop;
        end if;

        -- Stage 2: compare/select and register exchange. Strict comparison
        -- makes an equal-cost predecessor choice select p0.
        if v1 = '1' then
          last2 <= last1;
          depth2 <= depth1;
          for s in 0 to 63 loop
            p0 := (s mod 32) * 2;
            if candidate1(s) < candidate0(s) then
              metrics(s) <= candidate1(s);
              predecessor := p0 + 1;
            else
              metrics(s) <= candidate0(s);
              predecessor := p0;
            end if;
            if s >= 32 then
              input_bit := '1';
            else
              input_bit := '0';
            end if;
            if start1 = '1' then
              histories(s) <= (63 downto 1 => '0') & input_bit;
            else
              histories(s) <= histories(predecessor)(62 downto 0) & input_bit;
            end if;
          end loop;
        end if;

        -- Stage 3: first three levels of the balanced minimum tree.
        if v2 = '1' then
          last3 <= last2;
          depth3 <= depth2;
          for g in 0 to 7 loop
            w0 := (metric => metrics(8*g),   index => 8*g);
            w1 := (metric => metrics(8*g+1), index => 8*g+1);
            w2 := (metric => metrics(8*g+2), index => 8*g+2);
            w3 := (metric => metrics(8*g+3), index => 8*g+3);
            w4 := (metric => metrics(8*g+4), index => 8*g+4);
            w5 := (metric => metrics(8*g+5), index => 8*g+5);
            w6 := (metric => metrics(8*g+6), index => 8*g+6);
            w7 := (metric => metrics(8*g+7), index => 8*g+7);
            groups(g) <= better(
              better(better(w0, w1), better(w2, w3)),
              better(better(w4, w5), better(w6, w7)));
          end loop;
        end if;

        -- Stage 4: final three minimum-tree levels. Equal final metrics
        -- choose the lowest state number, matching argmin ordering.
        if v3 = '1' then
          last4 <= last3;
          depth4 <= depth3;
          selected := better(
            better(better(groups(0), groups(1)),
                   better(groups(2), groups(3))),
            better(better(groups(4), groups(5)),
                   better(groups(6), groups(7))));
          best_index <= selected.index;
        end if;

        -- Stage 5: select the matching survivor. With input spacing >=3,
        -- a subsequent register exchange can occur on this same edge;
        -- signal reads here still see the preceding observation's history.
        if flush_left /= 0 then
          out_valid <= '1';
          out_bit <= flush_history(flush_left - 1);
          if flush_left = 1 then
            out_last <= '1';
          end if;
          flush_left <= flush_left - 1;
        elsif v4 = '1' then
          if last4 = '1' then
            -- The final observation never emits a separate streaming bit.
            -- Its entire uncommitted suffix is copied and drained once.
            flush_history <= histories(best_index);
            flush_left <= depth4;
          elsif depth4 = 64 then
            out_valid <= '1';
            out_bit <= histories(best_index)(63);
          end if;
        end if;
      end if;
    end if;
  end process;
end architecture;


library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity wifi_fft64 is
  port (
    clk : in std_logic;
    rst : in std_logic;
    in_valid : in std_logic;
    input_data : in std_logic_vector(2047 downto 0);
    out_valid : out std_logic;
    output_data : out std_logic_vector(3071 downto 0)
  );
end entity;

architecture rtl of wifi_fft64 is
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


library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity wifi_backend is
  port (
    clk, rst, in_valid, in_header, in_last, out_ready : in std_logic;
    input_data : in std_logic_vector(1535 downto 0);
    in_ready : out std_logic;
    header_valid, header_error : out std_logic;
    header_rate : out std_logic_vector(7 downto 0);
    header_length : out std_logic_vector(11 downto 0);
    out_valid, out_first, out_last, out_fcs_ok, overflow : out std_logic;
    output_data, out_rate : out std_logic_vector(7 downto 0);
    out_length : out std_logic_vector(11 downto 0)
  );
end entity;

architecture rtl of wifi_backend is
  type state_type is (IDLE, DEMAP, FEED, HEADER_WAIT, DATA_WAIT,
                      OUTPUT_LOAD, OUTPUT_SEND);
  type frame_memory is array (0 to 4095) of std_logic_vector(7 downto 0);
  signal ram : frame_memory;
  signal state : state_type := IDLE;
  signal symbol_data : std_logic_vector(1535 downto 0);
  signal observations : std_logic_vector(287 downto 0);
  signal carrier : integer range 0 to 47 := 0;
  signal bpsc : integer range 1 to 6 := 1;
  signal ncbps : integer range 48 to 288 := 48;
  signal rate_value : integer range 0 to 54 := 0;
  signal frame_length : integer range 0 to 4095 := 0;
  signal obs_pos : integer range 0 to 288 := 0;
  signal mask_phase : integer range 0 to 5 := 0;
  signal cooldown : integer range 0 to 2 := 0;
  signal steps : integer range 0 to 32782 := 0;
  signal target_steps : integer range 0 to 32782 := 24;
  signal decoded_count : integer range 0 to 32782 := 0;
  signal symbol_last, decoding_header, decode_active, packet_active : std_logic := '0';
  signal signal_bits : std_logic_vector(23 downto 0) := (others => '0');
  signal service_prefix : std_logic_vector(6 downto 0) := (others => '0');
  signal scrambler : std_logic_vector(6 downto 0) := (others => '0');
  signal service_bad : std_logic := '0';
  signal byte_work : std_logic_vector(7 downto 0) := (others => '0');
  signal byte_bit : integer range 0 to 7 := 0;
  signal write_index : integer range 0 to 4095 := 0;
  signal read_index : integer range 0 to 4095 := 0;
  signal crc : unsigned(31 downto 0) := (others => '1');
  signal fcs_good : std_logic := '0';
  signal output_byte : std_logic_vector(7 downto 0) := (others => '0');
  signal v_reset, local_reset : std_logic := '0';
  signal v_valid, v_start, v_last : std_logic := '0';
  signal v_pair : std_logic_vector(3 downto 0) := (others => '0');
  signal v_out_valid, v_out_bit, v_out_last : std_logic;

  function demap_carrier(re, im, modulation : integer)
    return std_logic_vector is
    variable result : std_logic_vector(5 downto 0) := (others => '0');
    variable magnitude, value, width, base : integer;
  begin
    if modulation = 1 then
      if re >= 0 then result(0) := '1'; end if;
    else
      width := modulation / 2;
      for axis in 0 to 1 loop
        if axis = 0 then value := re; else value := im; end if;
        if value < 0 then
          magnitude := -value;
        else
          magnitude := value;
        end if;
        base := axis * width;
        if value >= 0 then result(base) := '1'; end if;
        if width = 2 then
          if magnitude < 2591 then result(base + 1) := '1'; end if;
        elsif width = 3 then
          if magnitude < 2529 then result(base + 1) := '1'; end if;
          if magnitude >= 1265 and magnitude < 3793 then
            result(base + 2) := '1';
          end if;
        end if;
      end loop;
    end if;
    return result;
  end function;

  -- k is the original encoder/puncturer index. The returned index is
  -- the position transmitted in the interleaved symbol. All divisors
  -- are constants to avoid a general-purpose hardware divider.
  function interleaved_index(k, modulation : integer) return integer is
    variable i : integer range 0 to 287;
  begin
    case modulation is
      when 1 =>
        i := 3 * (k mod 16) + k / 16;
        return i;
      when 2 =>
        i := 6 * (k mod 16) + k / 16;
        return i;
      when 4 =>
        i := 12 * (k mod 16) + k / 16;
        return 2 * (i / 2) + (i + 192 - i / 12) mod 2;
      when others =>
        i := 18 * (k mod 16) + k / 16;
        return 3 * (i / 3) + (i + 288 - i / 18) mod 3;
    end case;
  end function;

  function keep_observation(rate, phase : integer) return boolean is
  begin
    if rate = 48 then
      return phase /= 3;
    elsif rate = 9 or rate = 18 or rate = 36 or rate = 54 then
      return phase /= 3 and phase /= 4;
    else
      return true;
    end if;
  end function;

  function mask_size(rate : integer) return integer is
  begin
    if rate = 48 then return 4;
    elsif rate = 9 or rate = 18 or rate = 36 or rate = 54 then return 6;
    else return 2;
    end if;
  end function;

  function modulation_for(rate : integer) return integer is
  begin
    case rate is
      when 6 | 9 => return 1;
      when 12 | 18 => return 2;
      when 24 | 36 => return 4;
      when others => return 6;
    end case;
  end function;

begin
  v_reset <= rst or local_reset;
  decoder : entity work.wifi_viterbi
    port map (
      clk => clk, rst => v_reset,
      in_valid => v_valid, in_start => v_start, in_last => v_last,
      pair => v_pair, out_valid => v_out_valid,
      out_bit => v_out_bit, out_last => v_out_last
    );

  in_ready <= '1' when state = IDLE and rst = '0' else '0';
  out_valid <= '1' when state = OUTPUT_SEND else '0';
  out_first <= '1' when state = OUTPUT_SEND and read_index = 0 else '0';
  out_last <= '1' when state = OUTPUT_SEND and read_index = frame_length - 1 else '0';
  output_data <= output_byte;
  out_rate <= std_logic_vector(to_unsigned(rate_value, 8));
  out_length <= std_logic_vector(to_unsigned(frame_length, 12));
  out_fcs_ok <= fcs_good;
  overflow <= '0';

  process(clk)
    variable re, im, effective_rate, phase_v, pos_v, r, len : integer;
    variable carrier_re_word, carrier_im_word : std_logic_vector(15 downto 0);
    variable db : std_logic_vector(5 downto 0);
    variable pair_v : std_logic_vector(3 downto 0);
    variable hb : std_logic_vector(23 downto 0);
    variable prefix_v, seed_v, scr_v : std_logic_vector(6 downto 0);
    variable byte_v : std_logic_vector(7 downto 0);
    variable parity_v, feedback, clear_bit : std_logic;
    variable crc_v : unsigned(31 downto 0);
    variable good, enough : boolean;
  begin
    if rising_edge(clk) then
      if rst = '1' then
        state <= IDLE;
        carrier <= 0;
        bpsc <= 1;
        ncbps <= 48;
        rate_value <= 0;
        frame_length <= 0;
        obs_pos <= 0;
        mask_phase <= 0;
        cooldown <= 0;
        steps <= 0;
        target_steps <= 24;
        decoded_count <= 0;
        symbol_last <= '0';
        decoding_header <= '0';
        decode_active <= '0';
        packet_active <= '0';
        signal_bits <= (others => '0');
        service_prefix <= (others => '0');
        scrambler <= (others => '0');
        service_bad <= '0';
        byte_work <= (others => '0');
        byte_bit <= 0;
        write_index <= 0;
        read_index <= 0;
        crc <= (others => '1');
        fcs_good <= '0';
        output_byte <= (others => '0');
        local_reset <= '0';
        v_valid <= '0';
        v_start <= '0';
        v_last <= '0';
        v_pair <= (others => '0');
        header_valid <= '0';
        header_error <= '0';
        header_rate <= (others => '0');
        header_length <= (others => '0');
      else
        header_valid <= '0';
        header_error <= '0';
        v_valid <= '0';
        v_start <= '0';
        v_last <= '0';
        local_reset <= '0';
        if cooldown /= 0 then cooldown <= cooldown - 1; end if;

        case state is
          when IDLE =>
            if in_valid = '1' then
              if in_header = '1' then
                symbol_data <= input_data;
                carrier <= 0;
                bpsc <= 1;
                ncbps <= 48;
                obs_pos <= 0;
                mask_phase <= 0;
                steps <= 0;
                target_steps <= 24;
                decoded_count <= 0;
                signal_bits <= (others => '0');
                decoding_header <= '1';
                decode_active <= '1';
                packet_active <= '0';
                symbol_last <= '0';
                local_reset <= '1';
                state <= DEMAP;
              elsif packet_active = '1' then
                symbol_data <= input_data;
                carrier <= 0;
                obs_pos <= 0;
                symbol_last <= in_last;
                state <= DEMAP;
              end if;
            end if;

          when DEMAP =>
            carrier_re_word := symbol_data(32 * carrier + 15 downto 32 * carrier);
            carrier_im_word := symbol_data(32 * carrier + 31 downto 32 * carrier + 16);
            re := to_integer(signed(carrier_re_word));
            im := to_integer(signed(carrier_im_word));
            db := demap_carrier(re, im, bpsc);
            for bit_index in 0 to 5 loop
              if bit_index < bpsc then
                observations(carrier * bpsc + bit_index) <= db(bit_index);
              end if;
            end loop;
            if carrier = 47 then
              state <= FEED;
            else
              carrier <= carrier + 1;
            end if;

          when FEED =>
            if cooldown = 0 then
              if decoding_header = '1' then effective_rate := 6;
              else effective_rate := rate_value;
              end if;
              phase_v := mask_phase;
              pos_v := obs_pos;
              pair_v := (others => '0');
              enough := true;
              for lane in 0 to 1 loop
                if keep_observation(effective_rate, phase_v) then
                  if pos_v < ncbps then
                    pair_v(2 * lane) := observations(interleaved_index(pos_v, bpsc));
                    pair_v(2 * lane + 1) := '0';
                    pos_v := pos_v + 1;
                  else
                    enough := false;
                  end if;
                else
                  pair_v(2 * lane + 1 downto 2 * lane) := "10";
                end if;
                if phase_v = mask_size(effective_rate) - 1 then phase_v := 0;
                else phase_v := phase_v + 1;
                end if;
              end loop;
              if enough then
                v_pair <= pair_v;
                v_valid <= '1';
                if steps = 0 then v_start <= '1'; end if;
                cooldown <= 2;
                steps <= steps + 1;
                obs_pos <= pos_v;
                mask_phase <= phase_v;
                if steps + 1 = target_steps then
                  v_last <= '1';
                  if decoding_header = '1' then state <= HEADER_WAIT;
                  else state <= DATA_WAIT;
                  end if;
                elsif pos_v = ncbps then
                  if symbol_last = '1' then
                    packet_active <= '0';
                    decode_active <= '0';
                    local_reset <= '1';
                  end if;
                  state <= IDLE;
                end if;
              else
                packet_active <= '0';
                decode_active <= '0';
                local_reset <= '1';
                state <= IDLE;
              end if;
            end if;

          when HEADER_WAIT | DATA_WAIT =>
            null;

          when OUTPUT_LOAD =>
            output_byte <= ram(read_index);
            state <= OUTPUT_SEND;

          when OUTPUT_SEND =>
            if out_ready = '1' then
              if read_index = frame_length - 1 then
                state <= IDLE;
              else
                output_byte <= ram(read_index + 1);
                read_index <= read_index + 1;
              end if;
            end if;
        end case;

        if v_out_valid = '1' and decode_active = '1' then
          if decoding_header = '1' then
            hb := signal_bits;
            if decoded_count < 24 then
              hb(decoded_count) := v_out_bit;
              signal_bits <= hb;
            end if;
            decoded_count <= decoded_count + 1;
            if v_out_last = '1' then
              decode_active <= '0';
              decoding_header <= '0';
              r := 0;
              case hb(3 downto 0) is
                when "1011" => r := 6;
                when "1111" => r := 9;
                when "1010" => r := 12;
                when "1110" => r := 18;
                when "1001" => r := 24;
                when "1101" => r := 36;
                when "1000" => r := 48;
                when "1100" => r := 54;
                when others => r := 0;
              end case;
              len := to_integer(unsigned(hb(16 downto 5)));
              parity_v := '0';
              for bit_index in 0 to 17 loop
                parity_v := parity_v xor hb(bit_index);
              end loop;
              good := decoded_count = 23 and r /= 0 and len >= 14
                      and hb(4) = '0' and parity_v = '0'
                      and hb(23 downto 18) = "000000";
              state <= IDLE;
              if good then
                header_valid <= '1';
                header_rate <= std_logic_vector(to_unsigned(r, 8));
                header_length <= std_logic_vector(to_unsigned(len, 12));
                rate_value <= r;
                frame_length <= len;
                bpsc <= modulation_for(r);
                ncbps <= 48 * modulation_for(r);
                target_steps <= 22 + 8 * len;
                steps <= 0;
                decoded_count <= 0;
                mask_phase <= 0;
                packet_active <= '1';
                decode_active <= '1';
                service_prefix <= (others => '0');
                scrambler <= (others => '0');
                service_bad <= '0';
                byte_work <= (others => '0');
                byte_bit <= 0;
                write_index <= 0;
                crc <= (others => '1');
                fcs_good <= '0';
              else
                header_error <= '1';
                packet_active <= '0';
              end if;
            end if;
          else
            decoded_count <= decoded_count + 1;
            if decoded_count < 7 then
              prefix_v := service_prefix;
              prefix_v(decoded_count) := v_out_bit;
              service_prefix <= prefix_v;
              if decoded_count = 6 then
                seed_v(0) := prefix_v(2) xor prefix_v(6);
                seed_v(1) := prefix_v(1) xor prefix_v(5);
                seed_v(2) := prefix_v(0) xor prefix_v(4);
                seed_v(3) := seed_v(0) xor prefix_v(3);
                seed_v(4) := seed_v(1) xor prefix_v(2);
                seed_v(5) := seed_v(2) xor prefix_v(1);
                seed_v(6) := seed_v(3) xor prefix_v(0);
                if seed_v = "0000000" then service_bad <= '1'; end if;
                scr_v := seed_v;
                for bit_index in 0 to 6 loop
                  feedback := scr_v(6) xor scr_v(3);
                  if (prefix_v(bit_index) xor feedback) /= '0' then
                    service_bad <= '1';
                  end if;
                  scr_v := scr_v(5 downto 0) & feedback;
                end loop;
                scrambler <= scr_v;
              end if;
            elsif decoded_count < 16 + 8 * frame_length then
              feedback := scrambler(6) xor scrambler(3);
              clear_bit := v_out_bit xor feedback;
              scrambler <= scrambler(5 downto 0) & feedback;
              if decoded_count < 16 then
                if clear_bit /= '0' then service_bad <= '1'; end if;
              else
                byte_v := byte_work;
                byte_v(byte_bit) := clear_bit;
                byte_work <= byte_v;
                crc_v := shift_right(crc, 1);
                if (crc(0) xor clear_bit) = '1' then
                  crc_v := crc_v xor unsigned'(x"EDB88320");
                end if;
                crc <= crc_v;
                if byte_bit = 7 then
                  ram(write_index) <= byte_v;
                  write_index <= write_index + 1;
                  byte_bit <= 0;
                else
                  byte_bit <= byte_bit + 1;
                end if;
              end if;
            end if;
            if v_out_last = '1' then
              decode_active <= '0';
              packet_active <= '0';
              if service_bad = '0' and decoded_count = target_steps - 1
                 and write_index = frame_length and byte_bit = 0 then
                if crc = unsigned'(x"DEBB20E3") then fcs_good <= '1';
                else fcs_good <= '0';
                end if;
                read_index <= 0;
                state <= OUTPUT_LOAD;
              else
                state <= IDLE;
              end if;
            end if;
          end if;
        end if;
      end if;
    end if;
  end process;
end architecture;


library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
package wifi_front_pkg is
  type int_table is array(natural range <>) of integer;
  constant sine_q : int_table(0 to 64) := (
    0,402,804,1205,1606,2006,2404,2801,3196,3590,3981,4370,
    4756,5139,5520,5897,6270,6639,7005,7366,7723,8076,8423,
    8765,9102,9434,9760,10080,10394,10702,11003,11297,11585,
    11866,12140,12406,12665,12916,13160,13395,13623,13842,
    14053,14256,14449,14635,14811,14978,15137,15286,15426,
    15557,15679,15791,15893,15986,16069,16143,16207,16261,
    16305,16340,16364,16379,16384);
  constant polarity : int_table(0 to 126) := (
    1,1,1,1,-1,-1,-1,1,-1,-1,-1,-1,1,1,-1,1,-1,-1,1,
    1,-1,1,1,-1,1,1,1,1,1,1,-1,1,1,1,-1,1,1,-1,-1,
    1,1,1,-1,1,-1,-1,-1,1,-1,1,-1,-1,1,-1,-1,1,1,
    1,1,1,-1,-1,1,1,-1,-1,1,-1,1,-1,1,1,-1,-1,-1,1,
    1,-1,-1,-1,-1,1,-1,-1,1,-1,1,1,1,1,-1,1,-1,1,
    -1,1,-1,-1,-1,-1,-1,1,-1,1,1,-1,1,-1,1,1,1,-1,
    -1,1,-1,-1,-1,1,1,1,-1,-1,-1,-1,-1,-1,-1);
  function iabs(x : integer) return integer;
  function training_sign(a : natural) return integer;
  function sin16(p : integer) return integer;
  function angle16(x,y : signed) return integer;
  function sat16(x : signed) return signed;
  function sat24(x : signed) return signed;
  function rotate_sample(w : std_logic_vector(31 downto 0); p : integer) return std_logic_vector;
  function carrier(a : natural) return integer;
  function bin_number(a : natural) return natural;
  function is_pilot(a : natural) return boolean;
  function lts_real(n : natural) return integer;
  function lts_imag(n : natural) return integer;
  function tapmul(x : signed(15 downto 0); y : integer) return signed;
  function dbps(rate : std_logic_vector(7 downto 0)) return positive;
end package;
package body wifi_front_pkg is
  -- All callers are bounded strictly above integer'low.
  function iabs(x : integer) return integer is
  begin
    if x<0 then return -x; else return x; end if;
  end;
  function training_sign(a : natural) return integer is
    constant s : int_table(0 to 51) := (
      1,1,-1,-1,1,1,-1,1,-1,1,1,1,1,1,1,-1,-1,1,1,
      -1,1,-1,1,1,1,1,1,-1,-1,1,1,-1,1,-1,1,-1,-1,-1,
      -1,-1,1,1,-1,-1,1,-1,1,-1,1,1,1,1);
  begin return s(a); end;
  function sin16(p : integer) return integer is
    variable t,q,j,f,v : integer;
  begin
    t:=p mod 65536; q:=t/16384; t:=t mod 16384;
    if q=1 or q=3 then t:=16384-t; end if;
    if t=16384 then v:=16384;
    else j:=t/256; f:=t mod 256;
      v:=sine_q(j)+((sine_q(j+1)-sine_q(j))*f)/256;
    end if;
    if q>=2 then v:=-v; end if;
    return v;
  end;
  function angle16(x,y : signed) return integer is
    constant ar : int_table(0 to 14) := (8192,4836,2555,1297,651,326,163,81,41,20,10,5,3,1,1);
    variable xx,yy,tx : signed(35 downto 0);
    variable z : integer;
  begin
    xx:=resize(x,36); yy:=resize(y,36); z:=0;
    if xx=0 and yy=0 then return 0; end if;
    if xx<0 then
      if yy>=0 then z:=32768; else z:=-32768; end if;
      xx:=-xx; yy:=-yy;
    end if;
    for j in 0 to 14 loop
      tx:=xx;
      if yy>0 then
        xx:=xx+shift_right(yy,j); yy:=yy-shift_right(tx,j); z:=z+ar(j);
      elsif yy<0 then
        xx:=xx-shift_right(yy,j); yy:=yy+shift_right(tx,j); z:=z-ar(j);
      end if;
    end loop;
    if z>=32768 then z:=z-65536; end if;
    if z < -32768 then z:=z+65536; end if;
    return z;
  end;
  function sat16(x : signed) return signed is
  begin
    if x>to_signed(32767,x'length) then return to_signed(32767,16);
    elsif x<to_signed(-32768,x'length) then return to_signed(-32768,16);
    else return resize(x,16); end if;
  end;
  function sat24(x : signed) return signed is
  begin
    if x>to_signed(8388607,x'length) then return to_signed(8388607,24);
    elsif x<to_signed(-8388608,x'length) then return to_signed(-8388608,24);
    else return resize(x,24); end if;
  end;
  function rotate_sample(w : std_logic_vector(31 downto 0); p : integer) return std_logic_vector is
    variable r,i,c,s : signed(15 downto 0);
    variable rr,ii : signed(32 downto 0);
    variable answer : std_logic_vector(31 downto 0);
  begin
    r:=signed(w(31 downto 16)); i:=signed(w(15 downto 0));
    c:=to_signed(sin16(p+16384),16); s:=to_signed(sin16(p),16);
    rr:=resize(r*c,33)+resize(i*s,33); ii:=resize(i*c,33)-resize(r*s,33);
    answer(31 downto 16):=std_logic_vector(sat16(shift_right(rr,14)));
    answer(15 downto 0):=std_logic_vector(sat16(shift_right(ii,14)));
    return answer;
  end;
  function carrier(a : natural) return integer is
  begin
    if a<26 then return integer(a)-26; else return integer(a)-25; end if;
  end;
  function bin_number(a : natural) return natural is
  begin return carrier(a) mod 64; end;
  function is_pilot(a : natural) return boolean is
    variable k : integer;
  begin k:=carrier(a); return k=-21 or k=-7 or k=7 or k=21; end;
  function lts_real(n : natural) return integer is
    variable v : integer:=0;
  begin
    for a in 0 to 51 loop v:=v+training_sign(a)*sin16(carrier(a)*integer(n)*1024+16384); end loop;
    return v/64;
  end;
  function lts_imag(n : natural) return integer is
    variable v : integer:=0;
  begin
    for a in 0 to 51 loop v:=v+training_sign(a)*sin16(carrier(a)*integer(n)*1024); end loop;
    return v/64;
  end;
  function tapmul(x : signed(15 downto 0); y : integer) return signed is
    variable r : signed(23 downto 0):=(others=>'0');
    variable m : natural range 0 to 127;
  begin
    m:=iabs(y);
    for j in 0 to 6 loop
      if ((m/(2**j)) mod 2)=1 then r:=r+shift_left(resize(x,24),j); end if;
    end loop;
    if y<0 then r:=-r; end if;
    return r;
  end;
  function dbps(rate : std_logic_vector(7 downto 0)) return positive is
  begin
    case to_integer(unsigned(rate)) is
      when 6 => return 24; when 9 => return 36;
      when 12 => return 48; when 18 => return 72;
      when 24 => return 96; when 36 => return 144;
      when 48 => return 192; when 54 => return 216;
      when others => return 24;
    end case;
  end;
end package body;

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use work.wifi_front_pkg.all;
entity dut is
  port(
    clk,rst,in_valid,out_ready : in std_logic;
    input_data : in std_logic_vector(31 downto 0);
    out_valid,out_first,out_last,out_fcs_ok,overflow : out std_logic;
    output_data : out std_logic_vector(7 downto 0);
    out_rate : out std_logic_vector(7 downto 0);
    out_length : out std_logic_vector(11 downto 0);
    debug_valid : out std_logic;
    debug_tag : out std_logic_vector(7 downto 0);
    debug_data : out std_logic_vector(31 downto 0)
  );
end entity;
architecture rtl of dut is
  type raw_memory is array(0 to 8191) of std_logic_vector(31 downto 0);
  signal raw : raw_memory;
  signal wr_count : natural range 0 to 1073741823:=0;
  signal raw_address_a,raw_address_b : natural range 0 to 8191:=0;
  signal raw_a,raw_b : std_logic_vector(31 downto 0);
  type search_bank is array(0 to 27) of std_logic_vector(31 downto 0);
  type search_memory is array(0 to 15) of search_bank;
  signal search_ram : search_memory;
  type bank_addresses is array(0 to 15) of natural range 0 to 27;
  type sample_lanes is array(0 to 15) of std_logic_vector(31 downto 0);
  signal search_addr : bank_addresses:=(others=>0);
  signal search_q : sample_lanes;
  signal search_we : std_logic:='0';
  signal search_wa : natural range 0 to 447:=0;
  signal search_wd : std_logic_vector(31 downto 0);
  type lag_array is array(0 to 47) of integer range -8388608 to 8388608;
  type pow_array is array(0 to 47) of integer range 0 to 8388608;
  signal lag_r,lag_i : lag_array;
  signal lag_p : pow_array;
  signal lag_ptr : natural range 0 to 47:=0;
  signal lag_count : natural range 0 to 48:=0;
  signal sum_r,sum_i : integer range -402653184 to 402653184:=0;
  signal sum_p : integer range 0 to 402653184:=0;
  signal cursor,trigger,search_base,lts_start,window_start : natural range 0 to 1073741823:=0;
  signal refine_index : natural range 0 to 47:=0;
  signal refine_r,refine_i : integer range -402653184 to 402653184:=0;
  signal refine_p,refine_old_p : integer range 0 to 402653184:=0;
  signal cfo_step : integer range -2048 to 2048:=0;
  signal fill_index : natural range 0 to 447:=0;
  signal candidate : natural range 0 to 384:=0;
  signal tap_group : natural range 0 to 3:=0;
  signal corr_r,corr_i : signed(35 downto 0):=(others=>'0');
  type score_array is array(0 to 63) of unsigned(36 downto 0);
  signal scores : score_array;
  signal best_score : unsigned(36 downto 0):=(others=>'0');
  signal best_pos : natural range 0 to 320:=0;
  type tap_array is array(0 to 63) of integer range -127 to 127;
  function make_taps(im : boolean) return tap_array is
    variable t : tap_array;
    variable v : integer;
  begin
    for j in 0 to 63 loop
      if im then v:=lts_imag(j); else v:=lts_real(j); end if;
      if v>=0 then t(j):=(v+32)/64; else t(j):=(v-32)/64; end if;
    end loop;
    return t;
  end;
  constant taps_r : tap_array:=make_taps(false);
  constant taps_i : tap_array:=make_taps(true);
  type sum_lanes is array(0 to 15) of signed(35 downto 0);
  signal fft_iv,fft_ov : std_logic:='0';
  signal fft_input : std_logic_vector(2047 downto 0):=(others=>'0');
  signal fft_output,fft_hold : std_logic_vector(3071 downto 0);
  signal fft_mode : natural range 0 to 2:=0;
  signal load_index : natural range 0 to 63:=0;
  signal fft_cooldown : natural range 0 to 400:=0;
  type chan_array is array(0 to 51) of signed(23 downto 0);
  signal train_r,train_i : chan_array;
  type coeff_array is array(0 to 51) of signed(41 downto 0);
  signal coeff_r,coeff_i : coeff_array;
  signal active_index : natural range 0 to 51:=0;
  signal div_nr,div_ni,div_qr,div_qi : unsigned(63 downto 0):=(others=>'0');
  signal div_rr,div_ri : unsigned(49 downto 0):=(others=>'0');
  signal div_den : unsigned(48 downto 0):=(others=>'0');
  signal div_neg_r,div_neg_i : std_logic:='0';
  signal div_bit : natural range 0 to 63:=0;
  type equal_array is array(0 to 51) of signed(23 downto 0);
  signal eq_r,eq_i : equal_array;
  signal pilot_r,pilot_i : signed(28 downto 0):=(others=>'0');
  signal common_phase : integer range -32768 to 32767:=0;
  signal data_index : natural range 0 to 47:=0;
  signal symbol_index,symbol_count : natural range 0 to 1366:=0;
  signal pilot_index : natural range 0 to 126:=0;
  signal packet_length : std_logic_vector(11 downto 0):=(others=>'0');
  signal backend_iv,backend_ready,backend_header,backend_last : std_logic:='0';
  signal backend_data : std_logic_vector(1535 downto 0):=(others=>'0');
  signal header_valid,header_error : std_logic;
  signal header_rate : std_logic_vector(7 downto 0);
  signal header_length : std_logic_vector(11 downto 0);
  signal be_ov,be_first,be_last,be_fcs,be_overflow : std_logic;
  signal be_data,be_rate : std_logic_vector(7 downto 0);
  signal be_length : std_logic_vector(11 downto 0);
  signal sticky_overflow : std_logic:='0';
  signal header_timer : natural range 0 to 2047:=0;
  signal send_cooldown : natural range 0 to 799:=0;
  signal recovery_code : natural range 0 to 255:=1;
  type state_type is (
    SCAN_WAIT,SCAN_READ,SCAN_CALC,
    REFINE_WAIT,REFINE_READ,REFINE_CALC,REFINE_FINISH,
    FILL_WAIT,FILL_READ,FILL_WRITE,
    CORR_PRIME,CORR_RUN,LOCK_LTS,
    LOAD_WAIT,LOAD_READ,LOAD_STORE,FFT_LAUNCH,FFT_WAIT,
    TRAIN_FIRST,TRAIN_SETUP,DIVIDE_CHANNEL,DIVIDE_STORE,
    EQUALIZE,PHASE_ESTIMATE,ROTATE_BINS,PREP_SEND,SEND_SYMBOL,HEADER_WAIT,RECOVER
  );
  signal state : state_type:=SCAN_WAIT;
begin
  fft_inst : entity work.wifi_fft64
    port map(clk=>clk,rst=>rst,in_valid=>fft_iv,input_data=>fft_input,
             out_valid=>fft_ov,output_data=>fft_output);
  backend_inst : entity work.wifi_backend
    port map(clk=>clk,rst=>rst,in_valid=>backend_iv,
      in_header=>backend_header,in_last=>backend_last,out_ready=>out_ready,
      input_data=>backend_data,in_ready=>backend_ready,
      header_valid=>header_valid,header_error=>header_error,
      header_rate=>header_rate,header_length=>header_length,
      out_valid=>be_ov,out_first=>be_first,out_last=>be_last,
      out_fcs_ok=>be_fcs,overflow=>be_overflow,output_data=>be_data,
      out_rate=>be_rate,out_length=>be_length);
  out_valid<=be_ov; out_first<=be_first; out_last<=be_last;
  out_fcs_ok<=be_fcs; output_data<=be_data; out_rate<=be_rate;
  out_length<=be_length; overflow<=sticky_overflow or be_overflow;

  process(clk)
  begin
    if rising_edge(clk) then
      raw_a<=raw(raw_address_a); raw_b<=raw(raw_address_b);
      if rst='0' and in_valid='1' then raw(wr_count mod 8192)<=input_data; end if;
    end if;
  end process;
  banks : for b in 0 to 15 generate
    process(clk)
    begin
      if rising_edge(clk) then
        search_q(b)<=search_ram(b)(search_addr(b));
        if rst='0' and search_we='1' and search_wa mod 16=b then
          search_ram(b)(search_wa/16)<=search_wd;
        end if;
      end if;
    end process;
  end generate;

  process(clk)
    variable ar,ai,br,bi : integer range -2048 to 2047;
    variable pr,pi : integer range -8388608 to 8388608;
    variable pp,op : integer range 0 to 8388608;
    variable sr,si : integer range -402653184 to 402653184;
    variable sp : integer range 0 to 402653184;
    variable magnitude,smallpart : integer range 0 to 603979776;
    variable aphase : integer;
    variable rotated : std_logic_vector(31 downto 0);
    variable cr,ci : signed(35 downto 0);
    variable vr,vi : sum_lanes;
    variable score,pair_score : unsigned(36 downto 0);
    variable xr,xi : signed(15 downto 0);
    variable fr,fi,hr,hi : signed(23 downto 0);
    variable fft_re_word,fft_im_word : std_logic_vector(23 downto 0);
    variable hsum_r,hsum_i : signed(24 downto 0);
    variable energy : unsigned(48 downto 0);
    variable rr,ri : unsigned(49 downto 0);
    variable qr,qi : unsigned(63 downto 0);
    variable coefftmp : signed(41 downto 0);
    variable er,ei : signed(66 downto 0);
    variable evr,evi : signed(23 downto 0);
    variable cc,ss : signed(15 downto 0);
    variable rotr,roti : signed(40 downto 0);
    variable bin,ps,nd,nsyms,ix,addr,t : integer;
    variable need : natural;
  begin
    if rising_edge(clk) then
      if rst='1' then
        state<=SCAN_WAIT; wr_count<=0; cursor<=16;
        trigger<=0; search_base<=0; lts_start<=0; window_start<=0;
        raw_address_a<=0; raw_address_b<=0; search_addr<=(others=>0);
        search_we<='0'; search_wa<=0; search_wd<=(others=>'0');
        lag_ptr<=0; lag_count<=0; sum_r<=0; sum_i<=0; sum_p<=0;
        refine_index<=0; refine_r<=0; refine_i<=0; refine_p<=0; refine_old_p<=0;
        cfo_step<=0; fill_index<=0; candidate<=0; tap_group<=0;
        corr_r<=(others=>'0'); corr_i<=(others=>'0');
        best_score<=(others=>'0'); best_pos<=0;
        fft_iv<='0'; fft_input<=(others=>'0'); fft_hold<=(others=>'0');
        fft_mode<=0; load_index<=0; fft_cooldown<=0;
        active_index<=0; div_bit<=0;
        div_nr<=(others=>'0'); div_ni<=(others=>'0');
        div_qr<=(others=>'0'); div_qi<=(others=>'0');
        div_rr<=(others=>'0'); div_ri<=(others=>'0');
        div_den<=(others=>'0'); div_neg_r<='0'; div_neg_i<='0';
        pilot_r<=(others=>'0'); pilot_i<=(others=>'0'); common_phase<=0;
        data_index<=0; symbol_index<=0; symbol_count<=0; pilot_index<=0;
        packet_length<=(others=>'0'); backend_iv<='0';
        backend_header<='0'; backend_last<='0'; backend_data<=(others=>'0');
        sticky_overflow<='0'; header_timer<=0; send_cooldown<=0; recovery_code<=1;
        debug_valid<='0'; debug_tag<=(others=>'0'); debug_data<=(others=>'0');
      else
        debug_valid<='0'; fft_iv<='0'; search_we<='0';
        if in_valid='1' then wr_count<=wr_count+1; end if;
        if fft_cooldown>0 then fft_cooldown<=fft_cooldown-1; end if;
        if send_cooldown>0 then send_cooldown<=send_cooldown-1; end if;
        if be_overflow='1' then sticky_overflow<='1'; end if;
        if be_ov='1' and out_ready='1' and be_last='1' then
          debug_valid<='1'; debug_tag<=x"04";
          debug_data<=std_logic_vector(resize(unsigned(be_length),32));
        end if;
        case state is
          when SCAN_WAIT =>
            backend_iv<='0';
            if cursor>=16384 and wr_count>=cursor then
              -- Sample indices, including debug indices, use a local epoch.
              -- A full ring shift preserves addresses and rolling STF history.
              -- Override the earlier increment while retaining this edge's input.
              if in_valid='1' then wr_count<=wr_count-8192+1;
              else wr_count<=wr_count-8192; end if;
              cursor<=cursor-8192;
              -- Dead until SCAN_CALC sets trigger/search_base and LOCK_LTS
              -- sets lts_start/window_start for the next packet.
              trigger<=0; search_base<=0; lts_start<=0; window_start<=0;
              -- Stay in SCAN_WAIT; issue no scan using the old epoch.
            elsif wr_count>cursor then
              if wr_count-cursor>8175 then
                sticky_overflow<='1'; cursor<=wr_count-16;
                lag_count<=0; lag_ptr<=0; sum_r<=0; sum_i<=0; sum_p<=0;
              else
                raw_address_a<=cursor mod 8192;
                raw_address_b<=(cursor-16) mod 8192; state<=SCAN_READ;
              end if;
            end if;
          when SCAN_READ => state<=SCAN_CALC;
          when SCAN_CALC =>
            ar:=to_integer(shift_right(signed(raw_a(31 downto 16)),4));
            ai:=to_integer(shift_right(signed(raw_a(15 downto 0)),4));
            br:=to_integer(shift_right(signed(raw_b(31 downto 16)),4));
            bi:=to_integer(shift_right(signed(raw_b(15 downto 0)),4));
            pr:=br*ar+bi*ai; pi:=br*ai-bi*ar; pp:=ar*ar+ai*ai;
            if lag_count=48 then
              sr:=sum_r-lag_r(lag_ptr)+pr; si:=sum_i-lag_i(lag_ptr)+pi;
              sp:=sum_p-lag_p(lag_ptr)+pp;
            else
              sr:=sum_r+pr; si:=sum_i+pi; sp:=sum_p+pp; lag_count<=lag_count+1;
            end if;
            lag_r(lag_ptr)<=pr; lag_i(lag_ptr)<=pi; lag_p(lag_ptr)<=pp;
            if lag_ptr=47 then lag_ptr<=0; else lag_ptr<=lag_ptr+1; end if;
            sum_r<=sr; sum_i<=si; sum_p<=sp;
            if iabs(sr)>iabs(si) then magnitude:=iabs(sr); smallpart:=iabs(si);
            else magnitude:=iabs(si); smallpart:=iabs(sr); end if;
            magnitude:=magnitude+smallpart/2;
            cursor<=cursor+1; state<=SCAN_WAIT;
            if lag_count>=47 and sp>1875 and magnitude>(sp/4)*3 then
              trigger<=cursor;
              if cursor>=96 then search_base<=cursor-96; else search_base<=0; end if;
              refine_index<=0; refine_r<=0; refine_i<=0; refine_p<=0; refine_old_p<=0;
              state<=REFINE_WAIT;
              debug_valid<='1'; debug_tag<=x"01";
              debug_data<=std_logic_vector(to_unsigned(cursor,32));
            end if;
          when REFINE_WAIT =>
            need:=trigger+48+refine_index;
            if wr_count>need then
              if wr_count-(need-16)>8191 then
                sticky_overflow<='1'; recovery_code<=2; state<=RECOVER;
              else
                raw_address_a<=need mod 8192;
                raw_address_b<=(need-16) mod 8192; state<=REFINE_READ;
              end if;
            end if;
          when REFINE_READ => state<=REFINE_CALC;
          when REFINE_CALC =>
            ar:=to_integer(shift_right(signed(raw_a(31 downto 16)),4));
            ai:=to_integer(shift_right(signed(raw_a(15 downto 0)),4));
            br:=to_integer(shift_right(signed(raw_b(31 downto 16)),4));
            bi:=to_integer(shift_right(signed(raw_b(15 downto 0)),4));
            pr:=br*ar+bi*ai; pi:=br*ai-bi*ar;
            pp:=ar*ar+ai*ai; op:=br*br+bi*bi;
            refine_r<=refine_r+pr; refine_i<=refine_i+pi;
            refine_p<=refine_p+pp; refine_old_p<=refine_old_p+op;
            if refine_index=47 then state<=REFINE_FINISH;
            else refine_index<=refine_index+1; state<=REFINE_WAIT; end if;
          when REFINE_FINISH =>
            if iabs(refine_r)>iabs(refine_i) then
              magnitude:=iabs(refine_r); smallpart:=iabs(refine_i);
            else magnitude:=iabs(refine_i); smallpart:=iabs(refine_r); end if;
            magnitude:=magnitude+smallpart/2;
            if refine_p>1875 and refine_old_p>1875 and
               magnitude>(refine_p/4)*3 and magnitude>(refine_old_p/4)*3 then
              aphase:=angle16(to_signed(refine_r,32),to_signed(refine_i,32));
              if aphase>=0 then t:=(aphase+8)/16; else t:=(aphase-8)/16; end if;
              cfo_step<=t; fill_index<=0; state<=FILL_WAIT;
              debug_valid<='1'; debug_tag<=x"07";
              debug_data<=std_logic_vector(to_signed(t,32));
            else recovery_code<=3; state<=RECOVER; end if;
          when FILL_WAIT =>
            need:=search_base+fill_index;
            if wr_count>need then
              if wr_count-need>8191 then
                sticky_overflow<='1'; recovery_code<=2; state<=RECOVER;
              else raw_address_a<=need mod 8192; state<=FILL_READ; end if;
            end if;
          when FILL_READ => state<=FILL_WRITE;
          when FILL_WRITE =>
            aphase:=(cfo_step*((search_base+fill_index) mod 65536)) mod 65536;
            search_wd<=rotate_sample(raw_a,aphase);
            search_wa<=fill_index; search_we<='1';
            if fill_index=447 then
              candidate<=0; tap_group<=0; search_addr<=(others=>0);
              corr_r<=(others=>'0'); corr_i<=(others=>'0');
              best_score<=(others=>'0'); best_pos<=0; state<=CORR_PRIME;
            else fill_index<=fill_index+1; state<=FILL_WAIT; end if;
          when CORR_PRIME =>
            -- Group zero is fetched on this edge; issue group one.
            for b in 0 to 15 loop
              addr:=candidate+16+((b-(candidate mod 16)+16) mod 16);
              search_addr(b)<=addr/16;
            end loop;
            tap_group<=0; corr_r<=(others=>'0'); corr_i<=(others=>'0');
            state<=CORR_RUN;
          when CORR_RUN =>
            for j in 0 to 15 loop
              ix:=(candidate+j) mod 16;
              xr:=signed(search_q(ix)(31 downto 16));
              xi:=signed(search_q(ix)(15 downto 0));
              t:=16*tap_group+j;
              vr(j):=resize(tapmul(xr,taps_r(t)),36)+resize(tapmul(xi,taps_i(t)),36);
              vi(j):=resize(tapmul(xi,taps_r(t)),36)-resize(tapmul(xr,taps_i(t)),36);
            end loop;
            for j in 0 to 7 loop vr(j):=vr(2*j)+vr(2*j+1); vi(j):=vi(2*j)+vi(2*j+1); end loop;
            for j in 0 to 3 loop vr(j):=vr(2*j)+vr(2*j+1); vi(j):=vi(2*j)+vi(2*j+1); end loop;
            for j in 0 to 1 loop vr(j):=vr(2*j)+vr(2*j+1); vi(j):=vi(2*j)+vi(2*j+1); end loop;
            cr:=corr_r+vr(0)+vr(1); ci:=corr_i+vi(0)+vi(1);
            corr_r<=cr; corr_i<=ci;
            if tap_group=3 then
              -- Retire the candidate on its final MAC cycle. Use the
              -- completed variables, including group three, not old signals.
              score:=resize(unsigned(abs(cr)),37)+resize(unsigned(abs(ci)),37);
              if candidate>=64 then
                if score<scores(candidate mod 64) then pair_score:=score;
                else pair_score:=scores(candidate mod 64); end if;
                if pair_score>best_score then
                  best_score<=pair_score; best_pos<=candidate-64;
                end if;
              end if;
              scores(candidate mod 64)<=score;
              if candidate=384 then state<=LOCK_LTS;
              else
                candidate<=candidate+1;
                for b in 0 to 15 loop
                  addr:=candidate+1+((b-((candidate+1) mod 16)+16) mod 16);
                  search_addr(b)<=addr/16;
                end loop;
                state<=CORR_PRIME;
              end if;
            else
              tap_group<=tap_group+1;
              if tap_group<2 then
                for b in 0 to 15 loop
                  addr:=candidate+16*(tap_group+2)+((b-(candidate mod 16)+16) mod 16);
                  search_addr(b)<=addr/16;
                end loop;
              end if;
            end if;
          when LOCK_LTS =>
            if best_score=0 then recovery_code<=4; state<=RECOVER;
            else
              lts_start<=search_base+best_pos; window_start<=search_base+best_pos;
              fft_mode<=0; load_index<=0; state<=LOAD_WAIT;
              debug_valid<='1'; debug_tag<=x"02";
              debug_data<=std_logic_vector(to_unsigned(search_base+best_pos,32));
            end if;
          when LOAD_WAIT =>
            need:=window_start+load_index;
            if wr_count>need then
              if wr_count-need>8191 then
                sticky_overflow<='1'; recovery_code<=2; state<=RECOVER;
              else raw_address_a<=need mod 8192; state<=LOAD_READ; end if;
            end if;
          when LOAD_READ => state<=LOAD_STORE;
          when LOAD_STORE =>
            aphase:=(cfo_step*((window_start+load_index) mod 65536)) mod 65536;
            rotated:=rotate_sample(raw_a,aphase);
            fft_input(32*load_index+15 downto 32*load_index)<=rotated(31 downto 16);
            fft_input(32*load_index+31 downto 32*load_index+16)<=rotated(15 downto 0);
            if load_index=63 then state<=FFT_LAUNCH;
            else load_index<=load_index+1; state<=LOAD_WAIT; end if;
          when FFT_LAUNCH =>
            if fft_cooldown=0 then fft_iv<='1'; fft_cooldown<=400; state<=FFT_WAIT; end if;
          when FFT_WAIT =>
            if fft_ov='1' then
              fft_hold<=fft_output; active_index<=0;
              debug_valid<='1'; debug_tag<=x"06";
              if fft_mode=0 then debug_data<=x"FFFFFFFE";
              elsif fft_mode=1 then debug_data<=x"FFFFFFFF";
              else debug_data<=std_logic_vector(to_unsigned(symbol_index,32)); end if;
              if fft_mode=0 then state<=TRAIN_FIRST;
              elsif fft_mode=1 then state<=TRAIN_SETUP;
              else pilot_r<=(others=>'0'); pilot_i<=(others=>'0'); state<=EQUALIZE; end if;
            end if;
          when TRAIN_FIRST =>
            bin:=bin_number(active_index);
            fft_re_word:=fft_hold(48*bin+23 downto 48*bin);
            fft_im_word:=fft_hold(48*bin+47 downto 48*bin+24);
            train_r(active_index)<=signed(fft_re_word);
            train_i(active_index)<=signed(fft_im_word);
            if active_index=51 then
              fft_mode<=1; window_start<=lts_start+64; load_index<=0; state<=LOAD_WAIT;
            else active_index<=active_index+1; end if;
          when TRAIN_SETUP =>
            bin:=bin_number(active_index);
            fft_re_word:=fft_hold(48*bin+23 downto 48*bin);
            fft_im_word:=fft_hold(48*bin+47 downto 48*bin+24);
            fr:=signed(fft_re_word);
            fi:=signed(fft_im_word);
            hsum_r:=resize(fr,25)+resize(train_r(active_index),25);
            hsum_i:=resize(fi,25)+resize(train_i(active_index),25);
            if training_sign(active_index)<0 then hsum_r:=-hsum_r; hsum_i:=-hsum_i; end if;
            hr:=sat24(shift_right(hsum_r,1)); hi:=sat24(shift_right(hsum_i,1));
            energy:=resize(unsigned(hr*hr),49)+resize(unsigned(hi*hi),49);
            if energy=0 then recovery_code<=5; state<=RECOVER;
            else
              div_den<=energy;
              div_nr<=shift_left(resize(unsigned(abs(resize(hr,25))),64),40);
              div_ni<=shift_left(resize(unsigned(abs(resize(hi,25))),64),40);
              div_neg_r<=hr(23); div_neg_i<=not hi(23);
              div_rr<=(others=>'0'); div_ri<=(others=>'0');
              div_qr<=(others=>'0'); div_qi<=(others=>'0');
              div_bit<=63; state<=DIVIDE_CHANNEL;
            end if;
          when DIVIDE_CHANNEL =>
            -- Eight exact restoring steps per cycle, training only.
            -- The same 64 numerator bits and quotient precision are retained.
            rr:=div_rr; ri:=div_ri; qr:=div_qr; qi:=div_qi;
            for j in 0 to 7 loop
              rr:=shift_left(rr,1); rr(0):=div_nr(div_bit-j);
              ri:=shift_left(ri,1); ri(0):=div_ni(div_bit-j);
              if rr>=resize(div_den,50) then rr:=rr-resize(div_den,50); qr(div_bit-j):='1'; end if;
              if ri>=resize(div_den,50) then ri:=ri-resize(div_den,50); qi(div_bit-j):='1'; end if;
            end loop;
            div_rr<=rr; div_ri<=ri; div_qr<=qr; div_qi<=qi;
            if div_bit=7 then state<=DIVIDE_STORE; else div_bit<=div_bit-8; end if;
          when DIVIDE_STORE =>
            coefftmp:=signed(resize(div_qr,42));
            if div_neg_r='1' then coefftmp:=-coefftmp; end if;
            coeff_r(active_index)<=coefftmp;
            coefftmp:=signed(resize(div_qi,42));
            if div_neg_i='1' then coefftmp:=-coefftmp; end if;
            coeff_i(active_index)<=coefftmp;
            if active_index=51 then
              fft_mode<=2; symbol_index<=0; pilot_index<=0;
              window_start<=lts_start+144; load_index<=0; state<=LOAD_WAIT;
            else active_index<=active_index+1; state<=TRAIN_SETUP; end if;
          when EQUALIZE =>
            bin:=bin_number(active_index);
            fft_re_word:=fft_hold(48*bin+23 downto 48*bin);
            fft_im_word:=fft_hold(48*bin+47 downto 48*bin+24);
            fr:=signed(fft_re_word);
            fi:=signed(fft_im_word);
            er:=resize(fr*coeff_r(active_index),67)-resize(fi*coeff_i(active_index),67);
            ei:=resize(fr*coeff_i(active_index),67)+resize(fi*coeff_r(active_index),67);
            evr:=sat24(shift_right(er,28)); evi:=sat24(shift_right(ei,28));
            eq_r(active_index)<=evr; eq_i(active_index)<=evi;
            if is_pilot(active_index) then
              ps:=polarity(pilot_index);
              if carrier(active_index)=21 then ps:=-ps; end if;
              if ps=1 then
                pilot_r<=pilot_r+resize(evr,29); pilot_i<=pilot_i+resize(evi,29);
              else pilot_r<=pilot_r-resize(evr,29); pilot_i<=pilot_i-resize(evi,29); end if;
            end if;
            if active_index=51 then state<=PHASE_ESTIMATE;
            else active_index<=active_index+1; end if;
          when PHASE_ESTIMATE =>
            common_phase<=angle16(pilot_r,pilot_i);
            active_index<=0; data_index<=0; state<=ROTATE_BINS;
          when ROTATE_BINS =>
            if not is_pilot(active_index) then
              cc:=to_signed(sin16(common_phase+16384),16); ss:=to_signed(sin16(common_phase),16);
              rotr:=resize(eq_r(active_index)*cc,41)+resize(eq_i(active_index)*ss,41);
              roti:=resize(eq_i(active_index)*cc,41)-resize(eq_r(active_index)*ss,41);
              backend_data(32*data_index+15 downto 32*data_index)<=std_logic_vector(sat16(shift_right(rotr,14)));
              backend_data(32*data_index+31 downto 32*data_index+16)<=std_logic_vector(sat16(shift_right(roti,14)));
              if data_index<47 then data_index<=data_index+1; end if;
            end if;
            if active_index=51 then
              if symbol_index=0 then backend_header<='1'; else backend_header<='0'; end if;
              if symbol_index/=0 and symbol_index=symbol_count then backend_last<='1'; else backend_last<='0'; end if;
              state<=PREP_SEND;
            else active_index<=active_index+1; end if;
          when PREP_SEND =>
            if send_cooldown=0 then backend_iv<='1'; state<=SEND_SYMBOL; end if;
          when SEND_SYMBOL =>
            if backend_ready='1' then
              backend_iv<='0'; send_cooldown<=798;
              if symbol_index=0 then header_timer<=0; state<=HEADER_WAIT;
              elsif symbol_index=symbol_count then
                cursor<=window_start+64;
                lag_count<=0; lag_ptr<=0; sum_r<=0; sum_i<=0; sum_p<=0;
                state<=SCAN_WAIT;
              else
                symbol_index<=symbol_index+1;
                if pilot_index=126 then pilot_index<=0; else pilot_index<=pilot_index+1; end if;
                window_start<=window_start+80; load_index<=0; state<=LOAD_WAIT;
              end if;
            end if;
          when HEADER_WAIT =>
            if header_error='1' then recovery_code<=6; state<=RECOVER;
            elsif header_valid='1' then
              if unsigned(header_length)<14 then recovery_code<=7; state<=RECOVER;
              else
                nd:=dbps(header_rate);
                nsyms:=(22+8*to_integer(unsigned(header_length))+nd-1)/nd;
                symbol_count<=nsyms; packet_length<=header_length;
                symbol_index<=1; pilot_index<=1;
                window_start<=lts_start+224; load_index<=0; state<=LOAD_WAIT;
                debug_valid<='1'; debug_tag<=x"03";
                debug_data<=x"00" & header_rate & "0000" & header_length;
              end if;
            elsif header_timer=2047 then recovery_code<=8; state<=RECOVER;
            else header_timer<=header_timer+1; end if;
          when RECOVER =>
            backend_iv<='0';
            cursor<=trigger+32;
            lag_count<=0; lag_ptr<=0; sum_r<=0; sum_i<=0; sum_p<=0;
            debug_valid<='1'; debug_tag<=x"05";
            debug_data<=std_logic_vector(to_unsigned(recovery_code,32));
            state<=SCAN_WAIT;
        end case;
      end if;
    end if;
  end process;
end architecture;

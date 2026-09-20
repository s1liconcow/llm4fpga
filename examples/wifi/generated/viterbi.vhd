library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity dut is
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

architecture rtl of dut is
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

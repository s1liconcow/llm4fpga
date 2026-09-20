library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity dut is
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

architecture rtl of dut is
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
        magnitude := abs(value);
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
            re := to_integer(signed(symbol_data(32 * carrier + 15 downto 32 * carrier)));
            im := to_integer(signed(symbol_data(32 * carrier + 31 downto 32 * carrier + 16)));
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
                read_index <= read_index + 1;
                state <= OUTPUT_LOAD;
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

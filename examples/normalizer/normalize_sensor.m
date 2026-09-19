function y = normalize_sensor(adc)
% Golden double-precision reference. adc is a signed 12-bit ADC code.
% A smooth bounded normalization / soft-limiting stage for a sensor signal.
% All 4096 ADC codes are legal. x is in [-1, 1 - 2^-11].
x = double(adc) ./ 2048.0;
y = x ./ sqrt(0.25 + x .* x);
end

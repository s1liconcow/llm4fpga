function export_golden(output_path)
% Compatible with MATLAB and GNU Octave.
adc = (-2048:2047)';
y = normalize_sensor(adc);
fid = fopen(output_path, 'w');
if fid < 0
    error('Could not open output file');
end
cleanup = onCleanup(@() fclose(fid));
fprintf(fid, 'adc,y\n');
for k = 1:numel(adc)
    fprintf(fid, '%d,%.17g\n', adc(k), y(k));
end
end

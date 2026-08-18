Calibration sensitivities with the RCA broadband correction (+128.9 dB) applied, converting
dB re 1 V/uPa to dB re 1 count/uPa. This is the directory the pipeline reads.

GENERATED - do not edit by hand. These are built by `cal-to-nc build` from the YAML specs in
../cal_specs, which are the reviewable source of truth. Hand edits are overwritten by the next
build and reported by `cal-to-nc check`.

The offset is the counts-per-volt of the 24-bit / 3 V ADC: 8388608 / 3 = 2796202 counts per volt
= 20log10(2796202) = 128.9 dB. Because it is baked into these files, audio_to_spec.py uses
VOLTAGE_MULTIPLIER = 1 - applying the correction in both places would double-count it.

pbp reads only the `sensitivity` variable. For a directional cal that is the 0/90 average plus the
offset; sensitivity_0 and sensitivity_90 are carried for reference and are never read, so an error
confined to those two does not affect calibrated output.

Each file records its origin in the netCDF attrs: source_spec, source_pdf, asset_id, serial_number,
sensitivity_units, rca_bb_offset, and a placeholder reason when the cal is a stand-in - find_cal_file
logs a run-time warning in that case. See "Hydrophone calibrations" in the repo README for how a cal
is selected for a given date and current known issues.

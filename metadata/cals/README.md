Calibration sensitivities as printed on the manufacturer sheets, in dB re 1 V/uPa.
This is the directory the pipeline reads.

GENERATED - do not edit by hand. These are built by `cal-to-nc build` from the YAML specs in
../cal_specs, which are the reviewable source of truth. Hand edits are overwritten by the next
build and reported by `cal-to-nc check`.

pbp reads only the `sensitivity` variable: the sheet curve directly, or the 0/90 average for
directional cals (sensitivity_0/sensitivity_90 are kept as the archival record of the sheet, so
an error confined to those two does not affect calibrated output). No counts-per-volt offset is
baked in here - stock mbari-pbp reads the FLAC as float normalized to 24-bit full scale, and
audio_to_spec.py converts to volts with VOLTAGE_MULTIPLIER = 3 (3 V full scale), so the sheet
values apply unmodified.

Each file records its origin in the netCDF attrs: source_spec (the YAML it came from), source_pdf
(the sheet that YAML was transcribed from), asset_id, serial_number, and a placeholder reason when
the cal is a stand-in for one that is unavailable - find_cal_file logs a run-time warning in that
case. See "Hydrophone calibrations" in the repo README for how a cal is selected for a given date
and current known issues.

Calibration sensitivities as printed on the manufacturer sheets, in dB re 1 V/uPa. This is
the directory the pipeline reads.

GENERATED - do not edit by hand. Built by `cal-to-nc build` from the YAML specs in
../cal_specs, which are the reviewable source of truth. Hand edits are overwritten by the
next build and reported by `cal-to-nc check`.

pbp reads only the `sensitivity` variable: the sheet curve directly, or the 0/90 average for
directional cals. sensitivity_0/sensitivity_90 are kept as the record of the sheet and are
never read, so an error confined to those two does not affect calibrated output. No
counts-per-volt offset is baked in here - audio_to_spec.py applies VOLTAGE_MULTIPLIER.

Attrs record origin: source_spec, source_pdf, asset_id, serial_number, and a placeholder
reason when the cal is a stand-in (find_cal_file warns at run time). See "Hydrophone
calibrations" in the repo README for cal selection and known issues.

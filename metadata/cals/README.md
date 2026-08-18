Exact calibration values as printed on the manufacturer sheets, in dB re 1 V/uPa.

GENERATED - do not edit by hand. These are built by `cal-to-nc build` from the YAML specs in
../cal_specs, which are the reviewable source of truth. Hand edits are overwritten by the next
build and reported by `cal-to-nc check`.

The pipeline does NOT read this directory. It reads ../rca_correction_cals, the same sensitivities
with the +128.9 dB counts-per-volt correction applied. This copy exists as the archival record of
what the sheet actually said.

Each file records its origin in the netCDF attrs: source_spec (the YAML it came from), source_pdf
(the sheet that YAML was transcribed from), asset_id, serial_number, and a placeholder reason when
the cal is a stand-in for one that is unavailable. See "Hydrophone calibrations" in the repo README
for the correction rationale, how a cal is selected for a given date, and current known issues.

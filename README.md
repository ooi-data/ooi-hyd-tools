# ooi-hyd-tools 

Assorted tools for processing Ocean Observatories Initiative hydrophone data. 
For otherOOI hydrophone tools see:

https://github.com/Ocean-Data-Lab/ooipy

https://github.com/bnestor/hydrophone_downloader

The repo adapts tools from: 

https://github.com/mbari-org/pbp

https://github.com/ioos/soundcoop

https://github.com/lifewatch/pypam

# How to convert ooi mseed archives to flac or wav
`git clone https://github.com/ooi-data/ooi-hyd-tools.git`

`conda create -n ooi-hyd-tools python=3.11 pip`

`conda activate ooi-hyd-tools`

`cd ooi-hyd-tools`

`pip install -e .`

Now you can run the `acoustic-pipeline` command to convert a single day or multiple days of archived ooi mseed to a day of 5 minute audio files.

```
acoustic-pipeline \
--hyd-refdes "CE04OSBP-LJ01C-11-HYDBBA105" \
--start-date "2025/02/20" \
--end-date "2025/03/15" \
--format PCM_24 \
--flag audio \
--fudge-factor 0.021
```
Run with `--flag all` to generate hybrid millidecade spectrograms.
`acoustic-pipeline --help` To learn more about each argument. 

Data for the audio stage of the pipeline is output to `./data` dir. Millidecade spectrogram plots are output to `./output` dir.

# How to extract audio of a single event

Use the `event-audio` command to pull a time window of broadband audio, run it through the same jitter/gap repair as the pipeline, and write a single continuous WAV to `./output/events` for listening.

For example, to extract the M5.5 Blanco Fracture Zone earthquake recorded 2026/06/29 at Slope Base Seafloor (the event runs 11:35:44–11:42:04 UTC):

```
event-audio \
--refdes "RS01SLBS-LJ01A-09-HYDBBA102" \
--start "2026-06-29T11:35:44" \
--end "2026-06-29T11:42:04" \
--bandpass 2 1000 \
--normalize \
--speed 2 \
--fade 1.0
```

`--speed` rewrites the sample rate `--bandpass LOW HIGH` isolates event of interest, `--normalize` boosts a quiet clip, and `--fade SEC` tapers both ends. `event-audio --help` for all arguments.

# Hydrophone calibrations

Cal files live in `./metadata` as netCDF, one per instrument per deployment, named `{refdes}_{deployment}.nc`:

- `metadata/cals` — exact values from the manufacturer PDF, in dB re 1 V/uPa.
- `metadata/rca_correction_cals` — the same with **+128.9 dB** applied, in dB re 1 count/uPa. **This is what the pipeline reads.**

The offset converts volts to the digital counts the archive stores: the 24-bit / 3 V ADC gives 8388608 / 3 = 2796202 counts per volt = 20log10(2796202) = 128.9 dB. Because it is baked into these files, `audio_to_spec.py` uses `VOLTAGE_MULTIPLIER = 1` when handing off data to mbari-pbp.

### Calibration yamls are the source of truth

 The reviewable source is one YAML spec per instrument in `metadata/cal_specs/`, holding every deployment (replacing `notebooks/03_PARSE_CAL_TO_NC.ipynb`):

```
cal-to-nc template > metadata/cal_specs/{refdes}.yaml   # scaffold, fill in from the PDF
cal-to-nc build metadata/cal_specs/*.yaml --plot        # write both .nc plus a QA plot
cal-to-nc check metadata/cal_specs/*.yaml               # CI: committed .nc still match specs
cal-to-nc from-nc {refdes}                              # backfill a spec from existing .nc
```

Add a deployment by appending a block under `deployments` and re-running `build`, which warns if mean sensitivity jumps more than 6 dB between consecutive deployments. Specs take either a single `sens` or directional `sens0`/`sens90` (averaged before the offset). Frequencies default to kHz; set `freq_units: Hz` otherwise. Validation rejects length mismatches, non-ascending frequencies, and values outside -220 to -120 dB.

Transcribing the PDF to yaml is the manual step.

### Where the PDFs come from

https://github.com/OOI-CabledArray/calibrationFiles/tree/master/HYDBBA — named `{asset_id}__{YYYYMMDD}.pdf`, matching the `asset_id` and `cal_date` in each spec. OOI asset management also has a `calibration/HYDBBA` directory, but the sensitivity tables exist only in these PDFs.

### How a cal file is selected

`find_cal_file()` in [audio_to_spec.py](ooi_hyd_tools/audio_to_spec.py) reads the deployment table live from OOI asset management:

```
https://raw.githubusercontent.com/oceanobservatories/asset-management/refs/heads/master/deployment/{node}_Deploy.csv
```

`{node}` is the first 8 characters of the refdes. The deployment whose window contains the date picks `rca_correction_cals/{refdes}_{deployment}.nc`; a missing file raises rather than silently producing uncalibrated output. That CSV's `sensor.uid` column also gives which physical asset was deployed, which is how a deployment maps to a cal sheet. `--apply-cals false` skips calibration.

Built files carry `source_spec`, `source_pdf`, `sensitivity_units`, `rca_bb_offset`, and `placeholder` when the cal is a stand-in — `find_cal_file` logs a run-time warning on placeholders.

### Known issues to fix

| issue | where | detail |
|---|---|---|
| Placeholder | `HYDBBA105` dep 10 | most recent available cal; the correct one is not yet published upstream |
| Reprocess | `HYDBBA102` 2016-07-17 to 2017-07-29 | deployment 3 had been calibrated with deployment 2's hydrophone. Cal is now correct; products from that window still carry the old one, which was **-2.10 dB off on average, up to 5.40 dB at 90.4 kHz** |
| Reprocess | `HYDBBA302` 2016-07-12 to 2017-07-31 | deployment 3 had been calibrated with a 2018 sheet postdating a hardware rebuild. Cal is now correct; products from that window are **-4.68 dB off on average, up to 8.65 dB at 190.7 kHz** |

Re-run `cal-to-nc build` after editing a spec. Calibrations exist for HYDBBA105, HYDBBA106, HYDBBA302 and HYDBBA303 since program inception; moorings since 2025.

# OOI reference designators (refdes) for broadband hydrophones and approximate lat/lon:

`"CE02SHBP-LJ01D-11-HYDBBA106": (44.63721, -124.30564), "Oregon Shelf"`

`"CE04OSBP-LJ01C-11-HYDBBA105": (44.36933, -124.95347), "Oregon Offshore"`

`"RS01SBPS-PC01A-08-HYDBBA103": (44.51516, -125.3899), "Slope Base Platform"`

`"RS01SLBS-LJ01A-09-HYDBBA102": (44.51505, -125.39002), "Slope Base Seafloor"`

`"RS03AXBS-LJ03A-09-HYDBBA302": (45.81676, -129.75426), "Axial Base Seafloor"`

`"RS03AXPS-PC03A-08-HYDBBA303": (45.81671, -129.75405), "Axial Base Platform"`


Interactive map of assets at https://app.interactiveoceans.washington.edu/map

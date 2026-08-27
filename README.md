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
--flag audio
```
Run with `--flag all` to generate hybrid millidecade spectrograms.
`acoustic-pipeline --help` To learn more about each argument. 

Data for the audio stage of the pipeline is output to `./data` dir. Millidecade spectrogram plots are output to `./output` dir.

### Jitter and gap repair

The DDS mislabels when each burst of samples arrived - by anywhere from a fraction of a
millisecond to a quarter second, and the pattern differs between instruments and between
years. It never moves or duplicates recording; only the labels are wrong. So the **number
of samples**, not the timestamps, decides whether any recording is actually missing.
Timestamps are only consulted afterwards, to find where a real break happened.

```mermaid
flowchart TD
    IN["5-min mseed file<br/>(1 to 1200+ pieces)"] --> Q{"How many samples<br/>are in the file?"}

    Q -->|"more than 5 min holds"| C["<b>CASE C — TOO MUCH DATA</b><br/>Should not happen"]
    Q -->|"a full 5 min"| A["<b>CASE A — COMPLETE</b><br/>Nothing is missing, so every<br/>apparent gap is a bad timestamp"]
    Q -->|"less than 5 min"| B["<b>CASE B — RECORDING MISSING</b><br/>Instrument stopped, or<br/>data was diverted"]

    C --> KEEP["Rejoin every piece into one file<br/>on its 5-minute startpoint"]
    A --> KEEP
    C -.->|log| FLAG(["ISSUE logged<br/>for review"])

    B --> WHERE{"Which gaps are real?<br/>longer than --gap-threshold AND the<br/>file's own measured label noise"}
    WHERE --> SPLIT["Split at each real break.<br/>Every surviving stretch is written,<br/>named from its own start time"]
    SPLIT --> CHECK{"Do those breaks account for<br/>all the missing recording?"}
    CHECK -->|yes| OUT(["FLAC written"])
    CHECK -->|"no"| FLAG

    KEEP --> OUT
    FLAG --> OUT
```

Nothing is discarded: a file that is short still yields audio, and any missing time is
reported rather than silently dropped. A gap counts as a real break only if it is longer
than `--gap-threshold` (OEK `TrHld2`, default 0.02 s) **and** longer than the file's own
measured label noise - overlaps can only ever be label error, so the largest overlap in a
file calibrates how big that file's timestamp lies are (observed 10-250 ms depending on
instrument and era).

What each case looks like on the clock. Every `|` is a boundary where the timestamps claim
one piece ends and the next begins - those boundaries are wrong, and the recording across
them is continuous.

```
                    0:00                                              5:00
                    |                                                    |

CASE A  COMPLETE    [==|==|==|==|==|==|==|==|==|==|==|==|==|=============]   pieces arrive mislabelled
                    [====================================================]   -> 1 file, the whole 5 min
                    19,200,000 samples = a full 5 min, so nothing is missing

CASE B  MISSING     [====|====|====|=====]          [====|====|====|=====]   a real break
                                          ^^^^^^^^^^  3 s of recording genuinely gone
                    [====================]          [====================]   -> 2 files, named from their own start
                    short by exactly 3 s, and that one break accounts for all of it

CASE C  TOO MUCH    [==|==|==|==|==|==|==|==|==|==|==|==|==|=================]
                                                                         ^^^^  more than 5 min holds
                    kept in full, but flagged for review
```

### File naming and start times

Files are named `{instrument}_{YYYYMMDD}_{HHMMSS}` at whole-second resolution, because that
is the finest mbari-pbp can parse - `meta_gen/utils.py` matches `{prefix}_YYYYMMDD_HHMMSS.`
and nothing after, so any sub-second suffix makes the file invisible to the spectrogram
stage. A recording that resumes mid-second therefore loses up to 1 s in its name; the exact
start is written into the file header instead (`date` and `comment`, readable in both FLAC
and WAV), where nothing truncates it.

### FLAC bit depth

OOI mseed carries 24-bit ADC counts right-justified in int32 (the integer *is* the count,
full scale 2^23); libsndfile's int32 API is left-justified (full scale 2^31). Writing counts
straight to `PCM_24` therefore stored `count >> 8`. Since v1.7 the writer shifts left by 8
first so the true count lands in the 24-bit word.

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

Cal files live in `metadata/cals` as netCDF, one per instrument per deployment, named `{refdes}_{deployment}.nc`, holding the manufacturer sheet values in dB re 1 V/uPa. pbp reads the `sensitivity` variable (the 0/90 average for directional cals; `sensitivity_0`/`sensitivity_90` are kept as the archival record).

The volts-to-counts conversion happens in code, not in the cal files: stock mbari-pbp reads the 24-bit FLAC as float normalized to full scale, and `audio_to_spec.py` sets `VOLTAGE_MULTIPLIER = 3` (the ADC's 3 V full scale), so sheet sensitivities apply unmodified. This replaces the retired `rca_correction_cals` copies carrying a +128.9 dB offset, which paired with an int32-reading pbp fork. The two paths are bit-identical apart from 0.031 dB, the rounding in that old constant (exact: 20log10(2^23/3) = 128.931).

### Calibration yamls are the source of truth

 The reviewable source is one YAML spec per instrument in `metadata/cal_specs/`, holding every deployment (replacing `notebooks/03_PARSE_CAL_TO_NC.ipynb`):

```
cal-to-nc template > metadata/cal_specs/{refdes}.yaml   # scaffold, fill in from the PDF
cal-to-nc build metadata/cal_specs/*.yaml --plot        # write the .nc plus a QA plot
cal-to-nc check metadata/cal_specs/*.yaml               # CI: committed .nc still match specs
cal-to-nc from-nc {refdes}                              # backfill a spec from existing .nc
```

Add a deployment by appending a block under `deployments` and re-running `build`, which warns if mean sensitivity jumps more than 6 dB between consecutive deployments. Specs take either a single `sens` or directional `sens0`/`sens90` (averaged into `sensitivity`). Frequencies default to kHz; set `freq_units: Hz` otherwise. Validation rejects length mismatches, non-ascending frequencies, and values outside -220 to -120 dB.

Transcribing the PDF to yaml is the manual step.

### Where the PDFs come from

https://github.com/OOI-CabledArray/calibrationFiles/tree/master/HYDBBA — named `{asset_id}__{YYYYMMDD}.pdf`, matching the `asset_id` and `cal_date` in each spec. OOI asset management also has a `calibration/HYDBBA` directory, but the sensitivity tables exist only in these PDFs.

### How a cal file is selected

`find_cal_file()` in [audio_to_spec.py](ooi_hyd_tools/audio_to_spec.py) reads the deployment table live from OOI asset management:

```
https://raw.githubusercontent.com/oceanobservatories/asset-management/refs/heads/master/deployment/{node}_Deploy.csv
```

`{node}` is the first 8 characters of the refdes. The deployment whose window contains the date picks `cals/{refdes}_{deployment}.nc`; a missing file raises rather than silently producing uncalibrated output. That CSV's `sensor.uid` column also gives which physical asset was deployed, which is how a deployment maps to a cal sheet. `--apply-cals false` skips calibration.

Built files carry `source_spec`, `source_pdf`, `sensitivity_units`, and `placeholder` when the cal is a stand-in — `find_cal_file` logs a run-time warning on placeholders.

### Known issues to fix

Ordered by size of the error on delivered spectrograms.

| issue | where | detail |
|---|---|---|
| Reprocess | `HYDBBA302` 2016-07-12 to 2017-07-31 | deployment 3 had been calibrated with a 2018 sheet postdating a hardware rebuild. Cal is now correct; products from that window are **-4.68 dB off on average, up to 8.65 dB at 190.7 kHz** |
| Reprocess | `HYDBBA102` 2016-07-17 to 2017-07-29 | deployment 3 had been calibrated with deployment 2's hydrophone. Cal is now correct; products from that window carry the old one, **-2.10 dB off on average, up to 5.40 dB at 90.4 kHz** |
| Cal gap | all instruments, 10 Hz - 10 kHz | sheet tables start at 10 kHz, so specs anchor 0 Hz to the 10 kHz value and everything below is a flat extrapolation. The certificates separately print a low-frequency spot value (e.g. -170.1 dB @ 26 Hz vs -171.05 @ 10 kHz on `ATOSU-58324-00015__20231213`), implying **~1 dB error across the band carrying most of the energy**. Unaddressed; affects all data equally, so it biases absolute levels but not trends |
| Untracked | all instruments | cal sheets state a **preamp gain** (36 dB on the sheets seen) that is baked into the quoted sensitivity, but the specs have no field for it. A deployment calibrated or operated at a different gain would silently break the counts-per-volt chain |
| Step change | all instruments, >12 kHz | the v1.7 bit-depth fix removed a **+0.96 dB bias at the quiet end of 20-27 kHz** (+0.45 at 12-20 kHz, nothing below 12 kHz). Within natural variability for a single measurement - the 20-27 kHz L05 spreads 7.6 dB within one day - but it is a *step* in a multi-year record. Note the reprocess date per instrument so trend work can account for it |
| Placeholder | `HYDBBA105` dep 10 | most recent available cal; the correct one is not yet published upstream |
| No cal | `HYDBBA303` deps 4-9 (2017-07-31 to 2024-08-11), `HYDBBA103` deps 1-11 | No calibration transcribed, so `--flag viz`/`all` raises `FileNotFoundError` rather than producing uncalibrated spectrograms - roughly seven years of HYDBBA303. The assignments exist in `OOI-CabledArray/deployments`, so this is transcription work. **Deprioritised**: both instruments are mooring-mounted |
| Degenerate timestamps | `HYDBBA102` 2023-06-15, extent unknown | Every trace in a file carries one **identical** timestamp - 383 traces all claiming `00:01:54.083000` - so the labels span 0.25 s while the file holds 95.8 s of audio. That single value sits anywhere from 0 to 300 s after the file's nominal start, so naming a file from its first trace (OEK §9) scatters output across the day and **fragments the spectrogram**, even though the archive's own filenames are clean 5-minute boundaries. Audio itself is written correctly and completely; only placement is wrong. A sound detector exists - labels cannot span less wall-clock time than the audio they contain (10 of 11 sampled files fail it) - and the fix is to name from the filename and skip gap-splitting when it trips. Not yet implemented, and **not yet surveyed across other instruments or eras** |

Re-run `cal-to-nc build` after editing a spec. Calibrations exist for HYDBBA105, HYDBBA106, HYDBBA302 and HYDBBA303 since program inception; moorings since 2025.

# OOI reference designators (refdes) for broadband hydrophones and approximate lat/lon:

`"CE02SHBP-LJ01D-11-HYDBBA106": (44.63721, -124.30564), "Oregon Shelf"`

`"CE04OSBP-LJ01C-11-HYDBBA105": (44.36933, -124.95347), "Oregon Offshore"`

`"RS01SBPS-PC01A-08-HYDBBA103": (44.51516, -125.3899), "Slope Base Platform"`

`"RS01SLBS-LJ01A-09-HYDBBA102": (44.51505, -125.39002), "Slope Base Seafloor"`

`"RS03AXBS-LJ03A-09-HYDBBA302": (45.81676, -129.75426), "Axial Base Seafloor"`

`"RS03AXPS-PC03A-08-HYDBBA303": (45.81671, -129.75405), "Axial Base Platform"`


Interactive map of assets at https://app.interactiveoceans.washington.edu/map

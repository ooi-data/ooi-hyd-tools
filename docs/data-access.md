# Accessing RCA broadband hydrophone FLAC on S3

## Where the data is

```
s3://ooi-hmb-data/flac/{YYYY}/{INSTRUMENT}/{YYYY_MM_DD}/
    {INSTRUMENT}_{YYYYMMDD}_{HHMMSS}.flac        5-minute files, 288 per full day
    {INSTRUMENT}_{YYYYMMDD}_manifest.json        one per day, written last
```

Hybrid millidecade spectrogram products for the same days, if useful as a reference:

```
s3://ooi-hmb-data/hmb/{YYYY}/{INSTRUMENT}/{INSTRUMENT}_{YYYYMMDD}.nc
```


```bash
aws s3 sync s3://ooi-hmb-data/flac/2024/HYDBBA105/2024_11_03/ ./local_dir/
aws s3 ls   s3://ooi-hmb-data/flac/2024/HYDBBA105/          # list available days
```

## Audio format

| property | value |
| --- | --- |
| container | FLAC, lossless |
| bit depth | 24-bit signed PCM |
| sample rate | 64 kHz (Nyquist 32 kHz) |
| channels | 1 |
| nominal length | 300 s = 19,200,000 samples |

The 24-bit sample values **are the raw ADC counts**, full scale +/-2^23 corresponding to
+/-3 V at the ADC input.

## Reading it

```python
import soundfile as sf

x, sr = sf.read("HYDBBA105_20241103_000000.flac")   # float64, 1.0 == full scale
volts = x * 3                                        # ADC full scale is +/-3 V
```

Then apply the hydrophone sensitivity in dB re 1 V/uPa to get pressure. Calibration files
are committed in the repository at `metadata/cals/{refdes}_{deployment}.nc`, one per
instrument per deployment, holding the manufacturer sheet values.

If you read with `dtype="int32"` instead of float, libsndfile left-justifies into the 32-bit
word and you get `count << 8`. Shift right by 8 to recover counts. The float path above
avoids this entirely.

## Timing 

**Filenames carry whole seconds only.** The exact start, including sub-second, is in the file
header and in the manifest.

```python
with sf.SoundFile("HYDBBA105_20241103_000000.flac") as f:
    print(f.date)      # e.g. 2024-11-03T00:00:00.014000Z  - authoritative
    print(f.comment)   # refdes=... start=... npts=... sampling_rate=... counts=...
```

The manifest's `files` array gives the same information for a whole day.

**Files are not always on a 5-minute grid.** When a recording resumes mid-window, the file is
named from its true start, so you will see names like `..._030615.flac`.

## Coverage varies by day 

Not every day is complete. Coverage across a delivered month ranges from roughly 50% to 100% due to naval data diversion. The manifest reports
this per day:

```json
{
  "refdes": "CE04OSBP-LJ01C-11-HYDBBA105",
  "date": "2024-11-03",
  "written_by": "ooi-hyd-tools 1.7.1",
  "counts": "int24_left_justified",
  "sampling_rate": 64000,
  "source_mseed_files": 288,
  "files_written": 288,
  "seconds_written": 86400.0,
  "day_coverage_pct": 100.0,
  "collisions": [],
  "files": [{"name": "...", "start": "...", "npts": 19200000}]
}
```

- **The manifest is written after all audio for that day**, so its presence means the day
  finished uploading. A day prefix without one is incomplete.

## Current delivery

```
CE04OSBP-LJ01C-11-HYDBBA105    Oregon Offshore, 600 m     2024-11-01 .. 2024-12-01
CE02SHBP-LJ01D-11-HYDBBA106    Oregon Shelf,     80 m     2024-11-01 .. 2024-12-01
62 instrument-days, 17,857 FLAC files
```

## Questions

Open an issue at <https://github.com/ooi-data/ooi-hyd-tools>.

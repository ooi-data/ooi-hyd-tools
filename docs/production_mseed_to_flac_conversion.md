# Production mseed to FLAC conversion

`mseed-to-flac` converts each OOI broadband hydrophone mseed file to FLAC as soon as the file
is closed, and writes the FLAC next to it on the raw data server. It runs the same timing and
gap repair as the RCA day pipeline, so its output is identical to what we produce today,
just minutes after recording instead of a day at a time.

That gives every user, Orcasound's live stream included, a standard audio file per 5 minutes,
with no repair or mseed handling on their side.

## Install

Python 3.10 or 3.11. The base install pulls only obspy, numpy, soundfile, fsspec, tqdm, loguru
and click (about 250 MB, mostly obspy's own dependencies), with none of the spectrogram stack.

```
pip install "ooi-hyd-tools @ git+https://github.com/ooi-data/ooi-hyd-tools@<tag>"
```

## Run

```
mseed-to-flac --refdes CE02SHBP-LJ01D-11-HYDBBA106 \
    /files/CE02SHBP/LJ01D/HYDBBA106/2026/11/14/OO-HYEA2--YDH-2026-11-14T07:30:00.000000Z.mseed
```

- **Output:** a FLAC in the same folder as the mseed, `addendum/` included. Takes several
  files per call.
- **`--refdes`:** the full refdes. It goes in the FLAC header, and the port number (`-11-`)
  isn't in the server path.
- **Naming:** the server's naming with `.flac`, timed from the data's first sample to the
  microsecond.
  - A complete file keeps its mseed's name.
  - A file whose data starts late (common in addenda) is named from where the data begins,
    e.g. `...T11:33:05.020000Z.flac` from a `...T11:30:00` mseed.
  - A real break in recording gives one FLAC per unbroken stretch.
- **Atomic writes:** each FLAC is written as `<name>.flac.part` and renamed when complete.
- **Reruns** overwrite the same names.
- **Exit status:** 1 if any input couldn't be read, so the caller can retry it.
  - Partial files log `ISSUE ... unaccounted for` warnings but still convert. They're expected
    at the edges of data withholding.
- **Cost:** about 0.6 s and 400 MB of memory per 5-minute file on a laptop.
- **FLAC contents:** raw 24-bit counts at the recorded rate, the same science data as the
  mseed, losslessly compressed. The header holds the refdes, exact start, sample count, sample
  rate and converter version.
- **Size:** about two-thirds of the mseed. One Oregon Shelf file was 26 MB of FLAC against
  39 MB of mseed. That's roughly 7.5 GB a day, or 2.7 TB a year, per instrument.

## Daily products (proposed, not yet built)

Once a day's files have all landed, a second job would write:

- the per-day manifest: coverage, file list, exact starts;
- the hybrid millidecade spectrogram (`.nc`, plus `.png`).

This job is heavier: it needs the `[pipeline]` install (pbp, xarray, netCDF) and the
calibration files in `metadata/`. It would ship as a container rather than a pip install. It
runs once per instrument per day, so it can sit outside the ingest path.

Three things have to be settled first:

- **When a day is complete.** Withheld data arrives in `addendum/` a median of 2.3 days later
  and up to about 7 days later (Sep–Nov 2026), so a day may need a first run at day end and a
  second after the addendum window.
- **pbp's naming.** pbp only reads `INSTRUMENT_YYYYMMDD_HHMMSS` names in a single folder, so
  the job has to stage server-named FLAC under those names, by links or a temporary copy.
- **Network access.** The calibration lookup reads the deployment table live from OOI asset
  management.

## Open questions for CI

1. **Trigger:** how does ingest know a 5-minute file is closed? Can it run a command at that
   point, or would watching a folder for new mseed be easier?
2. **Addendum:** what writes `addendum/` files, and can the same trigger fire when each one is
   complete? Is an addendum file ever rewritten after it first appears?
3. **Runtime:** a Python 3.10/3.11 virtualenv on the ingest host, or a container? How should
   upgrades be rolled out? Pin a release tag and bump it deliberately?
4. **Storage:** is about +65% on broadband hydrophone storage (2.7 TB a year per instrument)
   acceptable? Which instruments: all six broadband, or the seafloor four first?
5. **Temporary files:** `.flac.part` sits in the served folder for about a second while each
   FLAC is written. Is that acceptable, or should it be written elsewhere and moved in?
6. **Failures:** retry policy for exit 1, and where logs go. Who is alerted?
7. **Backfill:** should we convert the existing archive too? If a later version changes the
   repair, do we regenerate affected files? The version in each header identifies them. A
   regenerated file can occasionally split or name differently, leaving the old FLAC to
   clean up.
8. **Serving:** does the raw data server need configuration to serve `.flac` (content type,
   directory listings)?
9. **Daily products:** should CI run them, where should the `.nc`/`.png` and manifest live
   (next to the FLAC, or a separate products tree), and what counts as a complete day?

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
  - Stretches under 1 s aren't written: ms-long stubs where CI split a window into many
    files. Each is logged as an `ISSUE`. A file that is all stub writes nothing and still
    exits 0.
- **Atomic writes:** each FLAC is written as `<name>.flac.part` and renamed when complete.
- **Reruns** overwrite the same names.
- **Exit status:** 1 if any input couldn't be read, so the caller can retry it.
  - Partial files log `ISSUE ... unaccounted for` warnings but still convert. They're expected
    at the edges of data withholding.
- **Cost:** about 0.6 s and 400 MB of memory per 5-minute file on a laptop.
- **FLAC contents:** raw 24-bit counts at the recorded rate, the same science data as the
  mseed, losslessly compressed. The header holds the refdes, exact start, sample count, sample
  rate and converter version, plus `timing=approximate` where the start is right but later
  samples may be placed early (2023-06-07 to 2023-10-05 data only).
- **Size:** about two-thirds of the mseed. One Oregon Shelf file was 26 MB of FLAC against
  39 MB of mseed. That's roughly 7.5 GB a day, or 2.7 TB a year, per instrument.

## Daily products

`day-rollup` rolls up each day, main folder and `addendum/` together, into three files next
to the FLAC and mseed:

- **`HYDBBA106_20261103_manifest.json`:** coverage, the file list with exact starts, and what
  was built.
- **`HYDBBA106_20261103.nc`:** the calibrated hybrid millidecade spectrogram.
- **`HYDBBA106_20261103.png`:** its summary plot.

It runs from RCA's container image, which carries pbp, the netCDF stack and the cal files:

```
docker run --rm --user "$(id -u):$(id -g)" -v /files:/files \
    public.ecr.aws/p0l4c7i2/ooi-hyd-tools:<tag> \
    day-rollup --root /files \
        --refdes CE02SHBP-LJ01D-11-HYDBBA106 --refdes CE04OSBP-LJ01C-11-HYDBBA105
```

- **Schedule:** run it on a timer, e.g. hourly. Each run checks the 14 days before today
  (UTC). Addenda arrived up to 6.7 days late in Sep–Nov 2026. Today is left for later runs,
  since it's still landing.
- **What gets rebuilt:** only days whose inputs changed: the FLAC present, the instrument's cal
  files and netCDF attributes, or the software version. An unchanged day costs a folder
  listing.
- **Older days:** `--start-date` and `--end-date` rebuild a fixed range, for backfill.
- **Cal changes:** RCA publishes the image and its cal files; CI owns the schedule and
  storage. A cal fix reaches the products when CI moves to the new tag. Days in the window
  rebuild by themselves; older days need a backfill.
- **Same code as the RCA pipeline:** the same FLAC give an identical `.nc`.
- **Atomic writes:** outputs are written as `.part` and renamed when complete.
- **Failures:** a day whose spectrogram fails still gets its manifest, with the error under
  `products`. The run exits 1, and the day is retried on every run until it builds. An earlier
  `.nc` stays until it's replaced, so check `products` in the manifest.
- **Network:** needs outbound HTTPS. The cal lookup reads OOI asset management's deployment
  table from GitHub.
- **File ownership:** `--user` keeps the outputs from being owned by root.
- **Cost:** an hour of audio took 14 s on a laptop. A full day hasn't been timed.

The manifest keeps the RCA pipeline's fields (`check-flac --flac-layout ooi` reads it) and
adds `generated`, `fingerprint` and `products`. `collisions` lists pieces the spectrogram left
out because another piece started in the same second.

## Open questions for CI

1. **Trigger:** how does ingest know a 5-minute file is closed? Can it run a command at that
   point, or would watching a folder for new mseed be easier?
2. **Addendum:** what writes `addendum/` files, and can the same trigger fire when each one is
   complete? Is an addendum file ever rewritten after it first appears?
3. **Runtime:** a Python 3.10/3.11 virtualenv on the ingest host, or a container? How should
   upgrades be rolled out? Pin a release tag and bump it deliberately?
4. **Storage:** is about +65% on broadband hydrophone storage (2.7 TB a year per instrument)
   acceptable? Which instruments: all six broadband, or the seafloor four first?
5. **Temporary files:** `.part` files sit in the served folder while each FLAC or daily
   product is written, for about a second. Is that acceptable, or should it be written elsewhere and moved in?
6. **Failures:** retry policy for exit 1, and where logs go. Who is alerted?
7. **Backfill:** should we convert the existing archive too? If a later version changes the
   repair, do we regenerate affected files? The version in each header identifies them. A
   regenerated file can occasionally split or name differently, leaving the old FLAC to
   clean up.
8. **Serving:** does the raw data server need configuration to serve `.flac` (content type,
   directory listings)?
9. **Daily products:** can CI run the container on a timer, with `/files` mounted read-write
   and outbound HTTPS? How often, and as which user?

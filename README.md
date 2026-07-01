# ooi-hyd-tools 

Assorted tools for processing Ocean Observatories Initiative hydrophone data. (Just broadband for now), this repo is mostly intended for internal use and acoustic data QA/QC. 
For a more comprehensive suite of OOI hydrophone tools see:

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
Run with `--flag all` to generate MBARI-style hybrid millidecade spectrograms.
`acoustic-pipeline --help` To learn more about each argument. 

Data for the audio stage of the pipeline is output to `./data` dir. Millidecade spectrogram plots are output to `./output` dir.

# How to extract audio of a single event

Use the `event-audio` command to pulla a time window of broadband audio, run it through the same jitter/gap repair as the pipeline, and write a single continuous WAV to `./output/events` for listening. Handy for grabbing a nearby earthquake T-phase off a spectrogram.

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

`--speed` rewrites the sample rate `--bandpass LOW HIGH` isolates event of interest, `--normalize` boosts a quiet clip, and `--fade SEC` tapers both ends so it doesn't start/end on a click. `event-audio --help` for all arguments.

# OOI reference designators (refdes) for broadband hydrophones and approximate lat/lon:

`"CE02SHBP-LJ01D-11-HYDBBA106": (44.63721, -124.30564), "Oregon Shelf"`

`"CE04OSBP-LJ01C-11-HYDBBA105": (44.36933, -124.95347), "Oregon Offshore"`

`"RS01SBPS-PC01A-08-HYDBBA103": (44.51516, -125.3899), "Slope Base Platform"`

`"RS01SLBS-LJ01A-09-HYDBBA102": (44.51505, -125.39002), "Slope Base Seafloor"`

`"RS03AXBS-LJ03A-09-HYDBBA302": (45.81676, -129.75426), "Axial Base Seafloor"`

`"RS03AXPS-PC03A-08-HYDBBA303": (45.81671, -129.75405), "Axial Base Platform"`


Interactive map of assets at https://app.interactiveoceans.washington.edu/map

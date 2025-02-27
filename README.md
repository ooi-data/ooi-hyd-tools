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
`conda create -n ooi_hyd_tools python=3.11 pip`
`cd ooi-hyd-tools`
`pip install .`

```
mseed-to-audio --hyd-refdes "CE04OSBP-LJ01C-11-HYDBBA105" \
--date "2025/02/20" \
--sr 64000 \
--format PCM_24 \
--normalize-traces false \
--fudge-factor 0.02 \
--write-wav false
```
Adjust arguments as needed
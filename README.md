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

Now you can run the `mseed-to-audio` command to convert a day of archived ooi mseed to a day of 5 minute audio files.

```
mseed-to-audio --hyd-refdes "CE04OSBP-LJ01C-11-HYDBBA105" \
--date "2025/02/20" \
--sr 64000 \
--format PCM_24 \
--normalize-traces false \
--fudge-factor 0.02 \
--write-wav false \
--stages audio \
--apply-cals false
```
Adjust arguments as needed

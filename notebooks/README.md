# ooi-hyd-tools
Assorted tools for processing Ocean Observatories Initiative hydrophone data.

# WAV_processing_forOceanSonics.ipynb

This notebook converts OOI hydrophone data stored as mseed files on the OOI raw data archive 
into 5 minute wav files using obspy and soundfile. Wav file names are written to "./acoustic/wav/YYYY_MM_DD".
Files are named in the datetime format "YYMMDDHHMMSS"
The user can set the following processing parameters: 

HYD_REFDES

    The OOI reference designator for the hydrophone you want to process. For example, 
    "CE04OSBP-LJ01C-11-HYDBBA105" is the OOI hydrophone at the Oregon Offshore (600m) site. 
    "CE04OSBP-LJ01C-11-HYDBBA110" is the co-located Ocean Sonics test hydrophone at that same site.
DATE

    The day of hydrophone data you would like to convert to wav in the date format
    YYYY/MM/DD.
FILL_VALUE

    The value obspy will use to fill any gaps within an mseed file greater than 0.02 seconds. (edge case).
    See obspy docs: https://docs.obspy.org/packages/autogen/obspy.core.stream.Stream.merge.html
METHOD

    The method obspy will use to handle data "traces" that have an overlap greater that 0.02 seconds. (edge case).
    https://docs.obspy.org/packages/autogen/obspy.core.stream.Stream.html#obspy.core.stream.Stream._cleanup
SR

    Sample rate you wish to use when saving wav files. OOI Hydrophone sampling rate is 64000 Hz.
WAV_DATA_SUBTYPE

    'PCM_32' or 'FLOAT' The data subtype format for the resulting WAV files. OOI data is int32, 
     but some media players cannot import in this format. See `sf.available_subtypes('WAV')`
NORMALIZE_TRACES

    Option to normalize signal by mean of each 5 minute trace. If normalized float32 data type is needed.

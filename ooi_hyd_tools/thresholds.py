"""OEK gap-handling thresholds (HYDBB flow chart) and the spectrogram frequency limits.

Kept import-free so the prefect dispatcher can read the CLI defaults without loading the
science stack.
"""

# How far short of a full 5 minutes the sample count may fall and still count as complete.
# CI's trace packing often leaves a partial burst at one end, so files are not always exactly
# 19,200,000;
# (OEK TrHld1)
COUNT_THRESHOLD = 0.01

# How long a gap must be before it means recording was genuinely lost rather than
# mislabelled. Only consulted once the sample count says something is missing.
# (OEK TrHld2; the default for --gap-threshold)
GAP_THRESHOLD = 0.023

# Repaired stretches shorter than this aren't written as FLAC. Where CI split a window into
# many files, ms-long stubs (often 64 samples) land as their own pieces; each drop is logged
# and listed in the day's manifest.
MIN_FILE_SECONDS = 1.0

# A file whose trace labels span less than this fraction of its audio has one timestamp on
# every packet (all instruments, 2023-06-07 to 2023-10-05). The label dates the first packet;
# a partial file's internal gaps can't be located, so its FLAC is flagged timing=approximate.
LABEL_SPAN_FRACTION = 0.5

# Hybrid millidecade spectrogram band, Hz (min, max); the default for --freq-lims
FREQ_LIMS = (10, 30_000)

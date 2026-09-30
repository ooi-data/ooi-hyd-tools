"""OEK gap-handling thresholds (HYDBB flow chart).

Kept import-free so the prefect dispatcher can read the --gap-threshold default without
loading the science stack.
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

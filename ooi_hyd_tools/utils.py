import os

# How long a gap must be before it means recording was genuinely lost rather than
# mislabelled. Only consulted once the sample count says something is missing.
# (OEK TrHld2; the default for --gap-threshold)
GAP_THRESHOLD = 0.023


def select_logger():
    from prefect import get_run_logger

    try:
        logger = get_run_logger()
    except Exception:
        from loguru import logger

    return logger


def get_s3_kwargs():
    aws_key = os.environ.get("AWS_KEY")
    aws_secret = os.environ.get("AWS_SECRET")

    s3_kwargs = {"key": aws_key, "secret": aws_secret}
    return s3_kwargs

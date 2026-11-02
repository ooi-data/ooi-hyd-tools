import logging
import os
from contextlib import contextmanager
from datetime import timedelta


def select_logger():
    try:  # the prefect import is inside so lightweight installs without prefect fall back too
        from prefect import get_run_logger

        logger = get_run_logger()
    except Exception:
        from loguru import logger

    return logger


@contextmanager
def log_to_file(path):
    """also write this run's Prefect logs to path, appending, with any traceback"""
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    prefect_logger = logging.getLogger("prefect")  # flow, task and thread run logs all land here
    prefect_logger.addHandler(handler)
    try:
        yield
    except Exception:
        select_logger().exception("run failed")
        raise
    finally:
        prefect_logger.removeHandler(handler)
        handler.close()


def iter_dates(start, end=None):
    current = start
    end = end or start
    while current <= end:
        yield current
        current += timedelta(days=1)


def flac_day_dir(root, refdes, day, layout="pbp"):
    """where a day of FLAC and its manifest live under root, a local path or s3:// URL.
    pbp: ROOT/YYYY_MM_DD/INSTRUMENT, or ROOT/YYYY/INSTRUMENT/YYYY_MM_DD on S3.
    ooi: ROOT/SITE/NODE/INSTRUMENT/YYYY/MM/DD, the raw data server's tree."""
    root = str(root).rstrip("/")
    if layout == "ooi":
        return f"{root}/{refdes[0:8]}/{refdes[9:14]}/{refdes[18:27]}/{day:%Y/%m/%d}"
    if root.startswith("s3://"):  # a year of one instrument is ~105k files, so a day level
        return f"{root}/{day:%Y}/{refdes[-9:]}/{day:%Y_%m_%d}"
    return f"{root}/{day:%Y_%m_%d}/{refdes[-9:]}"


def manifest_name(refdes, day):
    return f"{refdes[-9:]}_{day:%Y%m%d}_manifest.json"


def get_s3_kwargs():
    aws_key = os.environ.get("AWS_KEY")
    aws_secret = os.environ.get("AWS_SECRET")

    s3_kwargs = {"key": aws_key, "secret": aws_secret}
    return s3_kwargs

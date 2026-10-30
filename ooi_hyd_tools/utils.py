import logging
import os
from contextlib import contextmanager


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


def get_s3_kwargs():
    aws_key = os.environ.get("AWS_KEY")
    aws_secret = os.environ.get("AWS_SECRET")

    s3_kwargs = {"key": aws_key, "secret": aws_secret}
    return s3_kwargs

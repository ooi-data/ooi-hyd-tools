"""
Check that every day with mseed has a finished day of FLAC: a manifest, built from the mseed
there now, with every FLAC it lists on disk. Reads only listings and the small manifests, so
a year takes minutes, not a re-run.

The manifest is written last, so a killed run leaves none. A count that disagrees means
mseed arrived after the run (late addenda) or FLAC were added or lost since.
"""
import json
import os
from datetime import datetime

import click
import fsspec
from loguru import logger as loguru_logger

from ooi_hyd_tools.repair import HydrophoneDay
from ooi_hyd_tools.thresholds import GAP_THRESHOLD
from ooi_hyd_tools.utils import (
    flac_day_dir,
    get_s3_kwargs,
    iter_dates,
    manifest_name,
    select_logger,
)


def check_day(fs, refdes, day, flac_root, layout, mseed_root):
    """what's wrong with this day's FLAC, or None if it's complete or there's no mseed"""
    hyd = HydrophoneDay(refdes, f"{day:%Y/%m/%d}", GAP_THRESHOLD, mseed_root=mseed_root)
    mseed = len(hyd.mseed_urls or [])
    flac_dir = flac_day_dir(flac_root, refdes, day, layout)
    manifest = f"{flac_dir}/{manifest_name(refdes, day)}"
    if not fs.exists(manifest):
        return f"{mseed} mseed, no manifest" if mseed else None

    m = json.loads(fs.cat(manifest))
    flac = len(fs.glob(f"{flac_dir}/*.flac"))
    problems = []
    if m["source_mseed_files"] != mseed:
        problems.append(f"manifest built from {m['source_mseed_files']} mseed, {mseed} there now")
    if m["files_written"] != flac:
        problems.append(f"manifest lists {m['files_written']} flac, {flac} on disk")
    return "; ".join(problems) or None


def check_range(refdes, start, end=None, flac_root="./data/flac", layout="pbp", mseed_root=None):
    """log each incomplete day as an ISSUE and return them as {day: problem}"""
    logger = select_logger()
    s3 = str(flac_root).startswith("s3://")
    flac_root = str(flac_root) if s3 else os.path.expanduser(flac_root)
    fs = fsspec.filesystem("s3", **get_s3_kwargs()) if s3 else fsspec.filesystem("file")

    days = list(iter_dates(start, end))
    bad = {}
    loguru_logger.disable("ooi_hyd_tools.repair")  # two listing lines a day would bury the ISSUEs
    for day in days:
        problem = check_day(fs, refdes, day, flac_root, layout, mseed_root)
        if problem:
            bad[day] = problem
            logger.warning(f"ISSUE {refdes[-9:]} {day:%Y-%m-%d}: {problem}")
    loguru_logger.enable("ooi_hyd_tools.repair")
    logger.info(
        f"checked {len(days)} day(s) of {refdes[-9:]} in {flac_root}: {len(bad)} to re-run"
        + "".join(f"\n  {d:%Y/%m/%d}" for d in bad)
    )
    return bad


@click.command()
@click.option("--hyd-refdes", required=True, help="e.g. CE02SHBP-LJ01D-11-HYDBBA106")
@click.option("--start-date", required=True, help="YYYY/MM/DD")
@click.option("--end-date", default=None, help="YYYY/MM/DD; omit to check one day")
@click.option(
    "--flac-root",
    default="./data/flac",
    show_default=True,
    help="Local folder or S3 prefix, e.g. ~/hydbb_flac or s3://ooi-hmb-data/flac.",
)
@click.option(
    "--flac-layout",
    type=click.Choice(["pbp", "ooi"], case_sensitive=False),
    default="pbp",
    show_default=True,
)
@click.option(
    "--mseed-root",
    default=None,
    help="Compare against a local mirror (ROOT/<refdes>/YYYY/MM/DD/) instead of the server.",
)
def check_flac(hyd_refdes, start_date, end_date, flac_root, flac_layout, mseed_root):
    """List days whose FLAC is missing or incomplete; exits 1 if there are any."""
    start = datetime.strptime(start_date, "%Y/%m/%d")
    end = datetime.strptime(end_date, "%Y/%m/%d") if end_date else None
    if check_range(hyd_refdes, start, end, flac_root, flac_layout, mseed_root):
        raise SystemExit(1)


if __name__ == "__main__":
    check_flac()

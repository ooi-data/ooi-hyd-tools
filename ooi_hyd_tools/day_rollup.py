"""
Roll up each day of FLAC on the raw data server, addenda included, into daily products written
next to the FLAC and mseed:

    {INSTRUMENT}_{YYYYMMDD}_manifest.json   coverage, file list, exact starts, what was built
    {INSTRUMENT}_{YYYYMMDD}.nc              calibrated hybrid millidecade spectrogram
    {INSTRUMENT}_{YYYYMMDD}.png             its summary plot

Built to run on a schedule over a lookback window. Addenda land days after recording, so a
day is rebuilt whenever its inputs change: the FLAC present, the instrument's cal files and
netCDF attributes, or this software's version. A day whose manifest records the same inputs
is skipped, so a run that finds nothing new costs a listing per day.

    day-rollup --root /files --refdes CE02SHBP-LJ01D-11-HYDBBA106 --lookback-days 14

ROOT is the raw data server's tree (SITE/NODE/INSTRUMENT/YYYY/MM/DD/[addendum/]) with FLAC
from mseed-to-flac. pbp reads only {INSTRUMENT}_YYYYMMDD_HHMMSS names in one folder, so each
day's FLAC are linked under those names in a scratch folder.
"""
import hashlib
import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
from pathlib import Path

import click
import soundfile as sf
from loguru import logger as loguru_logger

from ooi_hyd_tools.audio_to_spec import META_DIR, spectrogram_day
from ooi_hyd_tools.mseed_to_flac import day_manifest, keep_longer, pbp_name
from ooi_hyd_tools.repair import own_files
from ooi_hyd_tools.thresholds import FREQ_LIMS
from ooi_hyd_tools.utils import flac_day_dir, iter_dates, manifest_name, select_logger


def day_flac(day_dir, refdes):
    """the instrument's FLAC for the day, addenda included, with the exact start from each header"""
    files = []
    for p in own_files(sorted(day_dir.glob("*.flac")) + sorted(day_dir.glob("addendum/*.flac")), refdes):
        with sf.SoundFile(str(p)) as f:
            files.append(
                {
                    "name": str(p.relative_to(day_dir)),
                    "start": f.date,  # exact, sub-second; see mseed_to_flac.write_audio
                    "npts": f.frames,
                    "sampling_rate": f.samplerate,
                    "timing": "approximate" if "timing=approximate" in f.comment else None,
                    "path": p,
                }
            )
    return sorted(files, key=lambda f: f["start"])


def fingerprint(files, refdes, meta_dir):
    """what a day's products were built from: the FLAC, this instrument's cals, the netCDF
    attributes and the software version. Changes when any of them do."""
    h = hashlib.sha256(version("ooi-hyd-tools").encode())
    for f in files:
        st = f["path"].stat()
        h.update(f"{f['name']} {st.st_size} {st.st_mtime_ns}".encode())
    for p in sorted(meta_dir.glob(f"cals/{refdes}_*.nc")) + sorted(meta_dir.glob("attributes/*.yaml")):
        h.update(p.name.encode() + p.read_bytes())
    return h.hexdigest()[:16]


def _publish(src, dst):
    """copy in under a temporary name and rename, so readers never see a partial file"""
    part = dst.with_name(f"{dst.name}.part")
    shutil.copyfile(src, part)
    part.replace(dst)


def roll_up_day(root, refdes, day, meta_dir=META_DIR, force=False):
    """write or update one day's manifest and spectrogram. Returns "built", "unchanged",
    "no flac" or "failed"."""
    logger = select_logger()
    meta_dir = Path(meta_dir)
    day_dir = Path(flac_day_dir(root, refdes, day, "ooi"))
    files = day_flac(day_dir, refdes)
    if not files:
        return "no flac"

    fp = fingerprint(files, refdes, meta_dir)
    manifest_path = day_dir / manifest_name(refdes, day)
    old = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    nc_name = f"{refdes[-9:]}_{day:%Y%m%d}.nc"
    if not force and old.get("fingerprint") == fp and (day_dir / nc_name).exists():
        return "unchanged"

    # pbp sees one name per second, so overlapping pieces from the day and addendum/ resolve
    # the same way as in the pipeline
    kept, collisions = keep_longer(files, key=lambda f: pbp_name(refdes, f["start"]))
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        stage = tmp / "flac"
        stage.mkdir()
        for name, f in kept.items():
            (stage / f"{name}.flac").symlink_to(f["path"].resolve())
        try:
            nc = spectrogram_day(
                f"{day:%Y%m%d}", stage, refdes, apply_cals=True, freq_lims=FREQ_LIMS,
                out_dir=tmp / "out", meta_dir=meta_dir, json_dir=tmp / "json", download_dir=tmp / "dl",
            )  # fmt: skip
            for p in (nc, nc.with_suffix(".png")):
                _publish(p, day_dir / p.name)
            products = {"nc": nc.name, "png": nc.with_suffix(".png").name}
        except Exception as e:  # the manifest still records coverage; next run retries
            products, fp = {"error": f"{type(e).__name__}: {e}"}, None
            error = e
        else:
            error = None
        finally:
            # pbp's logger setup removes every loguru handler, which would silence this run
            loguru_logger.remove()
            loguru_logger.add(sys.stderr)
    if error:
        logger.opt(exception=error).error(f"{refdes[-9:]} {day:%Y-%m-%d}: spectrogram failed")

    source_mseed = len(own_files(list(day_dir.glob("*.mseed")) + list(day_dir.glob("addendum/*.mseed")), refdes))
    manifest = day_manifest(refdes, day, files, source_mseed, collisions) | {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fingerprint": fp,
        "products": products,
    }
    part = manifest_path.with_name(f"{manifest_path.name}.part")
    part.write_text(json.dumps(manifest, indent=2))
    part.replace(manifest_path)
    logger.info(
        f"{refdes[-9:]} {day:%Y-%m-%d}: {len(files)} flac, {manifest['day_coverage_pct']}% of "
        f"the day; {products}"
    )
    return "failed" if fp is None else "built"


@click.command()
@click.option("--root", required=True, help="Raw data server tree holding mseed and FLAC, e.g. /files.")
@click.option("--refdes", required=True, multiple=True, help="e.g. CE02SHBP-LJ01D-11-HYDBBA106; repeat for several.")
@click.option("--lookback-days", default=14, show_default=True, help="Days before today (UTC) to check; addenda land up to ~7 days late.")
@click.option("--start-date", default=None, help="YYYY/MM/DD; with --end-date, a fixed range instead of the lookback.")
@click.option("--end-date", default=None, help="YYYY/MM/DD; omit for one day.")
@click.option("--meta-dir", default=str(META_DIR), show_default=True, envvar="OOI_HYD_META_DIR", help="Folder with cals/ and attributes/. Env: OOI_HYD_META_DIR.")
@click.option("--force", is_flag=True, help="Rebuild days whose inputs haven't changed.")
def cli(root, refdes, lookback_days, start_date, end_date, meta_dir, force):
    """Write or update each day's manifest, .nc spectrogram and .png next to its FLAC.
    Exits 1 if any day failed, so a scheduler can flag it."""
    logger = select_logger()
    if start_date:
        start = datetime.strptime(start_date, "%Y/%m/%d")
        days = list(iter_dates(start, datetime.strptime(end_date, "%Y/%m/%d") if end_date else start))
    else:  # today is still landing, so it waits for tomorrow's runs
        today = datetime.now(timezone.utc).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
        days = list(iter_dates(today - timedelta(days=lookback_days), today - timedelta(days=1)))

    results = {(r, d): roll_up_day(root, r, d, meta_dir, force) for r in refdes for d in days}
    counts = {s: sum(v == s for v in results.values()) for s in ("built", "unchanged", "no flac", "failed")}
    logger.info(f"{len(results)} day(s): {counts}")
    if counts["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    cli()

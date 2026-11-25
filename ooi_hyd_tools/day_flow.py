"""
The Prefect day flow behind acoustic-pipeline and the ECS deployment (prefect.yaml): one
instrument-day through whichever --flag stage was asked for, then the S3 sync.

The audio stage converts a day of mseed from the raw data archive into 5-minute FLAC, written
to FLAC_ROOT/YYYY_MM_DD/INSTRUMENT/ as INSTRUMENT_YYYYMMDD_HHMMSS.flac (FLAC_ROOT defaults to
./data/flac). Each file is repaired first (repair.py) and written with the shared writer, naming
and manifest in mseed_to_flac.py, which also converts single files at ingest.

flac_layout="ooi" instead mirrors the raw data server, for copying FLAC alongside the
mseed: FLAC_ROOT/SITE/NODE/INSTRUMENT/YYYY/MM/DD/[addendum/] with the server's file naming,
e.g. OO-HYEA2--YDH-2026-10-20T00:40:00.014000Z.flac. pbp can't read those names or the
addendum/ split, so the spectrogram stage needs the default layout; day_rollup.py builds
spectrograms from the ooi layout.
"""
import json

from datetime import datetime
from pathlib import Path
from prefect import task, flow
from importlib.metadata import distributions

from ooi_hyd_tools.audio_to_spec import audio_to_spec
from ooi_hyd_tools.low_freq import run_low_freq_oneday
from ooi_hyd_tools.cloud import sync_png_nc_to_s3
from ooi_hyd_tools.mseed_to_flac import (
    INT24_SHIFT,
    day_manifest,
    drop_stubs,
    keep_longer,
    ooi_name,
    pbp_name,
    write_audio,
)
from ooi_hyd_tools.repair import HydrophoneDay
from ooi_hyd_tools.utils import flac_day_dir, log_to_file, manifest_name, select_logger
from ooi_hyd_tools.seismometer import run_obs_viz


@task(retries=2, retry_delay_seconds=60)
def convert_mseed_to_audio(
    hyd_refdes,
    date,
    gap_threshold,
    mseed_root=None,
    flac_layout="pbp",
    flac_root=Path("data/flac"),
):
    logger = select_logger()
    hyd = HydrophoneDay(hyd_refdes, date, gap_threshold, mseed_root=mseed_root)

    hyd.read_and_repair_gaps()

    if hyd.clean_list is None:  # retun None if no data available on that day
        return None
    else:
        # make dirs
        logger.info("Creating data directories")
        flac_dir = Path(flac_day_dir(flac_root, hyd_refdes, hyd.date, flac_layout))
        flac_dir.mkdir(parents=True, exist_ok=True)

        # the ooi layout keeps addenda in addendum/ as the server does; pbp needs one folder
        pieces = [
            {
                "tr": tr,
                "sub": "addendum/" if flac_layout == "ooi" and "/addendum/" in url else "",
                "start": tr.stats.starttime,
                "npts": tr.stats.npts,
                "sampling_rate": int(tr.stats.sampling_rate),
                "timing": tr.stats.get("timing"),
            }
            for url, st in zip(hyd.mseed_urls, hyd.clean_list)
            if st is not None
            for tr in st  # normally one chunk; more when a real gap split the file
        ]
        pieces, stubs = drop_stubs(pieces)
        for pc in pieces:
            # ooi: the server's naming, to the microsecond, so pieces never share a name
            pc["name"] = (
                f"{pc['sub']}{ooi_name(pc['tr'])}.flac"
                if flac_layout == "ooi"
                else f"{pbp_name(hyd_refdes, pc['start'])}.flac"
            )
        # same start in the day and addendum/ isn't a clash, so the folder is part of the key
        kept, collisions = keep_longer(pieces, key=lambda pc: pc["name"])

        for pc in kept.values():
            flac_path = flac_dir / pc["name"]
            flac_path.parent.mkdir(exist_ok=True)
            logger.debug(str(flac_path))
            # counts are left-shifted so PCM_24 stores them intact, see INT24_SHIFT
            write_audio(
                flac_path,
                pc["tr"].data << INT24_SHIFT,
                pc["sampling_rate"],
                "PCM_24",
                hyd_refdes,
                pc["start"],
                pc["npts"],
                pc["timing"],
            )

        files = [  # sorted: clean_list is filled by a thread pool
            {k: str(pc[k]) if k == "start" else pc[k] for k in ("name", "start", "npts", "sampling_rate", "timing")}
            for _, pc in sorted(kept.items())
        ]
        manifest = day_manifest(
            hyd_refdes, hyd.date, files, len(hyd.mseed_urls), collisions, gap_threshold,
            sorted(stubs, key=lambda s: s["start"]),
        )
        (flac_dir / manifest_name(hyd_refdes, hyd.date)).write_text(json.dumps(manifest, indent=2))

        # what fraction of the day actually made it to disk
        logger.info(
            f"{hyd_refdes[-9:]} {date}: wrote {manifest['seconds_written'] / 3600:.2f}h of audio "
            f"from {len(hyd.mseed_urls)} mseed files ({manifest['day_coverage_pct']:.1f}% of the day)"
        )
        return hyd


def find_flac_day(hyd_refdes, date, flac_root):
    """The manifest for a day of FLAC already on disk, plus its folder, or None.

    Spectrograms are built from FLAC on disk, not from mseed, so this answers "is it
    there" up front rather than letting pbp fail on an empty metadata index.
    """
    day = datetime.strptime(date, "%Y/%m/%d")
    flac_dir = Path(flac_day_dir(flac_root, hyd_refdes, day))
    manifest = flac_dir / manifest_name(hyd_refdes, day)
    if not manifest.is_file():
        return None
    m = json.loads(manifest.read_text())
    m["on_disk"] = len(list(flac_dir.glob("*.flac")))
    m["dir"] = flac_dir
    return m


@flow(log_prints=True)
def acoustic_flow_oneday(
    hyd_refdes,
    date,
    gap_threshold,
    apply_cals,
    freq_lims,
    s3_sync,
    flag,
    obs_run_type,
    mseed_root=None,
    flac_layout="pbp",
    flac_root=None,
):
    flac_root = Path(flac_root).expanduser().resolve() if flac_root else Path.cwd() / "data/flac"
    # kept after the run, unlike Prefect's; appended, so reruns of a day stack up
    log_path = Path.cwd() / f"logs/{hyd_refdes[-9:]}/{hyd_refdes[-9:]}_{date.replace('/', '')}.log"
    with log_to_file(log_path):
        logger = select_logger()
        logger.info(
            f"run: flag={flag} mseed_root={mseed_root} flac_root={flac_root} "
            f"flac_layout={flac_layout} s3_sync={s3_sync}"
        )
        # log python package versions on cloud machine
        installed_packages = {dist.metadata["Name"]: dist.version for dist in distributions()}
        logger.info(f"Installed packages: {installed_packages}")

        if flag == "audio" or flag == "all":
            hyd = convert_mseed_to_audio(
                hyd_refdes=hyd_refdes,
                date=date,
                gap_threshold=gap_threshold,
                mseed_root=mseed_root,
                flac_layout=flac_layout,
                flac_root=flac_root,
            )
            if hyd is None:
                logger.warning(f"No data availale for {date}. Moving to next day.")
                return

            logger.info(f"first 5 elements of cleaned mseed list: {hyd.clean_list[:5]}")

        if flag == "spectrogram" or flag == "all":
            m = find_flac_day(hyd_refdes, date, flac_root)
            if m is None:
                raise FileNotFoundError(
                    f"no flac for {hyd_refdes} {date} - run --flag audio first"
                )
            if m["on_disk"] != m["files_written"]:
                # leftovers from an earlier run of this day; pbp indexes whatever is there
                logger.warning(f"{m['on_disk']} flac on disk, manifest says {m['files_written']}")
            logger.info(
                f"spectrogram from {m['files_written']} flac, {m['day_coverage_pct']}% of the "
                f"day, written by {m['written_by']}"
            )
            audio_to_spec(date, m["dir"], hyd_refdes, apply_cals, freq_lims)

        if flag == "low_freq":
            run_low_freq_oneday(hyd_refdes, date, logger)

        if flag == "obs":
            run_obs_viz(hyd_refdes, date, obs_run_type)

        if s3_sync:  # "spectrogram" (.nc + .png) or "all" (those plus flac)
            sync_png_nc_to_s3(hyd_refdes, date, flag, scope=s3_sync, flac_root=flac_root)


if __name__ == "__main__":
    acoustic_flow_oneday()

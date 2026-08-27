import concurrent.futures
from datetime import datetime
from pathlib import Path

import fsspec
from prefect import task

from ooi_hyd_tools.utils import get_s3_kwargs, select_logger

OOI_DATA_BUCKET = "s3://ooi-hmb-data"
OOI_VIZ_BUCKET = "s3://ooi-rca-qaqc-prod"
FLAC_UPLOAD_WORKERS = 8  # a day is ~288 files / ~6 GB, so serial is too slow


@task
def sync_png_nc_to_s3(hyd_refdes, date, flag, scope="spectrogram", local_dir=Path("./output")):
    """sync products to S3.

    scope="spectrogram" uploads the .nc and .png; scope="all" adds the FLAC, which lands
    under flac/YYYY/INSTRUMENT/ mirroring where the .nc goes (hmb/YYYY/INSTRUMENT/).
    """
    logger = select_logger()
    instrument = hyd_refdes[-9:]
    year = datetime.strptime(date, "%Y/%m/%d").year
    fs_kwargs = get_s3_kwargs()
    s3_fs = fsspec.filesystem("s3", **fs_kwargs)

    def is_valid_file(fp: Path):
        filename = fp.name

        return instrument in filename and str(year) in filename

    if "obs" not in flag:
        # Upload .nc files to hmb/YYYY/
        nc_files = local_dir.rglob("*.nc")
        for fp in nc_files:
            if fp.is_file() and is_valid_file(fp):
                s3_uri = f"{OOI_DATA_BUCKET}/hmb/{year}/{instrument}/{fp.name}"
                logger.info(f"Uploading {fp} to {s3_uri}")
                s3_fs.put(str(fp), s3_uri)

        # Upload .png files to spectrograms/YYYY/
        png_files = local_dir.glob("*HYD*.png")
        for fp in png_files:
            if fp.is_file():
                s3_uri = f"{OOI_VIZ_BUCKET}/spectrograms/{year}/{instrument}/{fp.name}"
                logger.info(f"Uploading {fp} to {s3_uri}")
                s3_fs.put(str(fp), s3_uri)

        if scope == "all":
            sync_flac_to_s3(hyd_refdes, date, s3_fs, logger)

    elif "obs" in flag:
        obs_png_files = local_dir.rglob("*OBS*.png")  # recursive glob for subdirs
        for fp in obs_png_files:
            if fp.is_file():
                s3_uri = f"{OOI_VIZ_BUCKET}/QAQC_plots/{hyd_refdes[:8]}/{fp.name}"
                logger.info(f"Uploading {fp} to {s3_uri}")
                s3_fs.put(str(fp), s3_uri)


def sync_flac_to_s3(hyd_refdes, date, s3_fs, logger):
    """upload a day of FLAC to flac/YYYY/INSTRUMENT/YYYY_MM_DD/.

    Follows the .nc layout (hmb/YYYY/INSTRUMENT/) but adds a day level: a year of one
    instrument is ~105k files, too many to sit in a single prefix comfortably.
    """
    instrument = hyd_refdes[-9:]
    year = datetime.strptime(date, "%Y/%m/%d").year
    day = date.replace("/", "_")
    flac_dir = Path.cwd() / f"data/flac/{day}/{instrument}"
    files = sorted(flac_dir.glob("*.flac"))
    if not files:
        logger.warning(f"no flac to sync in {flac_dir}")
        return

    prefix = f"{OOI_DATA_BUCKET}/flac/{year}/{instrument}/{day}"
    total_gb = sum(f.stat().st_size for f in files) / 1e9
    logger.info(f"Uploading {len(files)} flac ({total_gb:.1f} GB) to {prefix}/")

    def put(fp):
        s3_fs.put(str(fp), f"{prefix}/{fp.name}")

    with concurrent.futures.ThreadPoolExecutor(FLAC_UPLOAD_WORKERS) as ex:
        list(ex.map(put, files))
    logger.info(f"Uploaded {len(files)} flac to {prefix}/")

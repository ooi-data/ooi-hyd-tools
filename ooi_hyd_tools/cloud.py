import fsspec
from datetime import datetime
from pathlib import Path
from loguru import logger
from prefect import task

from ooi_hyd_tools.utils import select_logger

OOI_BUCKET = "s3://ooi-acoustic-data"


@task
def sync_png_nc_to_s3(hyd_refdes, date, local_dir=Path("./output")):
    """sync .nc and .png files to S3 based on the given date and refdes."""
    logger = select_logger()
    instrument = hyd_refdes[-9:]
    year = datetime.strptime(date, "%Y/%m/%d").year

    def is_valid_file(fp: Path):

        filename = fp.name

        return instrument in filename and str(year) in filename

    s3_fs = fsspec.filesystem('s3')

    # Upload .nc files to hmb/YYYY/
    nc_files = local_dir.rglob("*.nc")
    for fp in nc_files:
        if fp.is_file() and is_valid_file(fp):
            s3_uri = f"{OOI_BUCKET}/hmb/{year}/{instrument}/{fp.name}"
            logger.info(f"Uploading {fp} to {s3_uri}")
            s3_fs.put(str(fp), s3_uri)

    # Upload .png files to spectrograms/YYYY/
    png_files = local_dir.glob("*.png")
    for fp in png_files:
        if fp.is_file() and is_valid_file(fp):
            s3_uri = f"{OOI_BUCKET}/spectrograms/{year}/{instrument}/{fp.name}"
            logger.info(f"Uploading {fp} to {s3_uri}")
            s3_fs.put(str(fp), s3_uri)

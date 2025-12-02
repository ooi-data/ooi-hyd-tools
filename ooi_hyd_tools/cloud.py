import fsspec
from datetime import datetime
from pathlib import Path
from prefect import task

from ooi_hyd_tools.utils import select_logger, get_s3_kwargs

OOI_DATA_BUCKET = "s3://ooi-hmb-data"
OOI_VIZ_BUCKET = "s3://ooi-rca-qaqc-prod"


@task
def sync_png_nc_to_s3(hyd_refdes, date, flag, local_dir=Path("./output")):
    """sync .nc and .png files to S3 based on the given date and refdes."""
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
    
    elif "obs" in flag:
        
        obs_png_files = local_dir.rglob("*OBS*.png") # recursive glob for subdirs
        for fp in obs_png_files:
            if fp.is_file():
                s3_uri = f"{OOI_VIZ_BUCKET}/QAQC_plots/{hyd_refdes[:8]}/{fp.name}"
                logger.info(f"Uploading {fp} to {s3_uri}")
                s3_fs.put(str(fp), s3_uri)

from pbp.meta_gen.gen_iclisten import IcListenMetadataGenerator
from datetime import datetime, timezone
import xarray as xr
import polars as pl
import matplotlib.pyplot as plt

from pbp.simpleapi import HmbGen
from pbp.plotting import plot_dataset_summary
from pathlib import Path
from prefect import task

# TODO pbp gets angry if I don't pass these
import boto3
import botocore
from botocore.config import Config

from ooi_hyd_tools.utils import select_logger


plt.switch_backend("Agg")  # use non-interactive backend
# hydrophone specification
VOLTAGE_MULTIPLIER = 1  # TODO confirm with Orest
# NOTE OOI broadband hydrophone output is 24-bit ADC with maximum 3 volts.
# So, there are 8388608 / 3 = 2796202 counts per volt which is equivalent
# to 128.9 dB (=20log10(2796202)). This offset is applied to the cal files in rca_correction_cals
# so here we use a voltage multiplier of 1.
FREQ_LIMS = (
    10,
    30000,
)  # subset frequency band for output HMB spectra, recording @ 64 kHz
DB_RANGE = (45, 120)

# metadata files for output netCDF data products
GLOBAL_ATTRS_YAML = "./metadata/attributes/globalAttributes.yaml"  # TODO
VARIABLE_ATTRS_YAML = "./metadata/attributes/variableAttributes.yaml"  # TODO

HYDBB_COORDS = {
    "CE02SHBP-LJ01D-11-HYDBBA106": (44.63721, -124.30564),
    "CE04OSBP-LJ01C-11-HYDBBA105": (44.36933, -124.95347),
    "RS01SBPS-PC01A-08-HYDBBA103": (44.51516, -125.3899),
    "RS01SLBS-LJ01A-09-HYDBBA102": (44.51505, -125.39002),
    "RS03AXBS-LJ03A-09-HYDBBA302": (45.81676, -129.75426),
    "RS03AXPS-PC03A-08-HYDBBA303": (45.81671, -129.75405),
}


@task
def audio_to_spec(
    start_date,
    file_type,
    hyd_refdes,
    apply_cals=False,
):
    # pbp takes dates as strings without slashes
    start_date = start_date.replace("/", "")

    gen_metadata(start_date, file_type, hyd_refdes)

    gen_hybrid_millidecade_spectrogram(start_date, hyd_refdes, apply_cals)


@task
def gen_metadata(start_date, file_type, hyd_refdes):
    logger = select_logger()

    instrument = hyd_refdes[-9:]
    date_dir = f"{start_date[:4]}_{start_date[4:6]}_{start_date[6:]}"

    # Audio data input specifications
    flac_uri = f"file://{str(Path.cwd())}/data/{file_type}/{date_dir}/{instrument}"
    logger.warning(flac_uri)
    flac_prefix = instrument  # prefix for the audio files
    start_date = start_date  # start date for temporal metadata extraction (YYYYMMDD)
    json_base_dir = "metadata/json"  # location to store generated metadata in JSON format

    # Convert the start and end dates to datetime objects
    start = datetime.strptime(start_date, "%Y%m%d")
    end = datetime.strptime(
        start_date, "%Y%m%d"
    )  # we can run multipl days on .nc files if needed

    # Create the metadata generator
    meta_gen = IcListenMetadataGenerator(
        log=logger,
        uri=flac_uri,
        json_base_dir=json_base_dir,
        start=start,
        end=end,
        prefixes=[flac_prefix],
        seconds_per_file=300,
    )

    meta_gen.run()


@task
def find_cal_file(refdes, date_str):
    node = refdes[:8]
    current_utc_datetime = datetime.now(timezone.utc)

    date = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=timezone.utc)

    # load deployments from OOI asset management
    df = pl.read_csv(
        f"https://raw.githubusercontent.com/oceanobservatories/asset-management/refs/heads/master/deployment/{node}_Deploy.csv"
    )

    df = df.filter(pl.col("Reference Designator") == refdes)

    df = df.with_columns(
        pl.col("startDateTime")
        .str.strptime(pl.Datetime)
        .dt.replace_time_zone("UTC")
        .alias("startDateTime")
    )
    df = df.with_columns(
        pl.col("stopDateTime")
        .str.strptime(pl.Datetime)
        .dt.replace_time_zone("UTC")
        .alias("stopDateTime")
    )
    df = df.with_columns(
        pl.col("stopDateTime").fill_null(current_utc_datetime).alias("stopDateTime")
    )

    deploy_df = df.filter((pl.col("startDateTime") < date) & (pl.col("stopDateTime") > date))
    deployment_number = deploy_df["deploymentNumber"]

    cal_file_path_str = (
        f"./metadata/rca_correction_cals/{refdes}_{str(deployment_number[0])}.nc"
    )
    cal_file_path = Path(cal_file_path_str)

    if not cal_file_path.exists():
        raise FileNotFoundError(f"No calibration file found for {date_str}")

    print(f"{date_str} falls under deployment < {deployment_number[0]} > for {refdes}")
    print(f"cal file at {cal_file_path_str}")
    return cal_file_path_str  # pbp wants a string not a path


def gen_hybrid_millidecade_spectrogram(start_date, hyd_refdes, apply_cals=False):
    logger = select_logger()
    instrument = hyd_refdes[-9:]
    # set up directories
    download_dir = Path("./downloads")
    json_base_dir = Path("./metadata/json")
    output_dir = Path("./output")

    download_dir.mkdir(parents=True, exist_ok=True)
    json_base_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_prefix = f"{instrument}_"  # a prefix for the name of generate files

    # hybrid millidecade settings
    hmb_gen = HmbGen()

    hmb_gen.set_json_base_dir(str(json_base_dir))
    hmb_gen.set_global_attrs_uri(GLOBAL_ATTRS_YAML)
    hmb_gen.set_variable_attrs_uri(VARIABLE_ATTRS_YAML)
    hmb_gen.set_voltage_multiplier(VOLTAGE_MULTIPLIER)
    hmb_gen.set_subset_to(FREQ_LIMS)

    if apply_cals:  # TODO
        logger.warning("CALS ARE A EXPERIMENTAL FEATURE")
        sensitivity_uri = find_cal_file(hyd_refdes, start_date)
        # hmb_gen.set_sensitivity(-170)
        hmb_gen.set_sensitivity(sensitivity_uri)

    config = Config(signature_version=botocore.UNSIGNED)
    s3_client = boto3.client("s3", config=config)
    hmb_gen.set_s3_client(s3_client)

    hmb_gen.set_download_dir(str(download_dir))
    hmb_gen.set_output_dir(str(output_dir))
    hmb_gen.set_output_prefix(output_prefix)

    hmb_gen.set_print_downloading_lines(True)
    hmb_gen.set_retain_downloaded_files(True)
    hmb_gen.set_assume_downloaded_files(True)  # useful for running locally

    error = hmb_gen.check_parameters()
    # A message is returned in case of any errors
    if error:
        raise RuntimeError(f"check_parameters returned:\n{error}")

    # The resulting NetCDF file should have been saved under the output directory.
    result = hmb_gen.process_date(start_date)
    # sanity check
    logger.info(result.dataset)

    nc_filename = output_dir / f"{instrument}_{start_date}.nc"
    ds = xr.open_dataset(nc_filename, engine="h5netcdf")

    plot_dataset_summary(
        ds,
        lat_lon_for_solpos=HYDBB_COORDS[hyd_refdes],
        title=f"{hyd_refdes}, {HYDBB_COORDS[hyd_refdes][0]}°N, {HYDBB_COORDS[hyd_refdes][1]}°W",
        ylim=FREQ_LIMS,
        cmlim=DB_RANGE,
        jpeg_filename=f"{str(output_dir)}/{instrument}_{start_date}.png",
        show=False,
    )

from pbp.meta_gen.gen_iclisten import IcListenMetadataGenerator
from pbp.logging_helper import create_logger_info
from datetime import datetime
import xarray as xr

from pbp.simpleapi import HmbGen
from pbp import get_pbp_version
from pbp.plotting import plot_dataset_summary
from pathlib import Path
from prefect import task

# TODO pbp gets angry if I don't pass these
import boto3
import botocore
from botocore.config import Config

from ooi_hyd_tools.utils import select_logger


# hydrophone specification
# NOTE this are placeholder values borrowed from MBARI, OOI will need to get its own attributes and metadata into YAML and NC 
# this is in the works...
VOLTAGE_MULTIPLIER = 3 # TODO confirm with Orest
FREQ_LIMS = (10, 30000)   # subset frequency band for output HMB spectra, recording @ 64 kHz #TODO
DB_RANGE = (20, 90) 

# metadata files for output netCDF data products
GLOBAL_ATTRS_YAML = './metadata/attributes/globalAttributes_placeholder.yaml' #TODO
VARIABLE_ATTRS_YAML = './metadata/attributes/variableAttributes_placeholder.yaml' #TODO

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
    instrument = hyd_refdes[-9:]
    start_date = start_date.replace('/', '')

    gen_metadata(start_date, file_type, instrument)

    gen_hybrid_millidecade_spectrogram(start_date, instrument, apply_cals)


@task
def gen_metadata(start_date, file_type, instrument):
    logger = select_logger()

    date_dir = f"{start_date[:4]}_{start_date[4:6]}_{start_date[6:]}"

    # Audio data input specifications
    flac_uri = f'file://{str(Path.cwd())}/data/{file_type}/{date_dir}/{instrument}'
    logger.warning(flac_uri)
    flac_prefix = instrument # prefix for the audio files
    start_date = start_date # start date for temporal metadata extraction (YYYYMMDD)
    json_base_dir = 'metadata/json' # location to store generated metadata in JSON format

    # Convert the start and end dates to datetime objects
    start = datetime.strptime(start_date, "%Y%m%d")
    end = datetime.strptime(start_date, "%Y%m%d") # we can run multipl days on .nc files if needed

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


def gen_hybrid_millidecade_spectrogram(start_date, instrument, apply_cals=False):
    logger = select_logger()
    # set up directories
    download_dir = Path('./downloads')
    json_base_dir = Path('./metadata/json')
    output_dir = Path('./output')

    download_dir.mkdir(parents=True, exist_ok=True)
    json_base_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_prefix = f'{instrument}_' # a prefix for the name of generate files

    # hybrid millidecade settings 
    hmb_gen = HmbGen()

    hmb_gen.set_json_base_dir(str(json_base_dir))
    hmb_gen.set_global_attrs_uri(GLOBAL_ATTRS_YAML)
    hmb_gen.set_variable_attrs_uri(VARIABLE_ATTRS_YAML)
    hmb_gen.set_voltage_multiplier(VOLTAGE_MULTIPLIER)
    hmb_gen.set_subset_to(FREQ_LIMS)

    if apply_cals: #TODO
        logger.warning("CALS NOT YET IMPLEMENTED")
        #sensitivity_uri =  "./metadata/cals/NRS11_H5R6_sensitivity_hms5kHz_PLACEHOLDER.nc"
        #hmb_gen.set_sensitivity(-170) 
        #hmb_gen.set_sensitivity(sensitivity_uri)

    config = Config(signature_version=botocore.UNSIGNED)
    s3_client = boto3.client('s3', config=config)
    hmb_gen.set_s3_client(s3_client)

    hmb_gen.set_download_dir(str(download_dir))
    hmb_gen.set_output_dir(str(output_dir))
    hmb_gen.set_output_prefix(output_prefix)

    hmb_gen.set_print_downloading_lines(True)
    hmb_gen.set_retain_downloaded_files(True) 
    hmb_gen.set_assume_downloaded_files(True) # useful for running locally

    error = hmb_gen.check_parameters()
    # A message is returned in case of any errors
    if error:
        raise RuntimeError(f"check_parameters returned:\n{error}")
    
    # The resulting NetCDF file should have been saved under the output directory.
    result = hmb_gen.process_date(start_date)
    # sanity check
    logger.info(result.dataset)

    nc_filename = output_dir / f'{instrument}_{start_date}.nc'
    ds = xr.open_dataset(nc_filename, engine="h5netcdf")

    plot_dataset_summary(
        ds,
        lat_lon_for_solpos=HYDBB_COORDS[instrument],
        title=f'{instrument}, {HYDBB_COORDS[instrument][0]}°N, {HYDBB_COORDS[instrument][1]}°W', # TODO human readable title
        ylim=FREQ_LIMS,
        cmlim=DB_RANGE,
        jpeg_filename=f'{str(output_dir)}/{instrument}_{start_date}.png',
        show=False,
    )

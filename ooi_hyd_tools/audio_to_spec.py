from pbp.meta_gen.gen_iclisten import IcListenMetadataGenerator
from datetime import datetime, timedelta, timezone
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

from ooi_hyd_tools.instruments import HYDBB
from ooi_hyd_tools.thresholds import FREQ_LIMS
from ooi_hyd_tools.utils import select_logger


plt.switch_backend("Agg")  # use non-interactive backend
# hydrophone specification
VOLTAGE_MULTIPLIER = 3
# NOTE (original, per Shima Abadi) OOI broadband hydrophone output is
# 24-bit ADC with maximum 3 volts. So, there are 8388608 / 3 = 2796202 counts per volt
# which is equivalent to 128.9 dB (=20log10(2796202)). This offset is applied to the cal
# files in rca_correction_cals so here we USED a voltage multiplier of 1.

# UPDATE Aug 2026: same constant, applied in a different place. Stock pbp reads the FLAC
# as float normalized to full scale (+-1.0 = +-2**23 counts), so multiplying by 3 gives
# volts and the sheet sensitivities apply unmodified - no rca_correction_cals needed.
DB_RANGE = (45, 120)

# cal files (cals/) and netCDF attribute yamls (attributes/); the repo's copy by default
META_DIR = Path("./metadata")



@task
def audio_to_spec(start_date, flac_dir, hyd_refdes, apply_cals, freq_lims):
    """the pipeline's spectrogram stage, with its repo-relative folders"""
    return spectrogram_day(start_date, flac_dir, hyd_refdes, apply_cals, freq_lims)


def spectrogram_day(
    start_date,
    flac_dir,
    hyd_refdes,
    apply_cals,
    freq_lims=FREQ_LIMS,
    out_dir=Path("./output"),
    meta_dir=META_DIR,
    json_dir=None,
    download_dir=Path("./downloads"),
):
    """One day's hybrid millidecade spectrogram (.nc) and summary plot (.png) in out_dir,
    from a folder of pbp-named FLAC ({INSTRUMENT}_YYYYMMDD_HHMMSS.flac). json_dir (pbp's
    per-day audio index) defaults to meta_dir/json. Returns the .nc path."""
    # pbp takes dates as strings without slashes
    freq_lims = tuple(freq_lims)
    start_date = start_date.replace("/", "")
    meta_dir = Path(meta_dir)
    json_dir = Path(json_dir or meta_dir / "json")

    gen_metadata(start_date, flac_dir, hyd_refdes, json_dir)

    return gen_hybrid_millidecade_spectrogram(
        start_date, hyd_refdes, apply_cals, freq_lims, meta_dir, json_dir, Path(download_dir), Path(out_dir)
    )


def gen_metadata(start_date, flac_dir, hyd_refdes, json_dir):
    logger = select_logger()

    instrument = hyd_refdes[-9:]

    # Audio data input specifications
    flac_uri = f"file://{flac_dir}"
    logger.info(f"pbp reading audio from {flac_uri}")
    flac_prefix = instrument  # prefix for the audio files
    start_date = start_date  # start date for temporal metadata extraction (YYYYMMDD)

    # Convert the start and end dates to datetime objects
    start = datetime.strptime(start_date, "%Y%m%d")
    end = datetime.strptime(
        start_date, "%Y%m%d"
    )  # we can run multipl days on .nc files if needed

    # Create the metadata generator
    meta_gen = IcListenMetadataGenerator(
        log=logger,
        uri=flac_uri,
        json_base_dir=str(json_dir),
        start=start,
        end=end,
        prefixes=[flac_prefix],
        seconds_per_file=300,
    )

    meta_gen.run()


def find_cal_file(refdes, date_str, meta_dir=META_DIR):
    logger = select_logger()
    node = refdes[:8]
    current_utc_datetime = datetime.now(timezone.utc)

    day_start = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

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

    # pbp applies one sensitivity to the whole day, so a turnover day takes the cal of the
    # deployment covering most of it (the incoming one on an exact tie)
    df = df.with_columns(
        (
            pl.min_horizontal("stopDateTime", pl.lit(day_end))
            - pl.max_horizontal("startDateTime", pl.lit(day_start))
        ).alias("overlap")
    ).filter(pl.col("overlap") > timedelta(0))
    if df.is_empty():
        raise FileNotFoundError(
            f"no {refdes} deployment overlaps {date_str} - not deployed, so no cal to apply"
        )
    best = df.sort(["overlap", "startDateTime"], descending=True).row(0, named=True)
    deployment_number = best["deploymentNumber"]
    if best["overlap"] < timedelta(days=1):
        hours = best["overlap"].total_seconds() / 3600
        logger.warning(
            f"{date_str} is a turnover day: deployment {deployment_number} covers {hours:.1f} h "
            f"of it, and its cal is applied to the whole day"
        )

    cal_file_path = Path(meta_dir) / "cals" / f"{refdes}_{deployment_number}.nc"
    cal_file_path_str = str(cal_file_path)

    if not cal_file_path.exists():
        raise FileNotFoundError(f"No calibration file found for {date_str}")

    logger.info(f"{date_str} falls under deployment < {deployment_number} > for {refdes}")
    logger.info(f"cal file at {cal_file_path_str}")

    placeholder = xr.open_dataset(cal_file_path).attrs.get("placeholder")
    if placeholder:
        logger.warning(f"{cal_file_path.name} is a PLACEHOLDER cal: {placeholder}")

    return cal_file_path_str  # pbp wants a string not a path


def cal_provenance(cal_path):
    """the applied cal, recorded in the product so a changed deployment lookup is visible later"""
    p = Path(cal_path)
    with xr.open_dataset(p) as ds:
        cal = dict(ds.attrs)
    attrs = {
        "calibration_file": p.name,
        "calibration_deployment": int(p.stem.rsplit("_", 1)[-1]),
        "calibration_asset_id": cal.get("asset_id"),
        "calibration_date": cal.get("calibration_date"),
        "calibration_source_pdf": cal.get("source_pdf"),
        "calibration_placeholder": cal.get("placeholder"),
        "calibration_lf_sensitivity": cal.get("lf_sensitivity"),
        "calibration_lf_corner_hz": cal.get("lf_corner_hz"),
        # the static global attrs say SB35-ETH, which early deployments were not
        "instrument": f"icListen model {cal['model']}" if cal.get("model") else None,
    }
    return {k: v for k, v in attrs.items() if v is not None}


def gen_hybrid_millidecade_spectrogram(
    start_date, hyd_refdes, apply_cals, freq_lims, meta_dir, json_dir, download_dir, output_dir
):
    logger = select_logger()
    instrument = hyd_refdes[-9:]
    for d in (download_dir, json_dir, output_dir):
        d.mkdir(parents=True, exist_ok=True)

    output_prefix = f"{instrument}_"  # a prefix for the name of generate files

    # hybrid millidecade settings
    hmb_gen = HmbGen()

    hmb_gen.set_json_base_dir(str(json_dir))
    hmb_gen.set_global_attrs_uri(str(meta_dir / "attributes/globalAttributes.yaml"))
    hmb_gen.set_variable_attrs_uri(str(meta_dir / "attributes/variableAttributes.yaml"))
    hmb_gen.set_voltage_multiplier(VOLTAGE_MULTIPLIER)
    hmb_gen.set_subset_to(freq_lims)

    cal_attrs = {"calibration_file": "none"}
    if apply_cals:
        logger.info(f"Applying calibration .nc files from {meta_dir / 'cals'}")
        sensitivity_uri = find_cal_file(hyd_refdes, start_date, meta_dir)
        # hmb_gen.set_sensitivity(-170)
        hmb_gen.set_sensitivity(sensitivity_uri)
        cal_attrs = cal_provenance(sensitivity_uri)

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

    if not hasattr(result, "dataset"):
        raise RuntimeError(f"pbp failed for {start_date}: {result}")
    logger.info(result.dataset)  # sanity check

    nc_filename = output_dir / f"{instrument}_{start_date}.nc"
    ds = xr.load_dataset(nc_filename, engine="h5netcdf")  # load, so the file can be rewritten

    # globalAttributes.yaml is static, so every instrument inherited HYDBBA106's position
    # and a platform listing all six. Set both from the refdes actually being processed.
    lat, lon = HYDBB[hyd_refdes].lat, HYDBB[hyd_refdes].lon
    ds.attrs["geospatial_bounds"] = f"POINT ({lat} {lon})"
    ds.attrs["platform"] = hyd_refdes
    ds.attrs.update(cal_attrs)
    ds.to_netcdf(nc_filename, engine="h5netcdf")

    plot_dataset_summary(
        ds,
        lat_lon_for_solpos=(lat, lon),
        title=f"{hyd_refdes}, {lat}°N, {abs(lon)}°W",
        ylim=freq_lims,
        cmlim=DB_RANGE,
        jpeg_filename=f"{str(output_dir)}/{instrument}_{start_date}.png",
        show=False,
    )
    return nc_filename

import click
import yaml 

from prefect.deployments import run_deployment
from datetime import datetime, timedelta, timezone
from ooi_hyd_tools.mseed_to_audio import acoustic_flow_oneday
from ooi_hyd_tools.utils import select_logger

logger = select_logger()

PREFECT_DEPLOYMENT = "acoustic-flow-oneday/"
TIMEOUT = 12  # if lowered, the OOI raw data server will be overloaded

# get yesterday's date in YYYY/MM/DD format
now_utc = datetime.now(timezone.utc)
yesterday_utc = now_utc - timedelta(days=1)
yesterday = yesterday_utc.strftime("%Y/%m/%d")

with open("./ooi_hyd_tools/config/config.yaml", "r") as f:
    config_dict = yaml.safe_load(f)

@click.command()
@click.option(
    "--start-date",
    type=str,
    default=yesterday,
    help="Date in the format YYYY/MM/DD (e.g., '2025/01/16'). Default is yesterday's date (UTC).",
)
@click.option(
    "--end-date", type=str, default=None, help="YYYY/MM/DD leave blank to run a single day"
)
@click.option(
    "--hyd-refdes",
    type=str,
    required=True,
    help="Hydrophone reference designator (e.g., 'CE04OSBP-LJ01C-11-HYDBBA105').",
)
@click.option(
    "--format",
    type=click.Choice(["FLOAT", "PCM_24", "PCM_32"], case_sensitive=False),
    default="PCM_24",
    show_default=True,
    help="format subtype (FLOAT, PCM_24, or PCM_32).",
)
@click.option(
    "--normalize-traces",
    type=bool,
    default=False,
    show_default=True,
    help="Set to True to normalize audio data in 5 minute incriments.",
)
@click.option(
    "--fudge-factor",
    type=float,
    default=0.02,
    show_default=True,
    help="The maxiximum size gap/overlap in the mseed data you want to tolerate without throwing an error (in seconds).",
)
@click.option(
    "--write-wav",
    type=bool,
    default=False,
    show_default=True,
    help="Set to True to write wav files in addition to flac.",
)
@click.option(
    "--apply-cals",
    type=bool,
    default=True,
    show_default=True,
    help="Apply hydrophone calibration before generateing hybrid millidecade spectrograms." 
    "Available for HYDBBA105, HYDBBA106, HYDBBA302, HYDBBA106 since program inception - moorings since 2025",
)
@click.option(
    "--freq-lims",
    type=(int, int),
    default=(10, 30_000),
    show_default=True,
    help="Frequency limits for the hybrid millidecade spectrograms (min, max). Default is (10, 30,000) As of 6/2025 OOI hydrophones"
    " are recording at 64 kHz.",
)
@click.option(
    "--s3-sync",
    type=bool,
    default=False,
    show_default=True,
    help="Whether to sync .nc and .png files in local output folder to s3",
)
@click.option(
    "--flag",
    type=click.Choice(["audio", "viz", "all", "low_freq"], case_sensitive=False),
    default="all",
    show_default=True,
    help="Which stage of pipeline to run: 'audio' converts mseed to audio, 'viz' converts audio to spectrograms, 'all' runs both."
    " 'low_freq' generates spectrograms for low freq hydrophones",
)
@click.option(
    "--parallel-in-cloud",
    type=bool,
    default=False,
    show_default=True,
    help="run prefect deployment in parellel in cloud, parallelized by date - need access to RCA cloud",
)
def run_acoustic_pipeline(
    start_date,
    end_date,
    hyd_refdes,
    format,
    normalize_traces,
    fudge_factor,
    write_wav,
    apply_cals,
    freq_lims,
    s3_sync,
    flag,
    parallel_in_cloud,
):
    if parallel_in_cloud:

        start_date = datetime.strptime(start_date, "%Y/%m/%d")
        if end_date is None:
            run_name = f"{hyd_refdes}_{start_date.strftime('%Y-%m-%d')}"
            params = {
                "hyd_refdes": hyd_refdes,
                "date": start_date.strftime("%Y/%m/%d"),
                "format": format,
                "normalize_traces": normalize_traces,
                "fudge_factor": fudge_factor,
                "write_wav": write_wav,
                "apply_cals": apply_cals,
                "freq_lims": freq_lims,
                "s3_sync": s3_sync,
                "flag": flag,
            }

            logger.info(f"Launching workflow for {run_name} in cloud")
            run_deployment(
                name=f"{PREFECT_DEPLOYMENT}{config_dict[hyd_refdes]}",
                parameters=params,
                flow_run_name=run_name,
                timeout=TIMEOUT,
            )
        else:
            end_date = datetime.strptime(end_date, "%Y/%m/%d")
            while start_date <= end_date:
                run_name = f"{hyd_refdes}_{start_date.strftime('%Y-%m-%d')}"
                params = {
                    "hyd_refdes": hyd_refdes,
                    "date": start_date.strftime("%Y/%m/%d"),
                    "format": format,
                    "normalize_traces": normalize_traces,
                    "fudge_factor": fudge_factor,
                    "write_wav": write_wav,
                    "apply_cals": apply_cals,
                    "freq_lims": freq_lims,
                    "s3_sync": s3_sync,
                    "flag": flag,
                }
                logger.info(f"Launching workflow for {run_name} in cloud")
                run_deployment(
                    name=f"{PREFECT_DEPLOYMENT}{config_dict[hyd_refdes]}",
                    parameters=params,
                    flow_run_name=run_name,
                    timeout=TIMEOUT,
                )
                start_date += timedelta(days=1)

    else:
        start_date = datetime.strptime(start_date, "%Y/%m/%d")

        if end_date is None:  # run a single day
            acoustic_flow_oneday(
                hyd_refdes=hyd_refdes,
                date=start_date.strftime("%Y/%m/%d"),
                format=format,
                normalize_traces=normalize_traces,
                fudge_factor=fudge_factor,
                write_wav=write_wav,
                apply_cals=apply_cals,
                freq_lims=freq_lims,
                s3_sync=s3_sync,
                flag=flag,
            )

        else:  # run a range of days
            end_date = datetime.strptime(end_date, "%Y/%m/%d")

            while start_date <= end_date:
                acoustic_flow_oneday(
                    hyd_refdes=hyd_refdes,
                    date=start_date.strftime("%Y/%m/%d"),
                    format=format,
                    normalize_traces=normalize_traces,
                    fudge_factor=fudge_factor,
                    write_wav=write_wav,
                    apply_cals=apply_cals,
                    freq_lims=freq_lims,
                    s3_sync=s3_sync,
                    flag=flag,
                )

                start_date += timedelta(days=1)


# local debugging
if __name__ == "__main__":
    run_acoustic_pipeline()

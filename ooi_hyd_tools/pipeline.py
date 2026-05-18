import click
import yaml

from abc import ABC, abstractmethod
from prefect.deployments import run_deployment
from datetime import datetime, timedelta, timezone
from ooi_hyd_tools.mseed_to_audio import acoustic_flow_oneday
from ooi_hyd_tools.utils import select_logger

logger = select_logger()

PREFECT_DEPLOYMENT = "acoustic-flow-oneday/"
TIMEOUT = 12  # if lowered, the OOI raw data server will be overloaded

now_utc = datetime.now(timezone.utc)
yesterday_utc = now_utc - timedelta(days=1)
yesterday = yesterday_utc.strftime("%Y/%m/%d")

with open("./ooi_hyd_tools/config/config.yaml", "r") as f:
    HYD_CONFIG_DICT = yaml.safe_load(f)
with open("./ooi_hyd_tools/config/obs_config.yaml", "r") as f:
    OBS_CONFIG_DICT = yaml.safe_load(f)


def iter_dates(start: datetime, end: datetime | None):
    current = start
    end = end or start
    while current <= end:
        yield current
        current += timedelta(days=1)


def build_params(
    date: datetime,
    hyd_refdes,
    format,
    normalize_traces,
    fudge_factor,
    write_wav,
    apply_cals,
    freq_lims,
    s3_sync,
    flag,
    obs_run_type,
) -> dict:
    return {
        "hyd_refdes": hyd_refdes,
        "date": date.strftime("%Y/%m/%d"),
        "format": format,
        "normalize_traces": normalize_traces,
        "fudge_factor": fudge_factor,
        "write_wav": write_wav,
        "apply_cals": apply_cals,
        "freq_lims": freq_lims,
        "s3_sync": s3_sync,
        "flag": flag,
        "obs_run_type": obs_run_type,
    }


class Runner(ABC):
    @abstractmethod
    def run(self, date: datetime, params: dict) -> None: ...


class LocalRunner(Runner):
    def run(self, date: datetime, params: dict) -> None:
        acoustic_flow_oneday(**params)


class PrefectRunner(Runner):
    def __init__(self, deployment_name: str):
        self.deployment_name = deployment_name

    def run(self, date: datetime, params: dict) -> None:
        run_name = f"{params['hyd_refdes']}_{date.strftime('%Y-%m-%d')}"
        logger.info(f"Launching workflow for {run_name} in cloud")
        run_deployment(
            name=self.deployment_name,
            parameters=params,
            flow_run_name=run_name,
            timeout=TIMEOUT,
        )


class CeleryRunner(Runner):
    def run(self, date: datetime, params: dict) -> None:
        raise NotImplementedError("CeleryRunner is not yet implemented")


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
    type=click.Choice(["audio", "viz", "all", "low_freq", "obs"], case_sensitive=False),
    default="all",
    show_default=True,
    help="Which stage of pipeline to run: 'audio' converts mseed to audio for broadband hydrophoines,"
    " 'viz' converts audio to spectrograms for broadband, 'all' runs both 'audio' and 'viz' broadband routines."
    " 'low_freq' generates spectrograms for low freq hydrophones, 'obs' processes OBS seismometer data.",
)
@click.option(
    "--obs-run-type",
    type=click.Choice(["daily", "weekly"], case_sensitive=False),
    default="daily",
    show_default=True,
    help="Only use with --flag 'obs', 'daily' run-type generates plots for 1, 7 day spans, 'weekly'"
    "also includes 30 day span.",
)
@click.option(
    "--runner",
    type=click.Choice(["local", "prefect", "celery"], case_sensitive=False),
    default="local",
    show_default=True,
    help="Runner backend: 'local' runs in-process, 'prefect' dispatches to Prefect cloud deployment parallelized by date,"
    " 'celery' dispatches to Celery workers (not yet implemented).",
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
    obs_run_type,
    runner,
):
    config_dict = OBS_CONFIG_DICT if flag == "obs" else HYD_CONFIG_DICT
    deployment_name = (
        f"{PREFECT_DEPLOYMENT}{config_dict[hyd_refdes][obs_run_type]}"
        if flag == "obs"
        else f"{PREFECT_DEPLOYMENT}{config_dict[hyd_refdes]}"
    )

    runners = {
        "local": LocalRunner(),
        "prefect": PrefectRunner(deployment_name),
        "celery": CeleryRunner(),
    }
    _runner = runners[runner]

    start_date = datetime.strptime(start_date, "%Y/%m/%d")
    end_date = datetime.strptime(end_date, "%Y/%m/%d") if end_date else None

    for date in iter_dates(start_date, end_date):
        params = build_params(
            date=date,
            hyd_refdes=hyd_refdes,
            format=format,
            normalize_traces=normalize_traces,
            fudge_factor=fudge_factor,
            write_wav=write_wav,
            apply_cals=apply_cals,
            freq_lims=freq_lims,
            s3_sync=s3_sync,
            flag=flag,
            obs_run_type=obs_run_type,
        )
        _runner.run(date, params)


if __name__ == "__main__":
    run_acoustic_pipeline()

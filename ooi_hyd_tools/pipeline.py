import click

from prefect.deployments import run_deployment
from datetime import datetime, timedelta
from ooi_hyd_tools.mseed_to_audio import acoustic_flow_oneday
from loguru import logger

PREFECT_DEPLOYMENT = "hydbb_pipeline_2vcpu_16gb"

@click.command()
@click.option(
    "--start-date", 
    type=str,
    required=True,
    help="Date in the format YYYY/MM/DD (e.g., '2025/01/16')."
    )
@click.option(
    "--end-date", 
    type=str, 
    default=None,
    help="YYYY/MM/DD leave blank to run a single day") 
@click.option(
    "--hyd-refdes", 
    type=str, 
    required=True, 
    help="Hydrophone reference designator (e.g., 'CE04OSBP-LJ01C-11-HYDBBA105')."
)
@click.option(
    "--sr", 
    type=int, 
    default=64000, 
    show_default=True, 
    help="Sample rate in Hz (e.g., 64000)."
)
@click.option(
    "--format", 
    type=click.Choice(["FLOAT", "PCM_24", "PCM_32"], case_sensitive=False), 
    default="PCM_24", 
    show_default=True, 
    help="format subtype (FLOAT, PCM_24, or PCM_32)."
)
@click.option(
    "--normalize-traces", 
    type=bool, 
    default=False, 
    show_default=True, 
    help="Set to True to normalize traces for Audacity."
)
@click.option(
    "--fudge-factor", 
    type=float, 
    default=0.02, 
    show_default=True, 
    help="The maxiximum size gap/overlap in the mseed data you want to tolerate without throwing an error (in seconds)."
)
@click.option(
    "--write-wav", 
    type=bool, 
    default=False, 
    show_default=True, 
    help="Set to True to write wav files in addition to flac."
)
@click.option(
    "--apply-cals", 
    type=bool, 
    default=False, 
    show_default=True, 
    help="NOT YET IMPLEMENTED!!! Apply hydrophone calibration before generateing hybrid millidecade spectrograms."
) # TODO not yet implemented
@click.option(
    "--s3-sync", 
    type=bool, 
    default=False, 
    show_default=True, 
    help="Whether to sync .nc and .png files in local output folder to s3"
)
@click.option(
    "--stages", 
    type=click.Choice(["audio", "viz", "all"], case_sensitive=False), 
    default="audio", 
    show_default=True, 
    help="Which stage of pipeline to run: 'audio' converts mseed to audio, 'viz' converts audio to spectrograms, 'all' runs both."
)
@click.option(
    "--parallel-in-cloud",
    type=bool,
    default=False,
    show_default=True,
    help="run prefect deployment in parellel in cloud, parallelized by date - need access to RCA cloud"
)
def run_acoustic_pipeline(
    start_date,
    end_date,
    hyd_refdes,
    sr,
    format,
    normalize_traces,
    fudge_factor,
    write_wav,
    apply_cals,
    s3_sync,
    stages,
    parallel_in_cloud,
):
    
    if parallel_in_cloud:

        start_date = datetime.strptime(start_date, "%Y/%m/%d")
        if end_date is None:

            run_name = f"{hyd_refdes}_{start_date.strftime("%Y-%m-%d")}"
            params = {
                "hyd_refdes": hyd_refdes,
                "date": start_date.strftime("%Y/%m/%d"),
                "sr": sr,
                "format": format,
                "normalize_traces": normalize_traces,
                "fudge_factor": fudge_factor,
                "write_wav": write_wav,
                "apply_cals": apply_cals,
                "s3_sync": s3_sync,
                "stages": stages,
            }

            logger.info(f"Launching workflow for {run_name} in cloud")
            run_deployment(
                name=PREFECT_DEPLOYMENT,
                parameters=params,
                flow_run_name=run_name,
                timeout=5
            )
        else: 
            end_date = datetime.strptime(end_date, "%Y/%m/%d")
            while start_date <= end_date:
                run_name = f"{hyd_refdes}_{start_date.strftime("%Y-%m-%d")}"
                params = {
                    "hyd_refdes": hyd_refdes,
                    "date": start_date.strftime("%Y/%m/%d"),
                    "sr": sr,
                    "format": format,
                    "normalize_traces": normalize_traces,
                    "fudge_factor": fudge_factor,
                    "write_wav": write_wav,
                    "apply_cals": apply_cals,
                    "s3_sync": s3_sync,
                    "stages": stages,
                }
                logger.info(f"Launching workflow for {run_name} in cloud")
                run_deployment(
                name=PREFECT_DEPLOYMENT,
                parameters=params,
                flow_run_name=run_name,
                timeout=5
                )
                start_date += timedelta(days=1)
    
    else:
        start_date = datetime.strptime(start_date, "%Y/%m/%d")

        if end_date is None: # run a single day
            acoustic_flow_oneday(
                    hyd_refdes=hyd_refdes,
                    date=start_date.strftime("%Y/%m/%d"),
                    sr=sr,
                    format=format,
                    normalize_traces=normalize_traces,
                    fudge_factor=fudge_factor,
                    write_wav=write_wav,
                    apply_cals=apply_cals,
                    s3_sync=s3_sync,
                    stages=stages,
                )

        else: # run a range of days
            end_date = datetime.strptime(end_date, "%Y/%m/%d")

            while start_date <= end_date:
                acoustic_flow_oneday(
                    hyd_refdes=hyd_refdes,
                    date=start_date.strftime("%Y/%m/%d"),
                    sr=sr,
                    format=format,
                    normalize_traces=normalize_traces,
                    fudge_factor=fudge_factor,
                    write_wav=write_wav,
                    apply_cals=apply_cals,
                    s3_sync=s3_sync,
                    stages=stages,
                )

                start_date += timedelta(days=1)


# local debugging
if __name__ == "__main__":
    run_acoustic_pipeline()


import click

from prefect import flow
from datetime import datetime, timedelta
from ooi_hyd_tools.mseed_to_audio import main as acoustic_pipeline


@flow
def acoustic_flow_oneday(
    hyd_refdes, 
    date, 
    sr, 
    format, 
    normalize_traces, 
    fudge_factor, 
    write_wav, 
    apply_cals, 
    s3_sync,
    stages,
):

    acoustic_pipeline(
    hyd_refdes,
    date,
    sr,
    format,
    normalize_traces,
    fudge_factor,
    write_wav,
    apply_cals,
    s3_sync,
    stages,
)


@click.command()
@click.option("--start-date", type=str)
@click.option("--end-date", type=str, default=None) # TODO allow one day run
@click.option("--hyd-refdes", type=str)
@click.option("--sr", type=int, default=64000)
@click.option("--format", type=click.Choice(["FLOAT", "PCM_24", "PCM_32"], case_sensitive=False), 
    default="FLOAT", 
)
@click.option("--normalize-traces", type=bool, default=False)
@click.option("--fudge-factor", type=float, default=0.02)
@click.option("--write-wav", type=bool, default=False)
@click.option("--apply-cals", type=bool, default=False) # TODO not yet implemented
@click.option("--s3-sync", type=bool, default=False)
@click.option(
    "--stages", 
    type=click.Choice(["audio", "viz", "all"], case_sensitive=False), 
    default="audio", 
)
def run_acoustic_date_range(
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
):
    start_date = datetime.strptime(start_date, "%Y/%m/%d")

    if end_date is None: # run a single day
         acoustic_flow_oneday(
                hyd_refdes=hyd_refdes,
                date=start_date,
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
                date=start_date,
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

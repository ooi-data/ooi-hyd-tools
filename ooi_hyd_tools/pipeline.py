from prefect import flow
from datetime import datetime, timedelta
from mseed_to_audio import main as acoustic_pipeline


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
)


@flow
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
):
    
    start_date = datetime.strptime(start_date, "%Y-%m-%d")

    while start_date <= end_date:
        acoustic_flow_oneday(
            hyd_refdes=hyd_refdes,
            start_date=start_date,
            sr=sr,
            format=format,
            normalize_traces=normalize_traces,
            fudge_factor=fudge_factor,
            write_wav=write_wav,
            apply_cals=apply_cals,
            s3_sync=s3_sync,
        )

        start_date += timedelta(days=1)

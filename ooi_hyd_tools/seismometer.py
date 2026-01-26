import requests
import obspy as obs
import matplotlib as mpl
import matplotlib.dates as mdates

from datetime import datetime, timedelta
from pathlib import Path
from prefect import task

from ooi_hyd_tools.utils import select_logger

mpl.rcParams.update(mpl.rcParamsDefault)  # reset matplotlib params to avoid latex bug


PARAM_NAME = "groundvel_accel"
# IRIS station code mapped to OOI refdes
STATION_DICT = {
    "RS03ASHS-MJ03B-06-OBSSPA301": "AXAS1",
    "RS03ASHS-MJ03B-05-OBSSPA302": "AXAS2",
    "RS03AXBS-MJ03A-05-OBSBBA303": "AXBA1",
    "RS03CCAL-MJ03F-06-OBSBBA301": "AXCC1",
    "RS03ECAL-MJ03E-05-OBSSPA303": "AXEC1",
    "RS03ECAL-MJ03E-09-OBSBBA302": "AXEC2",
    "RS03ECAL-MJ03E-08-OBSSPA304": "AXEC3",
    "RS03INT2-MJ03D-05-OBSSPA305": "AXID1",
    "RS01SUM1-LJ01B-08-OBSSPA101": "HYS11",
    "RS01SUM1-LJ01B-07-OBSSPA102": "HYS12",
    "RS01SUM1-LJ01B-06-OBSSPA103": "HYS13",
    "RS01SUM1-LJ01B-05-OBSBBA101": "HYS14",
    "RS01SLBS-MJ01A-05-OBSBBA102": "HYSB1",
}
NETWORK = "OO"
CHUNK_SIZE_DAYS = 4 # if timespan is too large for obspy/earthscope

def make_url(station, starttime, endtime):
    "format url for IRIS data service"
    # datetime format: 2025-11-04T00:00:00
    return f"https://service.iris.edu/fdsnws/dataselect/1/query?net={NETWORK}&sta={station}&starttime={starttime}&endtime={endtime}&format=miniseed&nodata=404"


def annotate_sampling_rate(fig, st):
    """annotate sampling rate on each obspy subplot because obspy doesn't expose it nicely"""
    for ax in fig.axes:
        for txt in ax.texts:
            trace_label = txt.get_text()
            channel = trace_label.split("..")[-1]

            # find the corresponding trace
            matching_traces = [tr for tr in st if tr.stats.channel == channel]
            if matching_traces:
                sf = matching_traces[0].stats.sampling_rate
                label = f"{sf} Hz"

                ax.text(
                    0.97,
                    0.97,
                    label,
                    transform=ax.transAxes,
                    ha="right",
                    va="top",
                    fontsize=9,
                    bbox=dict(facecolor="white", alpha=0.7, edgecolor="none", pad=2),
                )


@task
def run_obs_viz(refdes: str, date_str: str, obs_run_type: str):
    logger = select_logger()

    if obs_run_type == "daily":
        time_spans = {1: "day", 7: "week"}
    elif obs_run_type == "weekly":
        time_spans = {1: "day", 7: "week", 30: "month"}

    output_dir = Path(f"./output/{refdes[:8]}")
    output_dir.mkdir(parents=True, exist_ok=True)

    date = datetime.strptime(date_str, "%Y/%m/%d") + timedelta(days=1) # end date should be yesterday UTC
    end_date = date.strftime("%Y-%m-%dT00:00:00")

    start_dates = {
        span: (date - timedelta(days=span)).strftime("%Y-%m-%dT00:00:00")
        for span in time_spans.keys()
    }

    data_dict = {}
    for span, start_date in start_dates.items():
        logger.info(
            f"Requesting data for {refdes} from {start_date} to {end_date} for {span}-day span"
        )
        try:
            st = obs.read(make_url(STATION_DICT[refdes], start_date, end_date))
            data_dict[span] = st
        except (obs.io.mseed.ObsPyMSEEDFilesizeTooLargeError, requests.exceptions.HTTPError):
            logger.warning(f"mseed file too large, requesting in {CHUNK_SIZE_DAYS}-day chunks")

            chunked_stream_list = []  # some complicated datetime formatting
            chunk_start = datetime.strptime(start_date, "%Y-%m-%dT00:00:00")  # datetime
            while chunk_start < datetime.strptime(end_date, "%Y-%m-%dT00:00:00"):  # datetimes
                chunk_end = min(
                    chunk_start + timedelta(days=CHUNK_SIZE_DAYS),
                    datetime.strptime(end_date, "%Y-%m-%dT00:00:00"),
                )  # datetimes

                logger.info(
                    f"Reading chunk: {chunk_start.strftime('%Y-%m-%dT00:00:00')} → {chunk_end.strftime('%Y-%m-%dT00:00:00')}"
                )  # strings

                st_chunk = obs.read(
                    make_url(
                        STATION_DICT[refdes],
                        chunk_start.strftime("%Y-%m-%dT00:00:00"),
                        chunk_end.strftime("%Y-%m-%dT00:00:00"),
                    )
                )  # strings

                chunked_stream_list.append(st_chunk)
                chunk_start = chunk_end

            st = obs.Stream()
            logger.info(f"Combining {len(chunked_stream_list)} chunks into single stream")
            for st_chunk in chunked_stream_list:
                st += st_chunk
            data_dict[span] = st

    for span, st in data_dict.items():
        for tr in st:
            tr.stats.sampling_rate = round(
                tr.stats.sampling_rate
            )  # sometimes IRIS returns non-integer rates, which messes up plotting

        # TODO how to display empty streams?
        fig = st.plot(size=(1200, 1450), linewidth=0.05)
        fig.suptitle(refdes, fontsize=15, fontweight="bold")
        fig.supylabel("Digital Counts", fontsize=10, x=0.01)
        for ax in fig.axes:  # smaller y tick labels
            ax.tick_params(axis="y", labelsize=6)

        annotate_sampling_rate(fig, st)

        if span >= 7:  # a tick for each day
            for ax in fig.axes:
                ax.xaxis.set_major_locator(mdates.DayLocator())
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))

        fpath = output_dir / f"{refdes}_{PARAM_NAME}_{time_spans[span]}_none_full.png"
        fig.savefig(fpath)

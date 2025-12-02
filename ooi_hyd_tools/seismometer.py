import obspy as obs
import matplotlib as mpl
from datetime import datetime, timedelta
from pathlib import Path
from prefect import task

from ooi_hyd_tools.utils import select_logger

mpl.rcParams.update(mpl.rcParamsDefault) # reset matplotlib params to avoid latex bug


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


def make_url(station, starttime, endtime):
    "format url for IRIS data service"
    # datetime format: 2025-11-04T00:00:00
    return f"https://service.iris.edu/fdsnws/dataselect/1/query?net={NETWORK}&sta={station}&starttime={starttime}&endtime={endtime}&format=miniseed&nodata=404"

@task
def run_obs_viz(refdes, date_str, obs_run_type):
    logger = select_logger()

    if obs_run_type == "daily":
        time_spans = {1: "day", 7: "week"}
    elif obs_run_type == "weekly":
        time_spans = {1: "day", 7: "week", 30: "month"}

    output_dir = Path(f"./output/{refdes[:8]}")
    output_dir.mkdir(parents=True, exist_ok=True)

    date = datetime.strptime(date_str, "%Y/%m/%d")
    end_date = date.strftime("%Y-%m-%dT00:00:00")

    start_dates = {span :(date - timedelta(days=span)).strftime("%Y-%m-%dT00:00:00") for span in time_spans.keys()}

    data_dict = {}
    for span, start_date in start_dates.items(): 

        logger.info(f"Requesting data for {refdes} from {start_date} to {end_date} for {span}-day span")
        st = obs.read(make_url(STATION_DICT[refdes], start_date, end_date))
        data_dict[span] = st

    for span, st in data_dict.items():

        for tr in st:
            tr.stats.sampling_rate = round(tr.stats.sampling_rate) # sometimes IRIS returns non-integer rates, which messes up plotting
        
        # TODO how to display empty streams?
        fig = st.plot(size=(1200, 1450), linewidth=0.05)
        fig.suptitle(refdes, fontsize=15, fontweight="bold")
        fpath = output_dir / f"{refdes}_{PARAM_NAME}_{time_spans[span]}_none_full.png"
        fig.savefig(fpath)


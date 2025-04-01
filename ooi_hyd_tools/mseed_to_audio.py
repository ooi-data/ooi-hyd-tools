import fsspec
import concurrent.futures
import obspy as obs
import numpy as np
import multiprocessing as mp
import soundfile as sf
import matplotlib.pyplot as plt

from datetime import datetime
from tqdm import tqdm
from pathlib import Path
from prefect import task, flow

from ooi_hyd_tools.audio_to_spec import audio_to_spec
from ooi_hyd_tools.cloud import sync_png_nc_to_s3
from ooi_hyd_tools.utils import select_logger


"""
This script converts OOI hydrophone data stored as mseed files on the OOI raw data archive 
into 5 minute flac files using obspy and soundfile. Flac file names are written to 
"./data/flac/YYYY_MM_DD/INSTRUMENT".
Files are named in the datetime format "INSTRUMENT_YYMMDDHHMMSS"
The user can set the following processing parameters: 

HYD_REFDES
    The OOI reference designator for the hydrophone you want to process. For example, 
    "CE04OSBP-LJ01C-11-HYDBBA105" is the OOI hydrophone at the Oregon Offshore (600m) site. 
    "CE04OSBP-LJ01C-11-HYDBBA110" is the co-located Ocean Sonics test hydrophone at that same site.
DATE
    The day of hydrophone data you would like to convert to wav in the date format
    YYYY/MM/DD.
SR
    Sample rate you wish to use when saving wav files. OOI Hydrophone sampling rate is 64000 Hz.
FORMAT
    'PCM_32' or 'FLOAT' The data subtype format for the resulting WAV files. OOI data is int32, 
     but some media players cannot import in this format. See `sf.available_subtypes('WAV')`
NORMALIZE_TRACES
    Option to normalize signal by mean of each 5 minute trace. If normalized float32 data type is needed.

NOTE See cli in pipeline.py for additional help and context.
"""


def _map_concurrency(func, iterator, args=(), max_workers=-1, verbose=False):
    # automatically set max_workers to 2x(available cores)
    if max_workers == -1:
        max_workers = 2 * mp.cpu_count()
        print(f"Max workers: {max_workers}")

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Start the load operations and mark each future with its URL
        future_to_url = {executor.submit(func, i, args): i for i in iterator}
        # Disable progress bar
        is_disabled = not verbose
        for future in tqdm(
            concurrent.futures.as_completed(future_to_url),
            total=len(iterator),
            disable=is_disabled,
        ):
            data = future.result()
            results.append(data)
    return results


class HydrophoneDay:
    def __init__(
        self,
        refdes,
        str_date,
        fudge_factor,
        clean_list=None,
    ):
        self.refdes = refdes
        self.date = datetime.strptime(str_date, "%Y/%m/%d")
        self.fudge_factor = fudge_factor
        self.mseed_urls = self.get_mseed_urls(str_date, refdes)
        self.clean_list = clean_list
        self.file_str = f"{self.refdes}_{self.date.strftime('%Y_%m_%d')}"

    def get_mseed_urls(self, day_str, refdes):
        base_url = "https://rawdata.oceanobservatories.org/files"
        mainurl = f"{base_url}/{refdes[0:8]}/{refdes[9:14]}/{refdes[18:27]}/{day_str}/"
        FS = fsspec.filesystem("http")
        print(mainurl)
        print(Path.cwd())

        try:
            data_url_list = sorted(
                f["name"]
                for f in FS.ls(mainurl)
                if f["type"] == "file" and f["name"].endswith(".mseed")
            )
        except Exception as e:
            print("Client response: ", str(e))
            return None

        if not data_url_list:
            print("No Data Available for Specified Time")
            return None

        return data_url_list

    def read_and_repair_gaps(self, format):
        if self.mseed_urls is None:
            return None
        else:
            self.clean_list = _map_concurrency(
                func=self._deal_with_gaps_and_overlaps,
                args=format,
                iterator=self.mseed_urls,
                verbose=False,
            )

    def _merge_by_timestamps(self, st):
        cs = st.copy()

        data = []
        for tr in cs:
            data.append(tr.data)
        data_cat = np.concatenate(data)

        stats = dict(cs[0].stats)
        stats["starttime"] = st[0].stats["starttime"]
        stats["endtime"] = st[-1].stats[
            "endtime"
        ]  # TODO we may want to set the endtime based on n datapoints and not just the endtime of the last trace
        stats["npts"] = len(data_cat)

        cs = obs.Stream(traces=obs.Trace(data_cat, header=stats))

        return cs

    def _deal_with_gaps_and_overlaps(self, url, format):

        if format not in ["PCM_32", "PCM_24", "FLOAT"]:
            raise ValueError("Invalid wav data subtype. Please specify 'PCM_32' or 'FLOAT'")
        # first read in mseed
        if format == "PCM_32" or format == "PCM_24":
            st = obs.read(url, apply_calib=False, dtype=np.int32)
        if format == "FLOAT":
            st = obs.read(url, apply_calib=False, dtype=np.float64)

        trace_id = st[0].stats["starttime"]
        print("total traces before concatenation: " + str(len(st)), flush=True)
        # if 19.2 samples +- 640 then concat
        samples = 0
        for trace in st:
            samples += len(trace)

        if 19199360 <= samples <= 19200640:  # CASE A: just jitter, no true gaps
            print(f"There are {samples} samples in this stream, Simply concatenating")
            cs = self._merge_by_timestamps(st)
            print("total traces after concatenation: " + str(len(cs)))
        else:
            print(
                f"{trace_id}: there are a unexpected number of samples in this file: {samples} Checking for large gaps:"
            )
            gaps = st.get_gaps()
            st_contains_large_gap = False
            # loop checks for large gaps
            for gap in gaps:
                if abs(gap[6]) > self.fudge_factor:  # the gaps 6th element is the gap length
                    st_contains_large_gap = True
                    break

            if st_contains_large_gap:  # CASE B: - edge case? - LARGE GAPS WILL RAISE ERROR
                print(
                    f"{trace_id}: This file contains large gaps - {gap}. Cannot repair with currently implimented methods"
                )
                return None
                # raise ValueError(f"{trace_id}: This file contains large gaps - {gap}. Cannot repair with currently implimented methods")
                # TODO if this is deployed we want to make multiple files seperated by gaps > fudge factor
            else:  # CASE C: shortened mseed file before divert with no large gaps
                print(
                    f"{trace_id}: This file is short but only contains jitter. Simply concatenating"
                )
                cs = self._merge_by_timestamps(st)
                print("total traces after concatenation: " + str(len(cs)), flush=True)
        return cs


@task(retries=2, retry_delay_seconds=60)
def convert_mseed_to_audio(
    hyd_refdes,
    date,
    fudge_factor,
    sr,
    format,
    normalize_traces,
    write_wav,
):
    logger = select_logger()
    hyd = HydrophoneDay(hyd_refdes, date, fudge_factor)

    hyd.read_and_repair_gaps(format=format)

    if hyd.clean_list is None:  # retun None if no data available on that day
        return None, None, None
    else:
        # make dirs
        logger.info("Creating data directories")
        date_str = datetime.strftime(hyd.date, "%Y_%m_%d")
        flac_dir = Path.cwd() / f"data/flac/{date_str}/{hyd.refdes[18:]}"
        png_dir = Path.cwd() / f"data/png/{date_str}/{hyd.refdes[18:]}"
        wav_dir = Path.cwd() / f"data/wav/{date_str}/{hyd.refdes[18:]}"

        flac_dir.mkdir(parents=True, exist_ok=True)
        png_dir.mkdir(parents=True, exist_ok=True)
        wav_dir.mkdir(parents=True, exist_ok=True)

        for st in hyd.clean_list:
            if (
                st is not None
            ):  # TODO as of now we are throwing out 5 minute segments with gaps > fudge factor
                start_time = str(st[0].stats["starttime"])
                dt = datetime.strptime(start_time, "%Y-%m-%dT%H:%M:%S.%fZ")

                new_format = dt.strftime("%Y%m%d_%H%M%S")  # dt.strftime("%y%m%d%H%M%S%z")

                if format == "FLOAT":
                    st[0].data = st[0].data.astype(np.float64)

                if normalize_traces:
                    st = st.normalize()

                print(type(st[0].data[0]))
                print(st[0].data[:5])

                flac_path = flac_dir / f"{hyd_refdes[-9:]}_{new_format}.flac"
                wav_path = wav_dir / f"{hyd_refdes[-9:]}_{new_format}.wav"

                print(str(flac_path))
                sf.write(
                    flac_path, st[0].data, sr, subtype=format
                )  # use sf package to write instead of obspy
                if write_wav:
                    print(str(wav_path))
                    sf.write(
                        wav_path, st[0].data, sr, subtype=format
                    )  # use sf package to write instead of obspy

        return hyd, png_dir, date_str


@task
def compare_flac_wav(hyd_refdes, format, hyd, png_dir, date_str):
    logger = select_logger()

    logger.info("Some flac/wav comparisions:")
    example_datetime = hyd.clean_list[1][
        0
    ].stats.starttime  # use 2nd element because 1st is more often truncated
    example_time = example_datetime.strftime("%Y%m%d_%H%M%S")
    logger.info(f"Using {example_time} for logging and sanity checking")

    if format == "FLOAT":
        dtype = "float64"
    else:
        dtype = "int32"

    wav, _ = sf.read(
        f"data/wav/{date_str}/{hyd_refdes[18:]}/{hyd_refdes[18:]}_{example_time}.wav",
        dtype=dtype,
    )
    logger.info(f"wav data sanity check {wav}")

    flac, _ = sf.read(
        f"data/flac/{date_str}/{hyd_refdes[18:]}/{hyd_refdes[18:]}_{example_time}.flac",
        dtype=dtype,
    )
    logger.info(f"flac data sanity check {flac}")

    wavflac_ratio = wav / flac
    logger.info(f"wav/flac ratio: {wavflac_ratio}")

    logger.info("saving some comparison plots")
    plt.plot(wav[:200], linewidth=0.5)
    plt.plot(flac[:200], linewidth=0.5)

    compare_path = png_dir / f"{hyd.file_str}_flacwav_compare.png"
    plt.savefig(compare_path, dpi=300, bbox_inches="tight")
    plt.close()

    plt.plot(wav[:200] - flac[:200])
    diff_path = png_dir / f"{hyd.file_str}_flacwav_diff.png"
    plt.savefig(diff_path, dpi=300, bbox_inches="tight")
    plt.close()


@flow(log_prints=True)
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
    logger = select_logger()

    if stages == "audio" or stages == "all":
        hyd, png_dir, date_str = convert_mseed_to_audio(
            hyd_refdes=hyd_refdes,
            date=date,
            sr=sr,
            format=format,
            normalize_traces=normalize_traces,
            fudge_factor=fudge_factor,
            write_wav=write_wav,
        )
        if hyd is None:
            logger.warning(f"No data availale for {date}. Moving to next day.")
            return

        # first element of list is different each time due to multithreading - could add sort step?
        logger.info(f"first 5 elements of cleaned mseed list: {hyd.clean_list[:5]}")

        if write_wav:
            compare_flac_wav(hyd_refdes, format, hyd, png_dir, date_str)

    if stages == "viz" or stages == "all":
        audio_to_spec(date, "flac", hyd_refdes, apply_cals)

    if s3_sync:
        sync_png_nc_to_s3(hyd_refdes, date)


if __name__ == "__main__":
    acoustic_flow_oneday()

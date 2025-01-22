import fsspec
import concurrent.futures
import obspy as obs
import numpy as np
import multiprocessing as mp
import soundfile as sf
import matplotlib.pyplot as plt
import click

from datetime import datetime
from tqdm import tqdm
from pathlib import Path
from loguru import logger

"""
This script converts OOI hydrophone data stored as mseed files on the OOI raw data archive 
into 5 minute wav and then flac files using obspy and soundfile. Wav file names are written to "./acoustic/wav/YYYY_MM_DD".
Files are named in the datetime format "YYMMDDHHMMSS"
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
WAV_DATA_SUBTYPE
    'PCM_32' or 'FLOAT' The data subtype format for the resulting WAV files. OOI data is int32, 
     but some media players cannot import in this format. See `sf.available_subtypes('WAV')`
NORMALIZE_TRACES
    Option to normalize signal by mean of each 5 minute trace. If normalized float32 data type is needed.
"""

# HYD_REFDES = "CE04OSBP-LJ01C-11-HYDBBA105"
# DATE = "2025/01/16"
# SR = 64000
# WAV_DATA_SUBTYPE = "FLOAT" #"PCM_24"#'PCM_32'  #audacity uses normalized float
# NORMALIZE_TRACES = False # True if you want to use float32 for audacity
# FUDGE_FACTOR = 0.02


def _map_concurrency(func, iterator, args=(), max_workers=-1, verbose=False):
    # automatically set max_workers to 2x(available cores)
    if max_workers == -1:
        max_workers = 2 * mp.cpu_count()

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Start the load operations and mark each future with its URL
        future_to_url = {executor.submit(func, i, *args): i for i in iterator}
        # Disable progress bar
        is_disabled = not verbose
        for future in tqdm(
            concurrent.futures.as_completed(future_to_url), total=len(iterator), disable=is_disabled
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
        data=None,
        mseed_urls=None,
        clean_list=None,
        stream=None,
        spec=None,
    ):
        self.refdes = refdes
        self.date = datetime.strptime(str_date, "%Y/%m/%d")
        self.fudge_factor = fudge_factor
        self.data = data
        self.mseed_urls = self.get_mseed_urls(str_date, refdes)
        self.clean_list=clean_list
        self.stream=stream
        self.spec=spec
        self.file_str = f"{self.refdes}_{self.date.strftime('%Y_%m_%d')}"


    def get_mseed_urls(self, day_str, refdes):

        base_url = "https://rawdata.oceanobservatories.org/files"
        mainurl = f"{base_url}/{refdes[0:8]}/{refdes[9:14]}/{refdes[15:27]}/{day_str}/"
        FS = fsspec.filesystem("http")
        print(mainurl)
    
        try:
            data_url_list = sorted(
                f["name"]
                for f in FS.ls(mainurl)
                if f["type"] == "file" and f["name"].endswith(".mseed")
            )
        except Exception as e:
            print("Client response: ", e)
            return None
    
        if not data_url_list:
            print("No Data Available for Specified Time")
            return None
    
        return data_url_list

    def read_and_repair_gaps(self, wav_data_subtype):
        self.clean_list = _map_concurrency(
            func=self._deal_with_gaps_and_overlaps, 
            args=(wav_data_subtype), 
            iterator=self.mseed_urls, verbose=False
        )
        
            
    def _merge_by_timestamps(self, st):
        cs = st.copy()
        
        data = []
        for tr in cs:
            data.append(tr.data)
        data_cat = np.concatenate(data)
    
        stats = dict(cs[0].stats)
        stats["starttime"] = st[0].stats["starttime"]
        stats["endtime"] = st[-1].stats["endtime"]
        stats["npts"] = len(data_cat)
    
        cs = obs.Stream(traces=obs.Trace(data_cat, header=stats))
    
        return cs
        

    def _deal_with_gaps_and_overlaps(self, url, wav_data_subtype):
        if wav_data_subtype not in ["PCM_32", "PCM_24", "FLOAT"]:
            raise ValueError("Invalid wav data subtype. Please specify 'PCM_32' or 'FLOAT'")
        # first read in mseed
        if wav_data_subtype == "PCM_32" or wav_data_subtype == "PCM_24":
            st = obs.read(url, apply_calib=False, dtype=np.int32)
        if wav_data_subtype == "FLOAT":
            st = obs.read(url, apply_calib=False, dtype=np.float64)
        
        
        trace_id = st[0].stats["starttime"]
        print("total traces before concatenation: " + str(len(st)), flush=True)
        # if 19.2 samples +- 640 then concat
        samples = 0
        for trace in st:
            samples += len(trace)
            
        if 19199360 <= samples <= 19200640: # CASE A: just jitter, no true gaps
            print(f"There are {samples} samples in this stream, Simply concatenating")
            cs = self._merge_by_timestamps(st)
            print("total traces after concatenation: " + str(len(cs)))
        else:
            print(f"{trace_id}: there are a unexpected number of samples in this file. Checking for large gaps:")
            gaps = st.get_gaps()
            st_contains_large_gap = False
            # loop checks for large gaps
            for gap in gaps:
                if abs(gap[6]) > self.fudge_factor: # the gaps 6th element is the gap length 
                    st_contains_large_gap = True
                    break
            
            if st_contains_large_gap: # CASE B: - edge case? - LARGE GAPS WILL RAISE ERROR
                raise ValueError(f"{trace_id}: This file contains large gaps. Cannot repair with currently impliimented methods")
            else: # CASE C: shortened trace before divert with no large gaps
                print(f"{trace_id}: This file is short but only contains jitter. Simply concatenating")
                cs = self._merge_by_timestamps(st)
                print("total traces after concatenation: " + str(len(cs)), flush=True)
        return cs
    
def convert_mseed_to_wav(
    hyd_refdes,
    date,
    fudge_factor,
    sr,
    wav_data_subtype,
    normalize_traces,
):
    logger.warning(wav_data_subtype)
    hyd = HydrophoneDay(hyd_refdes, date, fudge_factor)

    hyd.read_and_repair_gaps(wav_data_subtype=wav_data_subtype)

    # make dirs 
    date_str = datetime.strftime(hyd.date, "%Y_%m_%d")
    wav_dir = Path(f'./acoustic/wav/{date_str}')
    wav_dir.mkdir(parents=True, exist_ok=True)

    for st in hyd.clean_list:
        start_time = str(st[0].stats['starttime'])
        end_time = str(st[0].stats['endtime'])
        dt = datetime.strptime(start_time, "%Y-%m-%dT%H:%M:%S.%fZ")
        
        new_format = dt.strftime("%Y%m%d_%H%M%S")#dt.strftime("%y%m%d%H%M%S%z")

        if wav_data_subtype == 'FLOAT':
            st[0].data = st[0].data.astype(np.float64) 
            
        if normalize_traces:
            st = st.normalize()
            
        print(type(st[0].data[0]))

        wav_path = wav_dir / f"{hyd_refdes[-9:]}_{new_format}.wav"
        print(str(wav_path))

        sf.write(wav_path, st[0].data, sr, subtype=wav_data_subtype) # use sf package to write instead of obspy

    return hyd


@click.command()
@click.option(
    "--hyd-refdes", 
    type=str, 
    required=True, 
    help="Hydrophone reference designator (e.g., 'CE04OSBP-LJ01C-11-HYDBBA105')."
)
@click.option(
    "--date", 
    type=str, 
    required=True, 
    help="Date in the format YYYY/MM/DD (e.g., '2025/01/16')."
)
@click.option(
    "--sr", 
    type=int, 
    default=64000, 
    show_default=True, 
    help="Sample rate in Hz (e.g., 64000)."
)
@click.option(
    "--wav-data-subtype", 
    type=click.Choice(["FLOAT", "PCM_24", "PCM_32"], case_sensitive=False), 
    default="FLOAT", 
    show_default=True, 
    help="WAV data subtype (FLOAT, PCM_24, or PCM_32)."
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
    help="Fudge factor (e.g., 0.02)."
)
def main(hyd_refdes, date, sr, wav_data_subtype, normalize_traces, fudge_factor):

    hyd = convert_mseed_to_wav(
        hyd_refdes=hyd_refdes,
        date=date,
        sr=sr,
        wav_data_subtype=wav_data_subtype,
        normalize_traces=normalize_traces,
        fudge_factor=fudge_factor
    )

    logger.info(f"first 5 elements of cleaned mseed list: {hyd.clean_list[:5]}")

    # make dirs 
    logger.info(f"Creating directories for flac and wav files")
    date_str = datetime.strftime(hyd.date, "%Y_%m_%d")
    flac_dir = Path(f'./acoustic/flac/{date_str}')
    wav_dir = Path(f'./acoustic/wav/{date_str}')
    png_dir = Path(f'./acoustic/png/{date_str}')
    flac_dir.mkdir(parents=True, exist_ok=True)
    wav_dir.mkdir(parents=True, exist_ok=True)
    png_dir.mkdir(parents=True, exist_ok=True)

    # first element of list is different each time due to multithreading - could add sort step?
    example_datetime = hyd.clean_list[1][0].stats.starttime # use 2nd element because 1st is more often truncated
    example_time = example_datetime.strftime("%Y%m%d_%H%M%S")
    logger.info(f"Using {example_time} for logging and sanity checking")

    wav, _ = sf.read(f'acoustic/wav/{date_str}/{hyd_refdes[18:]}_{example_time}.wav', dtype="float") #TODO hyd shouldnt be hardcoded
    logger.info(f"wav data sanity check {wav}")

    # soundfile PCM_24 FLAC
    for wav_path in wav_dir.glob('*.wav'):
        data, sr = sf.read(wav_path, dtype='int32')
        
        flac_path = flac_dir / wav_path.with_suffix('.flac').name
        
        sf.write(flac_path, data, sr, subtype="PCM_24")
        logger.info(f'Converted {wav_path} to {flac_path}')

    flac, _ = sf.read(f'acoustic/flac/{date_str}/{hyd_refdes[18:]}_{example_time}.flac', dtype="float")
    logger.info(f"flac data sanity check {flac}")

    wavflac_ratio = wav / flac
    logger.info(f"wav/flac ratio: {wavflac_ratio}")

    logger.info("saving some comparison plots")
    plt.plot(wav[:200], linewidth=0.5)
    plt.plot(flac[:200], linewidth=0.5)

    compare_path = png_dir / f'{hyd.file_str}_flacwav_compare.png'
    plt.savefig(compare_path, dpi=300, bbox_inches='tight')
    plt.close()  

    plt.plot(wav[:200] - flac[:200])
    diff_path = png_dir / f'{hyd.file_str}_flacwav_diff.png'
    plt.savefig(diff_path, dpi=300, bbox_inches='tight')
    plt.close()

if __name__ == "__main__":
    main()

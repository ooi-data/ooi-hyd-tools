import fsspec
import concurrent.futures
import json
import obspy as obs
import numpy as np
import multiprocessing as mp
import requests
import soundfile as sf
import time
import matplotlib.pyplot as plt

from contextvars import copy_context

from datetime import datetime
from tqdm import tqdm
from pathlib import Path
from prefect import task, flow
from importlib.metadata import distributions, version

from ooi_hyd_tools.audio_to_spec import audio_to_spec
from ooi_hyd_tools.low_freq import run_low_freq_oneday
from ooi_hyd_tools.cloud import sync_png_nc_to_s3
from ooi_hyd_tools.utils import select_logger
from ooi_hyd_tools.seismometer import run_obs_viz


"""
Convert a day of OOI hydrophone mseed from the raw data archive into 5-minute FLAC,
written to ./data/flac/YYYY_MM_DD/INSTRUMENT/ as INSTRUMENT_YYYYMMDD_HHMMSS.flac.

Two things make this more than a format conversion:

Timestamps cannot be trusted. The DDS mislabels when each burst of samples arrived - by
anywhere from a fraction of a millisecond to a quarter second, differing by instrument and
by year - but it never moves or duplicates recording. So the sample count decides whether
anything is actually missing, and timestamps are only consulted afterwards to locate a
real break. See _deal_with_gaps_and_overlaps for the three cases (OEK HYDBB flow chart).

Counts must survive the write. mseed carries 24-bit ADC counts that the calibration chain
depends on, and libsndfile would truncate them; see INT24_SHIFT.

Filenames carry whole seconds only, because that is all mbari-pbp can parse. The exact
start, including sub-second, goes in the file header - see _write_audio.

"""

# mseed counts are 24-bit right-justified in int32 (full scale 2**23); libsndfile's int32
# API is left-justified (full scale 2**31), so writing counts straight to PCM_24 stores
# count >> 8. Shifting left by 8 first cancels that.
INT24_SHIFT = 8

# CI writes mseed in 5-min files on fixed startpoints (00:00, 00:05, ...), so a complete
# file holds NOMINAL_SECONDS * sampling_rate samples - 19,200,000 at 64 kHz.
NOMINAL_SECONDS = 300

# How far short of a full 5 minutes the sample count may fall and still count as complete.
# CI's trace packing often leaves a partial burst at one end, so files are not always exactly
# 19,200,000;
# (OEK TrHld1)
COUNT_THRESHOLD = 0.01

# How long a gap must be before it means recording was genuinely lost rather than
# mislabelled. Only consulted once the sample count says something is missing.
# (OEK TrHld2; the default for --gap-threshold)
GAP_THRESHOLD = 0.023

# == runtime ==
# The raw data server drops connections mid-transfer when it is busy. Retry the one file
# rather than letting it fail the task, which would re-download the whole day.
READ_ATTEMPTS = 4
RETRY_WAIT = 5  # seconds, doubled each attempt


def _map_concurrency(func, iterator, args=(), max_workers=-1, verbose=False):
    # automatically set max_workers to 2x(available cores)
    if max_workers == -1:
        max_workers = min(24, 2 * mp.cpu_count())
        select_logger().debug(f"Max workers: {max_workers}")

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Start the load operations and mark each future with its URL. Each worker runs in
        # a copy of the caller's context so it keeps the prefect run logger - without this
        # a worker's logging has no run to attach to and never reaches the flow run.
        future_to_url = {
            executor.submit(copy_context().run, func, i, args): i for i in iterator
        }
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


def _write_audio(path, data, sr, subtype, refdes, start, npts):
    """write one audio file, stamping the exact start time into the header.

    Filenames only carry whole seconds, because that is the finest resolution mbari-pbp
    can parse (meta_gen/utils.py matches {prefix}_YYYYMMDD_HHMMSS. and nothing after).
    A recording that resumes mid-second therefore loses up to 1 s in its name, so the
    true start goes in the header where nothing truncates it - Vorbis comment for FLAC,
    LIST/INFO chunk for WAV.
    """
    with sf.SoundFile(
        str(path),
        "w",
        samplerate=sr,
        channels=1,
        subtype=subtype,
        format=path.suffix[1:].upper(),
    ) as f:
        f.date = str(start)  # exact, sub-second; the filename is rounded
        f.software = f"ooi-hyd-tools {version('ooi-hyd-tools')}"
        # counts= says what the integers mean, for readers who have no changelog to consult
        f.comment = (
            f"refdes={refdes} start={start} npts={npts} sampling_rate={sr} "
            f"counts=int24_left_justified"
        )
        f.write(data)


class HydrophoneDay:
    def __init__(
        self,
        refdes,
        str_date,
        gap_threshold,
        clean_list=None,
    ):
        self.refdes = refdes
        self.date = datetime.strptime(str_date, "%Y/%m/%d")
        self.gap_threshold = gap_threshold
        self.mseed_urls = self.get_mseed_urls(str_date, refdes)
        self.clean_list = clean_list
        self.file_str = f"{self.refdes}_{self.date.strftime('%Y_%m_%d')}"

    def get_mseed_urls(self, day_str, refdes):
        logger = select_logger()
        base_url = "https://rawdata.oceanobservatories.org/files"
        mainurl = f"{base_url}/{refdes[0:8]}/{refdes[9:14]}/{refdes[18:27]}/{day_str}/"
        FS = fsspec.filesystem("http")
        logger.info(mainurl)
        logger.debug(Path.cwd())

        try:
            data_url_list = sorted(
                f["name"]
                for f in FS.ls(mainurl)
                if f["type"] == "file" and f["name"].endswith(".mseed")
            )

            try:
                addendum_list = sorted(
                    f["name"]
                    for f in FS.ls(f"{mainurl}/addendum")
                    if f["type"] == "file" and f["name"].endswith(".mseed")
                )
            except Exception:
                logger.info(f"No addendum for {day_str}")
                addendum_list = []

        except Exception as e:
            logger.warning(f"Client response: {e}")
            return None

        data_url_list.extend(addendum_list)

        if not data_url_list:
            logger.warning("No Data Available for Specified Time")
            return None

        return data_url_list

    def read_and_repair_gaps(self):
        if self.mseed_urls is None:
            return None
        self.clean_list = _map_concurrency(
            func=self._deal_with_gaps_and_overlaps,
            iterator=self.mseed_urls,
            verbose=False,
        )

    def _read_mseed(self, url):
        """download one file, retrying transient server errors.

        The OOI raw data server truncates transfers under load (IncompleteRead). Retrying
        the single file keeps one blip from failing the task and re-downloading the whole
        day. A file that never arrives returns None and is reported, so the day still
        produces audio for everything else.
        """
        logger = select_logger()
        for attempt in range(1, READ_ATTEMPTS + 1):
            try:
                # always int32: the writer needs integer counts to left-justify
                return obs.read(url, apply_calib=False, dtype=np.int32)
            except (requests.exceptions.RequestException, OSError) as e:
                if attempt == READ_ATTEMPTS:
                    logger.warning(
                        f"ISSUE {url}: no data after {READ_ATTEMPTS} attempts "
                        f"({type(e).__name__}); this 5 min is missing from the day"
                    )
                    return None
                wait = RETRY_WAIT * 2 ** (attempt - 1)
                logger.info(
                    f"{url}: {type(e).__name__}, retrying in {wait}s "
                    f"({attempt}/{READ_ATTEMPTS - 1})"
                )
                time.sleep(wait)

    def _nominal_start(self, url):
        """the fixed 5-min startpoint this file belongs to, per its CI filename.
        Lets a rename upstream blow up here - every file's placement depends on it."""
        return obs.UTCDateTime(url.split("YDH-")[-1].replace(".mseed", ""))

    def _start_for(self, actual, nominal, label_noise):
        """OEK: keep the fixed 5-min startpoint unless the data really does begin elsewhere.
        A label within this file's own timestamp noise of the startpoint is
        indistinguishable from jitter, so it snaps to the grid."""
        return nominal if abs(actual - nominal) <= max(self.gap_threshold, label_noise) else actual

    def _rejoin_traces(self, traces, starttime):
        """glue contiguous traces back into one, placed at the caller's starttime.

        The samples are simply concatenated in order - they were never actually broken
        apart, only relabelled. Metadata (network, station, channel, sampling rate) is
        inherited from the first trace, but its starttime is overwritten rather than
        inherited, because that value is exactly what we do not trust.

        The caller decides what to put there, via _start_for: normally `nominal`, the
        5-minute startpoint from CI's filename, and only the trace's own starttime when
        the data demonstrably begins somewhere else. Setting starttime and npts fixes the
        whole span - obspy derives endtime from starttime + npts / sampling_rate, and
        ignores any endtime handed to it.
        """
        data = np.concatenate([tr.data for tr in traces])
        stats = dict(traces[0].stats)
        stats["starttime"] = starttime
        stats["npts"] = len(data)
        return obs.Trace(data, header=stats)

    def _deal_with_gaps_and_overlaps(self, url, args=()):
        """Repair one 5-minute mseed file, following the OEK HYDBB flow chart.

        The hydrophone's timestamps are unreliable - the DDS mislabels when each burst of
        samples arrived, by anywhere from a fraction of a millisecond to a quarter second,
        and the pattern differs between instruments and between years. It never moves or
        duplicates data; only the labels are wrong. So the number of samples in the file,
        not the timestamps, tells us whether any recording is actually missing.

        That gives three cases, below. Returns one trace per unbroken stretch of
        recording - normally just one covering the whole 5 minutes.
        """
        logger = select_logger()
        st = self._read_mseed(url)
        if st is None:
            return None
        nominal = self._nominal_start(url)

        sr = st[0].stats.sampling_rate
        expected = round(NOMINAL_SECONDS * sr)  # samples in a full 5 minutes
        tol = round(COUNT_THRESHOLD * sr)  # slack on the count, not on the timestamps
        samples = sum(tr.stats.npts for tr in st)

        deltas = self._gap_samples(st, sr)
        label_noise_s = self._label_noise(deltas, sr)

        # =====================================================================
        # CASE C - TOO MUCH DATA
        # More samples than five minutes can hold. Should not happen; keep the
        # recording and flag it so someone looks.
        # =====================================================================
        if samples > expected + tol:
            logger.warning(
                f"ISSUE {nominal}: CASE C, {samples} samples is more than the "
                f"{expected} in a 5-min file. Keeping all of it, please investigate."
            )
            return obs.Stream(traces=[self._rejoin_traces(st.traces, nominal)])

        # =====================================================================
        # CASE A - COMPLETE FILE (the normal case)
        # All five minutes of recording are present, so nothing is missing and
        # every apparent gap is just a mislabelled timestamp. Join the pieces
        # back together and keep the file on its 5-minute startpoint.
        # =====================================================================
        if samples >= expected - tol:
            logger.info(f"{nominal}: CASE A, {len(st)} pieces rejoined, 5 min intact")
            start = self._start_for(st[0].stats.starttime, nominal, label_noise_s)
            return obs.Stream(traces=[self._rejoin_traces(st.traces, start)])

        # =====================================================================
        # CASE B - RECORDING IS GENUINELY MISSING
        # Fewer samples than five minutes holds, so the instrument stopped or the
        # data was diverted. Only now do the timestamps get consulted, to find
        # where. Split there and write each surviving stretch as its own file
        # (OEK: no padding)
        #
        # Six traces, jittered starttimes, and one real 5 s break after trace 2:
        #
        #   trace     0     1     2  |  3     4     5
        #   delta       -640  +640   | +320000  -640  +640
        #                            |
        #                            `- the only delta over break_floor
        #
        #   break_floor = max(0.023, label_noise 0.010) * 64000 = 1472 samples
        #   real        = [2]           the one boundary that is a genuine break
        #   bounds      = [0, 3, 6]     slice points, so traces 0:3 and 3:6
        #   chunks      = 2 traces out, one per surviving stretch
        #
        # The first chunk keeps the nominal 5-min startpoint; the second is named from
        # its own data, since it demonstrably begins after the break. deficit is 320000
        # samples (5 s) and that one break explains all of it, so no ISSUE is logged.
        # =====================================================================
        deficit = expected - samples
        break_floor = round(max(self.gap_threshold, label_noise_s) * sr)
        real = np.flatnonzero(deltas > break_floor)
        bounds = [0, *(real + 1), len(st)]
        chunks = [
            self._rejoin_traces(
                st.traces[a:b], self._start_for(st[a].stats.starttime, nominal, label_noise_s)
            )
            for a, b in zip(bounds, bounds[1:])
        ]

        explained = int(deltas[real].sum())
        logger.info(
            f"{nominal}: CASE B, missing {deficit / sr:.2f}s of recording; "
            f"{len(real)} break(s) account for {explained / sr:.2f}s "
            f"(label noise here {label_noise_s * 1000:.0f} ms); writing {len(chunks)} file(s)"
        )
        if abs(explained - deficit) > tol:
            logger.warning(
                f"ISSUE {nominal}: {(deficit - explained) / sr:.2f}s of the missing "
                f"recording is unaccounted for - either the file starts late or ends "
                f"early, or breaks are smaller than {max(self.gap_threshold, label_noise_s)}s"
            )
        return obs.Stream(traces=chunks)

    def _label_noise(self, deltas, sr):
        """seconds by which this file's trace starttimes are known to be wrong.

        A trace claiming to begin before the previous one ended cannot be real - one ADC
        cannot record an instant twice - so any overlap is proof of a bad starttime, and
        the largest one measures how bad this file gets. Observed from 10 ms up to 250 ms
        depending on instrument and era. Used to widen both the real-break test and the
        filename test, which would otherwise shred a noisy file into dozens of chunks.

        Scores 0 for a file with no overlaps, or one with a single trace and so no
        boundaries to measure.

        deltas come in samples and run negative for an overlap, so the biggest overlap is
        the most negative value: [+640, -640] -> worst = -640. Negating turns that into a
        magnitude (640 samples) and dividing by the sample rate puts it in seconds, which
        is what the callers compare against gap_threshold. So -640 at 64 kHz -> 0.01 s.
        """
        worst = int(deltas.min(initial=0))  # most negative delta = biggest overlap
        return -min(worst, 0) / sr  # -> positive magnitude, samples -> seconds

    def _gap_samples(self, st, sr):
        """how many samples each starttime claims are missing between one trace and the
        next. Positive means a possible break; negative is an overlap, which is always a
        mislabelled starttime rather than repeated recording.

        Three traces of 16000 samples at 64 kHz, so each covers 0.25 s. Compare where a
        trace should begin (previous starttime + its length) against where it says it does:

            trace  starttime   should begin   claims       delta
            0      0.00        -              -            -
            1      0.26        0.25           0.01 s late  +640
            2      0.50        0.51           0.01 s early -640

        returns [+640, -640] - one value per boundary, so len(st) - 1 of them, and an
        empty array for a single-trace file. Here the two cancel, which is the signature
        of jitter: the starttimes wobble but no recording is missing.
        """
        starts = np.array([tr.stats.starttime.timestamp for tr in st])
        npts = np.array([tr.stats.npts for tr in st])
        return np.rint((starts[1:] - (starts[:-1] + npts[:-1] / sr)) * sr).astype(int)


@task(retries=2, retry_delay_seconds=60)
def convert_mseed_to_audio(
    hyd_refdes,
    date,
    gap_threshold,
    format,
    normalize_traces,
    write_wav,
):
    logger = select_logger()
    hyd = HydrophoneDay(hyd_refdes, date, gap_threshold)

    hyd.read_and_repair_gaps()

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

        written = 0
        seen = {}  # stamp -> (npts, start, sr), to catch two pieces landing in one second
        collisions = []  # kept for the manifest; otherwise this evidence only ever gets logged
        for st in hyd.clean_list:
            if st is None:
                continue
            for tr in st:  # normally one chunk; more when a real gap split the file
                sr = int(tr.stats["sampling_rate"])
                start = tr.stats["starttime"]
                npts = tr.stats["npts"]
                stamp = start.strftime("%Y%m%d_%H%M%S")  # whole seconds: pbp parses no finer

                # CI occasionally chops one 5-min window into pieces starting ~100 ms
                # apart, which round to the same filename. Keep the longer piece and say
                # so, rather than silently overwriting.
                if stamp in seen:
                    prev_npts, prev_start, _ = seen[stamp]
                    keep = npts > prev_npts
                    collisions.append(
                        {
                            "stamp": stamp,
                            "kept_s": round(max(npts, prev_npts) / sr, 3),
                            "dropped_s": round(min(npts, prev_npts) / sr, 3),
                            "dropped_start": str(prev_start if keep else start),
                        }
                    )
                    logger.warning(
                        f"ISSUE {hyd_refdes[-9:]}_{stamp}: two pieces start in the same "
                        f"second ({prev_start} and {start}); keeping the longer "
                        f"({max(npts, prev_npts) / sr:.2f}s over "
                        f"{min(npts, prev_npts) / sr:.2f}s)"
                    )
                    if not keep:
                        continue
                    written -= prev_npts / sr  # the file being replaced
                seen[stamp] = (npts, start, sr)

                flac_path = flac_dir / f"{hyd_refdes[-9:]}_{stamp}.flac"
                wav_path = wav_dir / f"{hyd_refdes[-9:]}_{stamp}.wav"

                logger.debug(str(flac_path))
                # counts are left-shifted so PCM_24 stores them intact, see INT24_SHIFT
                _write_audio(
                    flac_path, tr.data << INT24_SHIFT, sr, "PCM_24", hyd_refdes, start, npts
                )
                written += npts / sr

                if write_wav:
                    logger.debug(str(wav_path))
                    data = tr.data
                    if normalize_traces:  # listening copy only
                        data = data / np.abs(data).max()
                        _write_audio(wav_path, data, sr, "FLOAT", hyd_refdes, start, npts)
                    else:
                        _write_audio(wav_path, data, sr, format, hyd_refdes, start, npts)

        # A day is ~288 objects in S3 and is often partial, so a consumer has no way to
        # tell a recording gap from a failed upload. The manifest carries that, the exact
        # sub-second starts (filenames hold only whole seconds), and the collisions.
        manifest = {
            "refdes": hyd_refdes,
            "date": hyd.date.strftime("%Y-%m-%d"),
            "written_by": f"ooi-hyd-tools {version('ooi-hyd-tools')}",
            "counts": "int24_left_justified",
            "sampling_rate": next(iter(seen.values()))[2] if seen else None,
            "gap_threshold_s": gap_threshold,
            "source_mseed_files": len(hyd.mseed_urls),
            "files_written": len(seen),
            "seconds_written": round(written, 3),
            "day_coverage_pct": round(100 * written / 86400, 2),
            "collisions": collisions,
            "files": [  # sorted: seen is filled by a thread pool, so its order varies
                {"name": f"{hyd_refdes[-9:]}_{stamp}.flac", "start": str(start), "npts": npts}
                for stamp, (npts, start, _) in sorted(seen.items())
            ],
        }
        manifest_name = f"{hyd_refdes[-9:]}_{hyd.date.strftime('%Y%m%d')}_manifest.json"
        (flac_dir / manifest_name).write_text(json.dumps(manifest, indent=2))

        # what fraction of the day actually made it to disk
        logger.info(
            f"{hyd_refdes[-9:]} {date}: wrote {written / 3600:.2f}h of audio from "
            f"{len(hyd.mseed_urls)} mseed files ({100 * written / 86400:.1f}% of the day)"
        )
        return hyd, png_dir, date_str


@task  # TODO remove this once FLAC are being distributed or sooner
def compare_flac_wav(hyd_refdes, format, hyd, png_dir, date_str):
    logger = select_logger()

    logger.info("Some flac/wav comparisions:")
    starts = [tr.stats.starttime for cs in hyd.clean_list if cs is not None for tr in cs]
    if not starts:
        logger.warning("nothing was written, skipping the flac/wav comparison")
        return
    example_time = starts[0].strftime("%Y%m%d_%H%M%S")
    logger.info(f"Using {example_time} for logging and sanity checking")

    wav, _ = sf.read(
        f"data/wav/{date_str}/{hyd_refdes[18:]}/{hyd_refdes[18:]}_{example_time}.wav",
        dtype="int32",
    )
    logger.info(f"wav data sanity check {wav}")

    flac, _ = sf.read(
        f"data/flac/{date_str}/{hyd_refdes[18:]}/{hyd_refdes[18:]}_{example_time}.flac",
        dtype="int32",
    )
    flac = flac >> INT24_SHIFT  # undo the left-justification to recover raw counts
    logger.info(f"flac data sanity check {flac}")

    diff = wav - flac
    logger.info(f"wav - flac max abs difference: {np.abs(diff).max()} counts (expect 0)")

    logger.info("saving some comparison plots")
    plt.plot(wav[:200], linewidth=0.5)
    plt.plot(flac[:200], linewidth=0.5)

    compare_path = png_dir / f"{hyd.file_str}_flacwav_compare.png"
    plt.savefig(compare_path, dpi=300, bbox_inches="tight")
    plt.close()

    plt.plot(diff[:200])
    diff_path = png_dir / f"{hyd.file_str}_flacwav_diff.png"
    plt.savefig(diff_path, dpi=300, bbox_inches="tight")
    plt.close()


@flow(log_prints=True)
def acoustic_flow_oneday(
    hyd_refdes,
    date,
    format,
    normalize_traces,
    gap_threshold,
    write_wav,
    apply_cals,
    freq_lims,
    s3_sync,
    flag,
    obs_run_type,
):
    logger = select_logger()
    # log python package versions on cloud machine
    installed_packages = {dist.metadata["Name"]: dist.version for dist in distributions()}
    logger.info(f"Installed packages: {installed_packages}")

    if flag == "audio" or flag == "all":
        hyd, png_dir, date_str = convert_mseed_to_audio(
            hyd_refdes=hyd_refdes,
            date=date,
            format=format,
            normalize_traces=normalize_traces,
            gap_threshold=gap_threshold,
            write_wav=write_wav,
        )
        if hyd is None:
            logger.warning(f"No data availale for {date}. Moving to next day.")
            return

        # first element of list is different each time due to multithreading - could add sort step?
        logger.info(f"first 5 elements of cleaned mseed list: {hyd.clean_list[:5]}")

        if write_wav and not normalize_traces:
            compare_flac_wav(hyd_refdes, format, hyd, png_dir, date_str)

    if flag == "viz" or flag == "all":
        audio_to_spec(date, "flac", hyd_refdes, apply_cals, freq_lims)

    if flag == "low_freq":
        run_low_freq_oneday(hyd_refdes, date, logger)

    if flag == "obs":
        run_obs_viz(hyd_refdes, date, obs_run_type)

    if s3_sync:  # "spectrogram" (.nc + .png) or "all" (those plus flac)
        sync_png_nc_to_s3(hyd_refdes, date, flag, scope=s3_sync)


if __name__ == "__main__":
    acoustic_flow_oneday()

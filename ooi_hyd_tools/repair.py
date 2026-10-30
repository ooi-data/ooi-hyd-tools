"""
Fetch and repair OOI broadband hydrophone mseed, one 5-minute file at a time.

Timestamps cannot be trusted. The DDS mislabels when each burst of samples arrived - by
anywhere from a fraction of a millisecond to a quarter second, differing by instrument and
by year - but it never moves or duplicates recording. So the sample count decides whether
anything is actually missing, and timestamps are only consulted afterwards to locate a
real break. See HydrophoneDay._deal_with_gaps_and_overlaps for the three cases (OEK HYDBB
flow chart).

Needs only obspy, numpy, fsspec and requests, so lightweight consumers (the live stream)
can import it without the spectrogram stack.
"""
import concurrent.futures
import multiprocessing as mp
import time
from contextvars import copy_context
from datetime import datetime
from pathlib import Path

import fsspec
import numpy as np
import obspy as obs
import requests
from tqdm import tqdm

from ooi_hyd_tools.thresholds import COUNT_THRESHOLD
from ooi_hyd_tools.utils import select_logger

# CI writes mseed in 5-min files on fixed startpoints (00:00, 00:05, ...), so a complete
# file holds NOMINAL_SECONDS * sampling_rate samples - 19,200,000 at 64 kHz.
NOMINAL_SECONDS = 300

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



class HydrophoneDay:
    def __init__(
        self,
        refdes,
        str_date,
        gap_threshold,
        clean_list=None,
        mseed_root=None,
    ):
        self.refdes = refdes
        self.date = datetime.strptime(str_date, "%Y/%m/%d")
        self.gap_threshold = gap_threshold
        self.mseed_urls = (
            self.get_local_mseed_paths(str_date, refdes, Path(mseed_root).expanduser())
            if mseed_root
            else self.get_mseed_urls(str_date, refdes)
        )
        self.clean_list = clean_list
        self.file_str = f"{self.refdes}_{self.date.strftime('%Y_%m_%d')}"

    def get_local_mseed_paths(self, day_str, refdes, root):
        """list a day from a local mirror laid out as ROOT/<refdes>/YYYY/MM/DD/[addendum/]"""
        logger = select_logger()
        if not (root / refdes).is_dir():
            # an unmounted share looks exactly like a missing day, so fail instead of skipping
            raise FileNotFoundError(f"{root / refdes} not found - is the archive mounted?")
        day_dir = root / refdes / day_str
        logger.info(day_dir)

        paths = [str(p) for p in sorted(day_dir.glob("*.mseed"))]
        paths += [str(p) for p in sorted(day_dir.glob("addendum/*.mseed"))]
        if not paths:
            logger.warning(f"No local mseed for {day_str} in {day_dir}")
            return None
        return paths

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

        except FileNotFoundError:
            logger.warning(f"No directory on the raw data server for {day_str}")
            return None
        except Exception as e:
            # transient server/network failure - raise so the task retries instead of
            # silently writing off the day as "no data"
            raise RuntimeError(f"listing failed for {mainurl}") from e

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

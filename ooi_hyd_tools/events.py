import click
import numpy as np
import obspy as obs
import soundfile as sf

from pathlib import Path
from datetime import timedelta

from ooi_hyd_tools.mseed_to_audio import HydrophoneDay
from ooi_hyd_tools.utils import select_logger

"""
Pull a user-specified window of broadband hydrophone audio from the OOI raw data
archive, run it through the same jitter/gap repair used in mseed_to_audio, and
write a single continuous WAV to ./output/events for listening (e.g. a nearby
earthquake T-phase). Reuses HydrophoneDay for fetching + gap repair.

Example:
    event-audio --refdes RS01SLBS-LJ01A-09-HYDBBA102 \\
        --start 2026-06-29T11:50:00 --end 2026-06-29T12:20:00 \\
        --bandpass 5 500 --normalize --speed 4 --fade 1.0
"""

SEG_SECONDS = 300  # each raw mseed segment is 5 minutes
INT32_FULLSCALE = 2**31  # scale raw counts into [-1, 1]


def _select_urls(urls, start, end):
    """keep only the 5-min segments overlapping [start, end)"""
    keep = []
    for url in urls:
        seg_start = obs.UTCDateTime(url.split("YDH-")[-1].replace(".mseed", ""))
        if seg_start < end and seg_start + SEG_SECONDS > start:
            keep.append(url)
    return sorted(keep)


def _gather_window(refdes, start, end, fudge_factor, logger):
    """fetch + jitter-repair every segment overlapping the window, across day boundaries"""
    traces = []
    day = obs.UTCDateTime(start.date.year, start.date.month, start.date.day)
    while day < end:
        day_str = day.strftime("%Y/%m/%d")
        hyd = HydrophoneDay(refdes, day_str, fudge_factor)
        if hyd.mseed_urls:
            hyd.mseed_urls = _select_urls(hyd.mseed_urls, start, end)
            logger.info(f"{day_str}: {len(hyd.mseed_urls)} segments in window")
            hyd.read_and_repair_gaps(format="FLOAT")
            traces += [cs[0] for cs in hyd.clean_list if cs is not None]
        day += timedelta(days=1)
    return traces


@click.command()
@click.option("--refdes", required=True, help="Hydrophone reference designator.")
@click.option(
    "--start", required=True, help="Window start, ISO e.g. 2026-06-29T11:50:00 (UTC)."
)
@click.option("--end", required=True, help="Window end, ISO e.g. 2026-06-29T12:20:00 (UTC).")
@click.option(
    "--bandpass",
    type=(float, float),
    default=None,
    help="Bandpass filter LOW HIGH in Hz to isolate the event (e.g. 5 500).",
)
@click.option("--normalize", is_flag=True, help="Peak-normalize to 0.99 for a louder listen.")
@click.option("--gain-db", type=float, default=0.0, help="Additional gain in dB.")
@click.option(
    "--speed",
    type=float,
    default=1.0,
    show_default=True,
    help="Playback speed factor; rewrites the sample rate (no resampling) so rumble shifts up.",
)
@click.option(
    "--fade", type=float, default=0.0, help="Fade in/out taper in seconds at both ends."
)
@click.option("--fudge-factor", type=float, default=0.02, show_default=True)
def extract_event(refdes, start, end, bandpass, normalize, gain_db, speed, fade, fudge_factor):
    logger = select_logger()
    start, end = obs.UTCDateTime(start), obs.UTCDateTime(end)

    traces = _gather_window(refdes, start, end, fudge_factor, logger)
    if not traces:
        logger.warning("No data available in the requested window.")
        return

    st = obs.Stream(traces=traces)
    st.merge(method=1, fill_value=0)

    if bandpass:
        st.detrend("demean")
        st.filter(
            "bandpass", freqmin=bandpass[0], freqmax=bandpass[1], corners=4, zerophase=True
        )

    st.trim(start, end)
    tr = st[0]
    sr = int(round(tr.stats.sampling_rate))

    data = tr.data.astype(np.float64) / INT32_FULLSCALE
    if normalize:
        data = 0.99 * data / np.abs(data).max()
    if gain_db:
        data *= 10 ** (gain_db / 20)
    data = np.clip(data, -1.0, 1.0)

    if fade:
        n = int(fade * sr)
        ramp = np.linspace(0, 1, n)
        data[:n] *= ramp
        data[-n:] *= ramp[::-1]

    out_dir = Path.cwd() / "output/events"
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = f"_bp{int(bandpass[0])}-{int(bandpass[1])}" if bandpass else ""
    tag += f"_{speed:g}x" if speed != 1 else ""
    fpath = (
        out_dir
        / f"{refdes[-9:]}_{start.strftime('%Y%m%dT%H%M%S')}_{end.strftime('%H%M%S')}{tag}.wav"
    )

    out_sr = int(round(sr * speed))
    if out_sr > 192_000:
        logger.warning(
            f"Output sample rate is {out_sr} Hz; QuickTime/browsers/phones may refuse it. "
            f"Lower --speed or open in VLC/Audacity."
        )
    sf.write(fpath, data, out_sr, subtype="PCM_16")
    logger.info(f"Wrote {len(data) / out_sr:.1f}s ({out_sr} Hz playback) to {fpath}")


if __name__ == "__main__":
    extract_event()

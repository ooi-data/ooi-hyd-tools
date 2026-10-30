"""
Stream an OOI broadband hydrophone as Orcasound-style HLS, a fixed delay behind real time.

Mseed lands on the raw data server in 5-min files, minutes to hours after recording, so the
stream runs --delay-minutes behind: audio recorded at t plays at t + delay. Each slot is
repaired (repair.py), laid on a silent 5-min buffer so gaps stay gaps, gained for listening
and piped to ffmpeg, which writes orcanode's layout and encoding:

    OUT/<node_name>/latest.txt                          epoch of the current stream
    OUT/<node_name>/hls/<epoch>/live.m3u8, liveNNN.ts   10 s AAC segments

The epoch is the data time of the first slot rather than wall time, so an archive indexed
from it dates the audio correctly. With --bucket, files are uploaded the way orcanode's
upload_s3.py does; credentials come from the standard AWS env vars.

Levels are counts with a fixed gain and a soft limiter, for listening - not calibrated
pressure. Instruments differ by ~25 dB (2024-11-03 12:00 RMS: shelf -52, offshore -72, axial
-76 dBFS), so set --gain-db per hydrophone.
"""
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import click
import fsspec
import numpy as np
import obspy as obs

from ooi_hyd_tools.repair import NOMINAL_SECONDS, HydrophoneDay
from ooi_hyd_tools.thresholds import GAP_THRESHOLD
from ooi_hyd_tools.utils import select_logger

SR = 64000  # OOI broadband rate; also used for a slot with no data to read it from
FULL_SCALE = 2**23  # 24-bit counts
SEGMENT_SECONDS = 10  # orcanode's SEGMENT_DURATION
STREAM_RATE = 48000  # orcanode's STREAM_RATE
FETCH_LEAD = timedelta(seconds=60)  # start fetching a slot this long before it plays
PACIFIC = ZoneInfo("America/Los_Angeles")
TS_OFFSET = 1.4  # s; MPEG-TS starts the first segment here, so playback clock - this = data time


def slot_audio(refdes, slot, mseed_root):
    """5 min of counts from slot onward on a silent buffer, or None if the file isn't there yet"""
    hyd = HydrophoneDay(refdes, f"{slot:%Y/%m/%d}", GAP_THRESHOLD, mseed_root=mseed_root)
    name = f"{slot:%Y-%m-%dT%H:%M:%S}.000000Z.mseed"  # files sit on the 5-min grid by name
    url = next((u for u in hyd.mseed_urls or [] if u.endswith(name)), None)
    st = hyd._deal_with_gaps_and_overlaps(url) if url else None
    if st is None:
        return None

    buf = np.zeros(NOMINAL_SECONDS * SR, dtype=np.float32)
    t0 = obs.UTCDateTime(slot)
    for tr in st:  # one trace normally; more where repair found a real break
        a = max(0, round((tr.stats.starttime - t0) * SR))
        n = min(len(tr.data), len(buf) - a)
        if n > 0:
            buf[a : a + n] = tr.data[:n]
    return buf


def start_ffmpeg(hls_dir):
    """orcanode's hls-only ffmpeg call, reading float32 from stdin instead of a sound card"""
    hls_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [
            "ffmpeg", "-loglevel", "error",
            "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "pipe:0",
            "-f", "segment", "-segment_list", str(hls_dir / "live.m3u8"),
            "-segment_list_flags", "+live", "-segment_time", str(SEGMENT_SECONDS),
            "-segment_format", "mpegts", "-ar", str(STREAM_RATE), "-ac", "1", "-acodec", "aac",
            str(hls_dir / "live%03d.ts"),
        ],
        stdin=subprocess.PIPE,
    )  # fmt: skip


def upload(fs, bucket, out, hls_dir, latest, sent):
    """push finished segments, then the playlist and latest.txt, like orcanode's upload_s3.py"""
    playlist = hls_dir / "live.m3u8"
    if not playlist.exists():
        return
    # ffmpeg lists a segment only once it is closed, so these are safe to copy
    for name in (ln for ln in playlist.read_text().splitlines() if ln and not ln.startswith("#")):
        if name not in sent:
            fs.put(str(hls_dir / name), f"{bucket}/{(hls_dir / name).relative_to(out)}")
            sent.add(name)
    for p in (playlist, latest):
        fs.put(str(p), f"{bucket}/{p.relative_to(out)}")


def wait_until(t):
    time.sleep(max(0.0, (t - datetime.now(timezone.utc)).total_seconds()))


@click.command()
@click.option("--hyd-refdes", required=True, help="e.g. CE02SHBP-LJ01D-11-HYDBBA106")
@click.option("--node-name", required=True, help="Orcasound node name, e.g. ooi_oregon_shelf")
@click.option(
    "--delay-minutes",
    default=360,
    show_default=True,
    help="How far behind real time to play. A slot not on the server by then streams as silence.",
)
@click.option("--gain-db", default=40.0, show_default=True, help="Fixed listening gain; aim for ~-25 dBFS RMS.")
@click.option("--out-dir", default="./data/stream", show_default=True, type=click.Path(file_okay=False))
@click.option("--bucket", default=None, help="e.g. s3://audio-orcasound-net; omit to write locally only.")
@click.option("--mseed-root", default=None, help="Read a local mirror (ROOT/<refdes>/YYYY/MM/DD/) instead of the server.")
@click.option(
    "--start",
    default=None,
    help="Replay from this data time (YYYY-MM-DDTHH:MM, UTC) at real-time pace, for testing.",
)
def stream(hyd_refdes, node_name, delay_minutes, gain_db, out_dir, bucket, mseed_root, start):
    logger = select_logger()
    now = datetime.now(timezone.utc)
    slot = datetime.fromisoformat(start).replace(tzinfo=timezone.utc) if start else now - timedelta(minutes=delay_minutes)
    slot = slot.replace(minute=slot.minute - slot.minute % 5, second=0, microsecond=0)  # 5-min grid
    delay = now - slot if start else timedelta(minutes=delay_minutes)  # replay plays at once

    out = Path(out_dir).expanduser()
    epoch = int(slot.timestamp())
    hls_dir = out / node_name / "hls" / str(epoch)
    latest = out / node_name / "latest.txt"
    ffmpeg = start_ffmpeg(hls_dir)
    latest.write_text(f"{epoch}\n")
    fs, sent = (fsspec.filesystem("s3"), set()) if bucket else (None, None)
    gain = 10 ** (gain_db / 20) / FULL_SCALE
    logger.info(f"{node_name}: {hyd_refdes} from {slot:%Y-%m-%d %H:%M}Z, {delay} behind, to {hls_dir}")

    step = SEGMENT_SECONDS * SR
    while True:
        wait_until(slot + delay - FETCH_LEAD)
        counts = slot_audio(hyd_refdes, slot, mseed_root)
        if counts is None:
            logger.warning(f"{slot:%Y-%m-%d %H:%M}Z: no mseed yet, streaming silence")
            counts = np.zeros(NOMINAL_SECONDS * SR, dtype=np.float32)
        audio = np.tanh(counts * gain).astype(np.float32)  # soft limit: axial impulses reach -4 dBFS raw

        for i in range(0, len(audio), step):  # paced so segments appear at real-time rate
            wait_until(slot + delay + timedelta(seconds=i / SR))
            ffmpeg.stdin.write(audio[i : i + step].tobytes())
            if fs:
                upload(fs, bucket, out, hls_dir, latest, sent)
        slot += timedelta(seconds=NOMINAL_SECONDS)


@click.command()
@click.argument("node_name")
@click.option("--out-dir", default="./data/stream", show_default=True, type=click.Path(file_okay=False))
def listen(node_name, out_dir):
    """Play NODE_NAME's latest stream with ffplay, showing the UTC time of the audio."""
    node = Path(out_dir).expanduser() / node_name
    epoch = int((node / "latest.txt").read_text())
    ffplay = subprocess.Popen(
        ["ffplay", "-nodisp", "-hide_banner", "-loglevel", "error", "-stats",
         str(node / "hls" / str(epoch) / "live.m3u8")],
        stderr=subprocess.PIPE,
    )  # fmt: skip
    # ffplay's status line, one per \r, starts with the playback clock in seconds; segment 0
    # starts at MPEG-TS's 1.4 s offset, so the audio's data time is epoch + clock - 1.4
    buf = b""
    try:
        while chunk := ffplay.stderr.read1(256):
            *lines, buf = (buf + chunk).split(b"\r")
            clock = next((ln.split()[0] for ln in reversed(lines) if ln.split()[:1] != [b"nan"] and ln.split()), None)
            if clock:
                t = datetime.fromtimestamp(epoch + float(clock) - TS_OFFSET, timezone.utc)
                pt = t.astimezone(PACIFIC)
                print(
                    f"\r\033[K  \033[1m{node_name}\033[0m  \033[2m│\033[0m  "
                    f"{t:%a %b %d  %H:%M:%S} UTC  \033[2m│\033[0m  "
                    f"{pt:%a %b %d  %-I:%M:%S %p} {pt:%Z}",
                    end="", flush=True,
                )
    except KeyboardInterrupt:
        ffplay.terminate()
    print()


if __name__ == "__main__":
    stream()

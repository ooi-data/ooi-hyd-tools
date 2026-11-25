"""
Convert OOI broadband mseed to FLAC one file at a time, written next to each mseed.

Built to run at ingest: call it on each 5-minute file once it is closed, and on each
addendum file when it lands. The repair uses only the file itself (see repair.py), so a file
converted alone gives the same FLAC as the day pipeline.

    mseed-to-flac --refdes CE02SHBP-LJ01D-11-HYDBBA106 \\
        .../2026/11/14/OO-HYEA2--YDH-2026-11-14T07:30:00.000000Z.mseed

FLAC keep the server's naming, timed from their own first sample to the microsecond: a
complete file keeps its mseed's name, while one starting late is named from where its data
begins, and a real break in recording gives one FLAC per unbroken stretch. Counts are
stored intact (see INT24_SHIFT) with the exact start in the header (see write_audio).

Needs only the base install: pip install ooi-hyd-tools.
"""
from importlib.metadata import version
from pathlib import Path

import click
import obspy as obs
import soundfile as sf

from ooi_hyd_tools.repair import MseedRepair
from ooi_hyd_tools.thresholds import GAP_THRESHOLD, MIN_FILE_SECONDS
from ooi_hyd_tools.utils import select_logger

# mseed counts are 24-bit right-justified in int32 (full scale 2**23); libsndfile's int32
# API is left-justified (full scale 2**31), so writing counts straight to PCM_24 stores
# count >> 8. Shifting left by 8 first cancels that.
INT24_SHIFT = 8


def write_audio(path, data, sr, subtype, refdes, start, npts):
    """write one audio file, stamping the exact start time into the header.

    Filenames only carry whole seconds, because that is the finest resolution mbari-pbp
    can parse (meta_gen/utils.py matches {prefix}_YYYYMMDD_HHMMSS. and nothing after).
    A recording that resumes mid-second therefore loses up to 1 s in its name, so the
    true start goes in the FLAC's Vorbis comment, where nothing truncates it.

    Written to a .part file and renamed when complete, so a killed run never leaves a
    FLAC that won't decode.
    """
    part = path.with_name(f"{path.name}.part")
    with sf.SoundFile(
        str(part),
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
    part.replace(path)


def ooi_name(tr):
    """the raw data server's name for a trace, to the microsecond, without extension"""
    s = tr.stats
    stamp = s.starttime.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return f"{s.network}-{s.station}-{s.location}-{s.channel}-{stamp}"


def pbp_name(refdes, start):
    """pbp's name for a file, without extension: INSTRUMENT_YYYYMMDD_HHMMSS, whole seconds only,
    since that is all pbp parses"""
    return f"{refdes[-9:]}_{obs.UTCDateTime(start).strftime('%Y%m%d_%H%M%S')}"


def drop_stubs(pieces, min_seconds=MIN_FILE_SECONDS):
    """Split off pieces shorter than min_seconds: ms-long stubs CI leaves where it split a
    window into many files. pieces: [{start, npts, sampling_rate, ...}]. Returns (kept,
    stubs), stubs as [{start, seconds}] for the manifest."""
    logger = select_logger()
    kept, stubs = [], []
    for p in pieces:
        seconds = p["npts"] / p["sampling_rate"]
        if seconds >= min_seconds:
            kept.append(p)
            continue
        stubs.append({"start": str(p["start"]), "seconds": round(seconds, 6)})
        logger.warning(f"ISSUE {p['start']}: dropping a {p['npts']}-sample stub, under {min_seconds}s")
    return kept, stubs


def keep_longer(pieces, key):
    """CI occasionally chops one 5-min window into pieces starting ~100 ms apart, which round
    to the same whole-second pbp name. Keep the longer piece per key(piece) and record the
    other for the manifest, rather than silently overwriting. pieces: [{start, npts,
    sampling_rate, ...}]. Returns ({key: kept piece}, collisions)."""
    logger = select_logger()
    kept, collisions = {}, []
    for p in pieces:
        k = key(p)
        prev = kept.get(k)
        if prev:
            long, short = (p, prev) if p["npts"] > prev["npts"] else (prev, p)
            kept_s, dropped_s = (round(x["npts"] / x["sampling_rate"], 3) for x in (long, short))
            collisions.append(
                {"stamp": k, "kept_s": kept_s, "dropped_s": dropped_s, "dropped_start": str(short["start"])}
            )
            logger.warning(
                f"ISSUE {k}: two pieces start in the same second ({prev['start']} and "
                f"{p['start']}); keeping the longer ({kept_s:.2f}s over {dropped_s:.2f}s)"
            )
            p = long
        kept[k] = p
    return kept, collisions


def day_manifest(refdes, day, files, source_mseed_files, collisions, gap_threshold=None, stubs=None):
    """A day's coverage and file list. A day is ~288 files and often partial, so a consumer has
    no other way to tell a recording gap from a failed upload. files: [{name, start, npts,
    sampling_rate}]; collisions: pieces dropped because they start in the same second; stubs:
    pieces under MIN_FILE_SECONDS, not written (None where they can't be known)."""
    seconds = sum(f["npts"] / f["sampling_rate"] for f in files)
    return {
        "refdes": refdes,
        "date": day.strftime("%Y-%m-%d"),
        "written_by": f"ooi-hyd-tools {version('ooi-hyd-tools')}",
        "counts": "int24_left_justified",
        "sampling_rates": sorted({f["sampling_rate"] for f in files}),
        "gap_threshold_s": gap_threshold,
        "source_mseed_files": source_mseed_files,
        "files_written": len(files),
        "seconds_written": round(seconds, 3),
        "day_coverage_pct": round(100 * seconds / 86400, 2),
        "collisions": collisions,
        "stubs": stubs,
        "files": files,
    }


def convert_file(mseed, refdes, out_dir=None, gap_threshold=GAP_THRESHOLD):
    """repair one mseed and write its FLAC to out_dir, by default the mseed's own folder.
    Returns the paths written, empty if the file was all stub; None if it can't be read."""
    st = MseedRepair(gap_threshold)._deal_with_gaps_and_overlaps(str(mseed))
    if st is None:
        return None
    out_dir = Path(out_dir or Path(mseed).parent)
    out_dir.mkdir(parents=True, exist_ok=True)
    # one trace normally; more where repair found a real break
    pieces = [{"tr": tr, "start": tr.stats.starttime, "npts": tr.stats.npts, "sampling_rate": int(tr.stats.sampling_rate)} for tr in st]
    pieces, _ = drop_stubs(pieces)
    paths = []
    for pc in pieces:
        path = out_dir / f"{ooi_name(pc['tr'])}.flac"
        write_audio(path, pc["tr"].data << INT24_SHIFT, pc["sampling_rate"], "PCM_24", refdes, pc["start"], pc["npts"])
        paths.append(path)
    return paths


@click.command()
@click.argument("mseed", nargs=-1, required=True)
@click.option("--refdes", required=True, help="e.g. CE02SHBP-LJ01D-11-HYDBBA106, recorded in each FLAC header.")
@click.option("--out-dir", default=None, help="Write here instead of next to each mseed; needed for URLs.")
@click.option("--gap-threshold", default=GAP_THRESHOLD, show_default=True, help="Seconds a gap must exceed to count as lost recording.")
def cli(mseed, refdes, out_dir, gap_threshold):
    """Convert OOI broadband mseed files to FLAC, next to each mseed. Exits 1 if any file
    couldn't be read, so a caller can retry it."""
    if not out_dir and any("://" in m for m in mseed):
        raise click.UsageError("--out-dir is required for URLs")
    logger = select_logger()
    failed = []
    for m in mseed:
        paths = convert_file(m, refdes, out_dir, gap_threshold)
        if paths is None:
            failed.append(m)
            continue
        for p in paths:
            logger.info(f"wrote {p}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    cli()

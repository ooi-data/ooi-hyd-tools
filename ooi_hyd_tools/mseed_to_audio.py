import json
import soundfile as sf

from datetime import datetime
from pathlib import Path
from prefect import task, flow
from importlib.metadata import distributions, version

from ooi_hyd_tools.audio_to_spec import audio_to_spec
from ooi_hyd_tools.low_freq import run_low_freq_oneday
from ooi_hyd_tools.cloud import sync_png_nc_to_s3
from ooi_hyd_tools.repair import HydrophoneDay
from ooi_hyd_tools.utils import log_to_file, select_logger
from ooi_hyd_tools.seismometer import run_obs_viz


"""
Convert a day of OOI hydrophone mseed from the raw data archive into 5-minute FLAC,
written to ./data/flac/YYYY_MM_DD/INSTRUMENT/ as INSTRUMENT_YYYYMMDD_HHMMSS.flac.

Two things make this more than a format conversion:

Timestamps cannot be trusted, so each file is repaired first - see repair.py.

Counts must survive the write. mseed carries 24-bit ADC counts that the calibration chain
depends on, and libsndfile would truncate them; see INT24_SHIFT.

Filenames carry whole seconds only, because that is all mbari-pbp can parse. The exact
start, including sub-second, goes in the file header - see _write_audio.

flac_layout="ooi" instead mirrors the raw data server, for copying FLAC alongside the
mseed: ./data/flac/SITE/NODE/INSTRUMENT/YYYY/MM/DD/ with the server's file naming, e.g.
OO-HYEA2--YDH-2026-10-20T00:40:00.014000Z.flac. pbp can't read those names, so that
layout is for the audio stage only.

"""

# mseed counts are 24-bit right-justified in int32 (full scale 2**23); libsndfile's int32
# API is left-justified (full scale 2**31), so writing counts straight to PCM_24 stores
# count >> 8. Shifting left by 8 first cancels that.
INT24_SHIFT = 8

def _write_audio(path, data, sr, subtype, refdes, start, npts):
    """write one audio file, stamping the exact start time into the header.

    Filenames only carry whole seconds, because that is the finest resolution mbari-pbp
    can parse (meta_gen/utils.py matches {prefix}_YYYYMMDD_HHMMSS. and nothing after).
    A recording that resumes mid-second therefore loses up to 1 s in its name, so the
    true start goes in the FLAC's Vorbis comment, where nothing truncates it.
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


@task(retries=2, retry_delay_seconds=60)
def convert_mseed_to_audio(
    hyd_refdes,
    date,
    gap_threshold,
    mseed_root=None,
    flac_layout="pbp",
):
    logger = select_logger()
    hyd = HydrophoneDay(hyd_refdes, date, gap_threshold, mseed_root=mseed_root)

    hyd.read_and_repair_gaps()

    if hyd.clean_list is None:  # retun None if no data available on that day
        return None
    else:
        # make dirs
        logger.info("Creating data directories")
        date_str = datetime.strftime(hyd.date, "%Y_%m_%d")
        flac_dir = Path.cwd() / f"data/flac/{date_str}/{hyd.refdes[18:]}"
        if flac_layout == "ooi":
            r = hyd.refdes
            flac_dir = Path.cwd() / f"data/flac/{r[0:8]}/{r[9:14]}/{r[18:27]}/{date}"
        flac_dir.mkdir(parents=True, exist_ok=True)

        written = 0
        seen = {}  # stamp -> (npts, start, sr, name), to catch two pieces landing in one second
        collisions = []  # kept for the manifest; otherwise this evidence only ever gets logged
        for st in hyd.clean_list:
            if st is None:
                continue
            for tr in st:  # normally one chunk; more when a real gap split the file
                sr = int(tr.stats["sampling_rate"])
                start = tr.stats["starttime"]
                npts = tr.stats["npts"]
                stamp = start.strftime("%Y%m%d_%H%M%S")  # whole seconds: pbp parses no finer
                name = f"{hyd_refdes[-9:]}_{stamp}"
                if flac_layout == "ooi":
                    # the server's naming, to the microsecond, so pieces never share a name
                    s = tr.stats
                    stamp = start.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                    name = f"{s.network}-{s.station}-{s.location}-{s.channel}-{stamp}"

                # CI occasionally chops one 5-min window into pieces starting ~100 ms
                # apart, which round to the same filename. Keep the longer piece and say
                # so, rather than silently overwriting.
                if stamp in seen:
                    prev_npts, prev_start, _, _ = seen[stamp]
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
                        f"ISSUE {name}: two pieces start in the same "
                        f"second ({prev_start} and {start}); keeping the longer "
                        f"({max(npts, prev_npts) / sr:.2f}s over "
                        f"{min(npts, prev_npts) / sr:.2f}s)"
                    )
                    if not keep:
                        continue
                    written -= prev_npts / sr  # the file being replaced
                seen[stamp] = (npts, start, sr, name)

                flac_path = flac_dir / f"{name}.flac"
                logger.debug(str(flac_path))
                # counts are left-shifted so PCM_24 stores them intact, see INT24_SHIFT
                _write_audio(
                    flac_path, tr.data << INT24_SHIFT, sr, "PCM_24", hyd_refdes, start, npts
                )
                written += npts / sr

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
                {"name": f"{name}.flac", "start": str(start), "npts": npts}
                for _, (npts, start, _, name) in sorted(seen.items())
            ],
        }
        manifest_name = f"{hyd_refdes[-9:]}_{hyd.date.strftime('%Y%m%d')}_manifest.json"
        (flac_dir / manifest_name).write_text(json.dumps(manifest, indent=2))

        # what fraction of the day actually made it to disk
        logger.info(
            f"{hyd_refdes[-9:]} {date}: wrote {written / 3600:.2f}h of audio from "
            f"{len(hyd.mseed_urls)} mseed files ({100 * written / 86400:.1f}% of the day)"
        )
        return hyd


def find_flac_day(hyd_refdes, date):
    """The manifest for a day of FLAC already on disk, or None.

    Spectrograms are built from FLAC in ./data, not from mseed, so this answers "is it
    there" up front rather than letting pbp fail on an empty metadata index.
    """
    instrument, date_str = hyd_refdes[-9:], date.replace("/", "_")
    flac_dir = Path.cwd() / f"data/flac/{date_str}/{instrument}"
    manifest = flac_dir / f"{instrument}_{date.replace('/', '')}_manifest.json"
    if not manifest.is_file():
        return None
    m = json.loads(manifest.read_text())
    m["on_disk"] = len(list(flac_dir.glob("*.flac")))
    return m


@flow(log_prints=True)
def acoustic_flow_oneday(
    hyd_refdes,
    date,
    gap_threshold,
    apply_cals,
    freq_lims,
    s3_sync,
    flag,
    obs_run_type,
    mseed_root=None,
    flac_layout="pbp",
):
    # kept after the run, unlike Prefect's; appended, so reruns of a day stack up
    log_path = Path.cwd() / f"logs/{hyd_refdes[-9:]}/{hyd_refdes[-9:]}_{date.replace('/', '')}.log"
    with log_to_file(log_path):
        logger = select_logger()
        logger.info(f"run: flag={flag} mseed_root={mseed_root} flac_layout={flac_layout} s3_sync={s3_sync}")
        # log python package versions on cloud machine
        installed_packages = {dist.metadata["Name"]: dist.version for dist in distributions()}
        logger.info(f"Installed packages: {installed_packages}")

        if flag == "audio" or flag == "all":
            hyd = convert_mseed_to_audio(
                hyd_refdes=hyd_refdes,
                date=date,
                gap_threshold=gap_threshold,
                mseed_root=mseed_root,
                flac_layout=flac_layout,
            )
            if hyd is None:
                logger.warning(f"No data availale for {date}. Moving to next day.")
                return

            # first element of list is different each time due to multithreading - could add sort step?
            logger.info(f"first 5 elements of cleaned mseed list: {hyd.clean_list[:5]}")

        if flag == "spectrogram" or flag == "all":
            m = find_flac_day(hyd_refdes, date)
            if m is None:
                raise FileNotFoundError(
                    f"no flac for {hyd_refdes} {date} - run --flag audio first"
                )
            if m["on_disk"] != m["files_written"]:
                # leftovers from an earlier run of this day; pbp indexes whatever is there
                logger.warning(f"{m['on_disk']} flac on disk, manifest says {m['files_written']}")
            logger.info(
                f"spectrogram from {m['files_written']} flac, {m['day_coverage_pct']}% of the "
                f"day, written by {m['written_by']}"
            )
            audio_to_spec(date, "flac", hyd_refdes, apply_cals, freq_lims)

        if flag == "low_freq":
            run_low_freq_oneday(hyd_refdes, date, logger)

        if flag == "obs":
            run_obs_viz(hyd_refdes, date, obs_run_type)

        if s3_sync:  # "spectrogram" (.nc + .png) or "all" (those plus flac)
            sync_png_nc_to_s3(hyd_refdes, date, flag, scope=s3_sync)


if __name__ == "__main__":
    acoustic_flow_oneday()

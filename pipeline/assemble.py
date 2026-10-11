"""Assemble per-chapter WAVs into chaptered M4B or YouTube-ready MP4."""
import json
import os
import shutil
import subprocess

from pipeline import procs

import soundfile as sf

from pipeline.synth import SAMPLE_RATE

DEFAULT_BITRATE = "128k"
MASTER_FILTERS = "highpass=f=80,loudnorm=I=-19:TP=-2:LRA=11"

VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
VIDEO_FPS = 30
VIDEO_CRF = "23"
# Fast path for audiobook + one picture: encode a short clip of the still image ONCE
# (a keyframe every STILL_CLIP_SECONDS, so it loops cleanly), then loop it with stream
# copy and mux it with the chapter audio, also copied. Nothing is re-encoded, so a
# multi-hour video takes seconds, at the normal 30 fps.
STILL_CLIP_SECONDS = 2
SLIDESHOW_FPS = 5

# A joined file may differ from the sum of its parts by at most this (seconds) before we
# distrust the stream-copy join and fall back to re-encoding from the WAVs.
COPY_DURATION_TOLERANCE = 1.0


def write_wav(audio, path: str, sample_rate: int = SAMPLE_RATE) -> str:
    sf.write(path, audio, sample_rate)
    return path


def _duration_ms(wav_path: str) -> int:
    info = sf.info(wav_path)
    return int(round(info.frames / info.samplerate * 1000))


def _escape(value: str) -> str:
    # Escape ffmetadata special characters.
    for ch in ("\\", "=", ";", "#", "\n"):
        value = value.replace(ch, "\\" + ch)
    return value


def _build_ffmetadata(titles, durations_ms, book_title=None, author=None) -> str:
    lines = [";FFMETADATA1"]

    if book_title:
        lines.append(f"title={_escape(book_title)}")

    if author:
        lines.append(f"artist={_escape(author)}")

    start = 0

    for title, dur in zip(titles, durations_ms):
        end = start + dur

        lines += [
            "",
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={start}",
            f"END={end}",
            f"title={_escape(title)}",
        ]

        start = end

    return "\n".join(lines) + "\n"


def _check_ffmpeg(ffmpeg: str) -> None:
    if shutil.which(ffmpeg) is None:
        raise RuntimeError(
            f"'{ffmpeg}' not found. Install ffmpeg and ensure it is on PATH "
            "(see README)."
        )


def _write_concat_file(paths, concat_path: str) -> None:
    with open(concat_path, "w", encoding="utf-8") as f:
        for p in paths:
            safe = os.path.abspath(p).replace("'", r"'\''")
            f.write(f"file '{safe}'\n")


def _video_filter(cover_fit: str) -> str:
    """Scale/pad (or crop) the picture to exactly 1920x1080."""
    if cover_fit == "contain":       # whole image visible on a black canvas (book covers)
        return (f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,"
                f"pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black")
    return (f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}")


def _ffmpeg(cmd, total_seconds, progress, cancelled=None):
    """Run ffmpeg; report progress and/or stop early when `cancelled()` turns true."""
    if progress is None and cancelled is None:
        return procs.run(cmd, check=True, capture_output=True, text=True)
    return procs.run_with_progress(cmd, total_seconds=total_seconds, cancelled=cancelled,
                                   on_progress=progress or (lambda _f: None))


class CopyMismatch(Exception):
    """The stream-copy join didn't produce what we expected; re-encode from the WAVs instead."""


def _probe_audio(path: str) -> dict:
    """codec / sample rate / channels / duration (ms) of an audio file's first stream."""
    out = procs.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=codec_name,sample_rate,channels:format=duration", "-of", "json", path],
        check=True, capture_output=True, text=True).stdout
    info = json.loads(out)
    stream = (info.get("streams") or [{}])[0]
    return {"codec": stream.get("codec_name"), "rate": stream.get("sample_rate"),
            "channels": stream.get("channels"),
            "ms": int(round(float(info["format"]["duration"]) * 1000))}


def _copy_plan(audio_files):
    """Chapter durations (ms) if the files can be joined without re-encoding, else None.

    Needs every file to be AAC with the same sample rate and channel count."""
    try:
        probes = [_probe_audio(p) for p in audio_files]
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError):
        return None
    if not probes or probes[0]["codec"] != "aac":
        return None
    first = (probes[0]["codec"], probes[0]["rate"], probes[0]["channels"])
    if any((p["codec"], p["rate"], p["channels"]) != first for p in probes):
        return None
    return [p["ms"] for p in probes]


def _check_joined_duration(path: str, expected_seconds: float) -> None:
    try:
        actual = _probe_audio(path)["ms"] / 1000.0
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError) as exc:
        raise CopyMismatch(f"cannot read the joined file: {exc}") from exc
    if abs(actual - expected_seconds) > COPY_DURATION_TOLERANCE:
        raise CopyMismatch(f"joined length {actual:.1f}s != expected {expected_seconds:.1f}s")


def build_m4b(
    chapter_wavs,
    output_path: str,
    book_title=None,
    author=None,
    cover=None,
    ffmpeg: str = "ffmpeg",
    master: bool = True,
    bitrate: str = DEFAULT_BITRATE,
    progress=None,
    cancelled=None,
    audio_files=None,
) -> str:
    """Build the original chaptered M4B audiobook format.

    progress: optional callable(fraction 0..1) called as ffmpeg encodes.
    cancelled: optional callable; true stops ffmpeg and raises pipeline.procs.Cancelled.
    audio_files: optional already-mastered AAC files, one per chapter (same order as
        chapter_wavs). They are joined WITHOUT re-encoding, which takes seconds instead
        of minutes. Falls back to re-mastering from the WAVs if they can't be joined.
    """
    kwargs = dict(book_title=book_title, author=author, cover=cover, ffmpeg=ffmpeg,
                  master=master, bitrate=bitrate, progress=progress, cancelled=cancelled)
    if audio_files:
        durations = _copy_plan(audio_files)
        if durations:
            try:
                return _build_m4b(chapter_wavs, output_path, audio_files=list(audio_files),
                                  audio_durations=durations, **kwargs)
            except (subprocess.CalledProcessError, CopyMismatch):
                pass                                   # re-master from the WAVs below
    return _build_m4b(chapter_wavs, output_path, **kwargs)


def _build_m4b(chapter_wavs, output_path, *, book_title, author, cover, ffmpeg, master,
               bitrate, progress, cancelled, audio_files=None, audio_durations=None) -> str:
    _check_ffmpeg(ffmpeg)

    copy = audio_files is not None
    titles = [t for t, _ in chapter_wavs]
    paths = audio_files if copy else [p for _, p in chapter_wavs]
    durations = audio_durations if copy else [_duration_ms(p) for p in paths]
    total_seconds = sum(durations) / 1000.0

    workdir = os.path.dirname(os.path.abspath(output_path)) or "."
    concat_path = os.path.join(workdir, "_concat.txt")
    meta_path = os.path.join(workdir, "_ffmeta.txt")

    _write_concat_file(paths, concat_path)

    with open(meta_path, "w", encoding="utf-8") as f:
        f.write(_build_ffmetadata(titles, durations, book_title, author))

    temp_output = output_path + ".building.m4b"

    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        concat_path,
        "-i",
        meta_path,
    ]

    if cover:
        cmd += ["-i", cover]

    cmd += ["-map", "0:a", "-map_metadata", "1"]

    if cover:
        cmd += [
            "-map",
            "2:v",
            "-disposition:v",
            "attached_pic",
            "-c:v",
            "mjpeg",
        ]

    if copy:
        cmd += ["-c:a", "copy"]                        # already mastered AAC: just re-package
    else:
        if master:
            cmd += ["-af", MASTER_FILTERS]
        cmd += ["-c:a", "aac", "-b:a", bitrate]

    cmd += [temp_output]

    try:
        try:
            _ffmpeg(cmd, total_seconds, progress, cancelled)
        except subprocess.CalledProcessError:
            # loudnorm can fail on almost completely silent audio.
            if copy or not master:
                raise

            fallback = [
                a for a in cmd
                if a not in ("-af", MASTER_FILTERS)
            ]

            _ffmpeg(fallback, total_seconds, progress, cancelled)

        if copy:
            _check_joined_duration(temp_output, total_seconds)

        os.replace(temp_output, output_path)

    finally:
        for tmp in (concat_path, meta_path):
            if os.path.exists(tmp):
                os.remove(tmp)

        if os.path.exists(temp_output):
            os.remove(temp_output)

    return output_path


def build_mp4(
    chapter_wavs,
    output_path: str,
    book_title=None,
    author=None,
    cover=None,
    ffmpeg: str = "ffmpeg",
    master: bool = True,
    bitrate: str = DEFAULT_BITRATE,
    cover_fit: str = "crop",
    progress=None,
    cancelled=None,
    audio_files=None,
    slideshow=None,
) -> str:
    """
    Build a YouTube-ready MP4 directly from chapter WAV files.

    The MP4 contains:
      - H.264 1920x1080 video
      - AAC audio
      - chapter metadata
      - title/author metadata
      - cover image as a static background when supplied
      - black background when no cover is supplied

    audio_files: optional already-mastered AAC files, one per chapter. When they can be
    joined as-is the audio is copied (no loudnorm / re-encode) and the still image is
    rendered at a low frame rate, which is many times faster. Otherwise (or if that
    fails) the video is built from the WAVs exactly as before.
    """
    kwargs = dict(book_title=book_title, author=author, cover=cover, ffmpeg=ffmpeg,
                  master=master, bitrate=bitrate, cover_fit=cover_fit, progress=progress,
                  cancelled=cancelled)
    if audio_files:
        durations = _copy_plan(audio_files)
        if durations:
            try:
                if slideshow and len(slideshow) > 1:
                    # several images: shown one after another, each for an equal share of the audio
                    return _build_mp4_loop(chapter_wavs, output_path, audio_files=list(audio_files),
                                           audio_durations=durations, slideshow=list(slideshow),
                                           **kwargs)
                return _build_mp4_loop(chapter_wavs, output_path, audio_files=list(audio_files),
                                       audio_durations=durations, **kwargs)
            except (subprocess.CalledProcessError, CopyMismatch):
                pass                                   # re-build from the WAVs below
    return _build_mp4(chapter_wavs, output_path, **kwargs)


def _build_mp4(chapter_wavs, output_path, *, book_title, author, cover, ffmpeg, master,
               bitrate, cover_fit, progress, cancelled) -> str:
    _check_ffmpeg(ffmpeg)

    if not chapter_wavs:
        raise ValueError("No chapter audio files were supplied.")

    titles = [t for t, _ in chapter_wavs]
    paths = [p for _, p in chapter_wavs]
    durations = [_duration_ms(p) for p in paths]

    total_duration_ms = sum(durations)

    if total_duration_ms <= 0:
        raise ValueError("The chapter audio has zero duration.")

    total_duration = total_duration_ms / 1000.0
    total_seconds = total_duration
    fps = VIDEO_FPS

    workdir = os.path.dirname(os.path.abspath(output_path)) or "."
    concat_path = os.path.join(workdir, "_concat.txt")
    meta_path = os.path.join(workdir, "_ffmeta.txt")

    _write_concat_file(paths, concat_path)

    with open(meta_path, "w", encoding="utf-8") as f:
        f.write(_build_ffmetadata(titles, durations, book_title, author))

    temp_output = output_path + ".building.mp4"

    # Input 0 = static video background
    # Input 1 = concatenated audio
    # Input 2 = FFmetadata
    if cover:
        video_input = [
            "-loop",
            "1",
            "-framerate",
            str(fps),
            "-i",
            cover,
        ]
    else:
        video_input = [
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r={fps}",
        ]

    cmd = [
        ffmpeg,
        "-y",
        *video_input,
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        concat_path,
        "-i",
        meta_path,

        # Video
        "-map",
        "0:v:0",

        # Audio
        "-map",
        "1:a:0",

        # Chapter/title metadata
        "-map_metadata",
        "2",

        # Keep the video exactly 1920x1080.
        "-vf",
        _video_filter(cover_fit),

        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        VIDEO_CRF,
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),

        # Audio
        "-c:a",
        "aac",
        "-b:a",
        bitrate,

        # End video when audio ends.
        "-t",
        f"{total_duration:.3f}",

        # Improve playback when uploaded to YouTube.
        "-movflags",
        "+faststart",

        temp_output,
    ]

    if master:
        # Insert audio filter before output.
        output_index = cmd.index("-c:v")
        cmd[output_index:output_index] = [
            "-af",
            MASTER_FILTERS,
        ]

    try:
        try:
            _ffmpeg(cmd, total_seconds, progress, cancelled)

        except subprocess.CalledProcessError:
            # loudnorm can fail on almost completely silent audio.
            if not master:
                raise

            fallback = [
                a for a in cmd
                if a not in ("-af", MASTER_FILTERS)
            ]

            _ffmpeg(fallback, total_seconds, progress, cancelled)

        os.replace(temp_output, output_path)

    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or "").strip()

        if details:
            raise RuntimeError(
                "FFmpeg failed while creating the MP4:\n\n" + details
            ) from exc

        raise

    finally:
        for tmp in (concat_path, meta_path):
            if os.path.exists(tmp):
                os.remove(tmp)

        if os.path.exists(temp_output):
            os.remove(temp_output)

    return output_path


def _build_mp4_loop(chapter_wavs, output_path, *, book_title, author, cover, ffmpeg, master,
                    bitrate, cover_fit, progress, cancelled, audio_files,
                    audio_durations, slideshow=None) -> str:
    """Fast video: copy the mastered chapter audio and loop a pre-encoded still-image clip.

    (master / bitrate are accepted for call-compatibility; nothing is re-encoded here.)"""
    _check_ffmpeg(ffmpeg)

    if not chapter_wavs:
        raise ValueError("No chapter audio files were supplied.")

    titles = [t for t, _ in chapter_wavs]
    total_seconds = sum(audio_durations) / 1000.0
    if total_seconds <= 0:
        raise ValueError("The chapter audio has zero duration.")

    workdir = os.path.dirname(os.path.abspath(output_path)) or "."
    concat_path = os.path.join(workdir, "_concat.txt")
    meta_path = os.path.join(workdir, "_ffmeta.txt")
    clip_path = output_path + ".building.clip.mp4"
    temp_output = output_path + ".building.mp4"

    _write_concat_file(audio_files, concat_path)
    with open(meta_path, "w", encoding="utf-8") as f:
        f.write(_build_ffmetadata(titles, audio_durations, book_title, author))

    slides_path = output_path + ".building.slides.txt"
    if slideshow:
        each = total_seconds / len(slideshow)
        with open(slides_path, "w", encoding="utf-8") as f:
            for p in slideshow:
                f.write(f"file '{os.path.abspath(p).replace(chr(39), chr(39) + chr(92) + chr(39) * 2)}'\n"
                        f"duration {each:.3f}\n")
            last = os.path.abspath(slideshow[-1]).replace("'", r"'\''")
            f.write(f"file '{last}'\n")          # the concat demuxer needs the last file repeated
        slide_cmd = [
            ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", slides_path,
            "-vf", _video_filter(cover_fit) + f",fps={SLIDESHOW_FPS},format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-crf", VIDEO_CRF,
            "-r", str(SLIDESHOW_FPS), "-t", f"{total_seconds:.3f}", clip_path,
        ]
    if cover:
        source = ["-loop", "1", "-framerate", str(VIDEO_FPS), "-i", cover]
    else:
        source = ["-f", "lavfi", "-i", f"color=c=black:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r={VIDEO_FPS}"]
    gop = VIDEO_FPS * STILL_CLIP_SECONDS
    clip_cmd = [
        ffmpeg, "-y", *source, "-t", str(STILL_CLIP_SECONDS),
        "-vf", _video_filter(cover_fit),
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-crf", VIDEO_CRF,
        "-pix_fmt", "yuv420p", "-r", str(VIDEO_FPS),
        # exactly one keyframe at the start of every loop
        "-x264-params", f"keyint={gop}:min-keyint={gop}:scenecut=0",
        clip_path,
    ]
    mux_cmd = [
        ffmpeg, "-y",
        *([] if slideshow else ["-stream_loop", "-1"]), "-i", clip_path,   # input 0: the still clip (repeated) or slideshow
        "-f", "concat", "-safe", "0", "-i", concat_path,   # input 1: chapter audio
        "-i", meta_path,                               # input 2: chapters / title
        "-map", "0:v:0", "-map", "1:a:0", "-map_metadata", "2",
        "-c", "copy",
        "-t", f"{total_seconds:.3f}",                  # the loop is endless: cap at the audio length
        "-movflags", "+faststart",
        temp_output,
    ]

    def check_cancel():
        if cancelled is not None and cancelled():
            raise procs.Cancelled("cancelled")

    try:
        check_cancel()
        if slideshow:
            _ffmpeg(slide_cmd, total_seconds, None, cancelled)
        else:
            _ffmpeg(clip_cmd, STILL_CLIP_SECONDS, None, cancelled)
        check_cancel()
        _ffmpeg(mux_cmd, total_seconds, progress, cancelled)
        _check_joined_duration(temp_output, total_seconds)
        os.replace(temp_output, output_path)
    finally:
        for tmp in (concat_path, meta_path, clip_path, temp_output, slides_path):
            if os.path.exists(tmp):
                os.remove(tmp)

    return output_path

"""Orchestrate the TTS pipeline into a finished YouTube-ready MP4 + manifest."""
import datetime
import json
import os
import shutil
import subprocess

import numpy as np
import soundfile as sf

from pipeline.parse import parse_chapters
from pipeline.chunk import chunk_paragraphs
from pipeline.synth import Synthesizer, PRESET_VOICES, SAMPLE_RATE
from pipeline.synth import SENTENCE_GAP, PARAGRAPH_GAP
from pipeline.assemble import write_wav, build_mp4, DEFAULT_BITRATE
from pipeline.normalize import normalize_text
from pipeline import procs

WAV_SUBDIR = "wav"
REQUEST_NAME = "request.json"


class RenderCancelled(Exception):
    """Raised when the user cancels a background render."""


def _default_synth_factory(voice_id, speed):
    return Synthesizer(
        voice=voice_id,
        lang_code=PRESET_VOICES[voice_id],
        speed=float(speed),
    )


def _wav_dir(library, job_id: str) -> str:
    d = os.path.join(library.new_dir(job_id), WAV_SUBDIR)
    os.makedirs(d, exist_ok=True)
    return d


def _chapter_meta(chapter_wavs):
    meta, start = [], 0

    for title, wp in chapter_wavs:
        info = sf.info(wp)
        dur = int(round(info.frames / info.samplerate * 1000))

        meta.append({
            "title": title,
            "startMs": start,
            "endMs": start + dur,
        })

        start += dur

    return meta


def render_audiobook(
    *,
    book_text,
    voice,
    speed,
    title,
    author,
    cover_path,
    library,
    job_id,
    emit,
    synth_factory=_default_synth_factory,
    custom_rules=None,
    cancelled=lambda: False,
    chunk_cache=None,
) -> dict:

    chapters = parse_chapters(
        book_text,
        default_title=title or "Audiobook",
    )

    workdir = library.new_dir(job_id)

    # Persist the inputs so a crashed render can be resumed: finished chapter
    # WAVs are kept on disk and skipped the next time this job id is rendered.
    _save_request(workdir, {
        "bookText": book_text,
        "voice": voice,
        "speed": float(speed),
        "title": title,
        "author": author,
        "customRules": custom_rules,
    })

    try:
        return _render_into(
            workdir,
            chapters,
            voice=voice,
            speed=speed,
            title=title,
            author=author,
            cover_path=cover_path,
            library=library,
            job_id=job_id,
            emit=emit,
            synth_factory=synth_factory,
            custom_rules=custom_rules,
            cancelled=cancelled,
            chunk_cache=chunk_cache,
        )

    except RenderCancelled:
        shutil.rmtree(workdir, ignore_errors=True)
        raise

    except Exception:
        # Keep the synthesized audio on disk so the render can be resumed
        # (see Library.incomplete / load_request).
        raise


def _save_request(workdir, request: dict) -> None:
    path = os.path.join(workdir, REQUEST_NAME)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(request, f, ensure_ascii=False)
    os.replace(tmp, path)


def load_request(library, job_id: str) -> dict | None:
    """Inputs of an unfinished render, or None if it can't be resumed."""
    path = os.path.join(library.base, job_id, REQUEST_NAME)
    if library.get(job_id) is not None or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            value = json.load(f)
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _render_into(
    workdir,
    chapters,
    *,
    voice,
    speed,
    title,
    author,
    cover_path,
    library,
    job_id,
    emit,
    synth_factory,
    custom_rules=None,
    cancelled=lambda: False,
    chunk_cache=None,
) -> dict:

    synth = synth_factory(voice, float(speed))
    wav_dir = _wav_dir(library, job_id)

    n = len(chapters)

    prepared = [
        (
            ch,
            chunk_paragraphs(
                normalize_text(ch.text, custom_rules)
            ),
        )
        for ch in chapters
    ]

    total_chunks = max(
        1,
        sum(
            len(p)
            for _, paragraphs in prepared
            for p in paragraphs
        ),
    )

    completed_chunks = 0
    chapter_wavs = []
    wav_files = []

    try:
        for i, (ch, paragraphs) in enumerate(prepared):

            if cancelled():
                raise RenderCancelled("render cancelled")

            emit({
                "type": "progress",
                "chapterIndex": i,
                "chapterCount": n,
                "chapterTitle": ch.title,
                "phase": "audio",
                "chunksDone": completed_chunks,
                "chunkCount": total_chunks,
                "percent": round(
                    completed_chunks / total_chunks * 90
                ),
            })

            rel = os.path.join(
                WAV_SUBDIR,
                f"chapter_{i + 1:03d}.wav",
            )

            wav_path = os.path.join(
                workdir,
                rel,
            )

            # Resume: a finished chapter WAV is only ever renamed into place
            # after it was fully written, so its existence means it is done.
            if os.path.isfile(wav_path):
                completed_chunks += sum(len(p) for p in paragraphs)
                chapter_wavs.append((ch.title, wav_path))
                wav_files.append(rel)
                continue

            final_wav_path = wav_path
            wav_path = wav_path[:-4] + ".part.wav"

            if hasattr(synth, "synth_chunk_with_retry"):

                wrote_audio = False

                with sf.SoundFile(
                    wav_path,
                    mode="w",
                    samplerate=SAMPLE_RATE,
                    channels=1,
                    subtype="PCM_16",
                ) as wav:

                    for pi, paragraph in enumerate(paragraphs):

                        for ci, chunk in enumerate(paragraph):

                            if cancelled():
                                raise RenderCancelled(
                                    "render cancelled"
                                )

                            if wrote_audio:
                                gap = (
                                    PARAGRAPH_GAP
                                    if ci == 0 and pi > 0
                                    else SENTENCE_GAP
                                )

                                wav.write(
                                    np.zeros(
                                        int(
                                            gap
                                            * SAMPLE_RATE
                                        ),
                                        dtype=np.float32,
                                    )
                                )

                            cache_key = {
                                "version": 1,
                                "voice": voice,
                                "speed": float(speed),
                                "text": chunk,
                            }

                            audio = (
                                chunk_cache.get(cache_key)
                                if chunk_cache
                                else None
                            )

                            if audio is None:

                                audio = (
                                    synth.synth_chunk_with_retry(
                                        chunk
                                    )
                                )

                                if chunk_cache:
                                    chunk_cache.put(
                                        cache_key,
                                        audio,
                                    )

                            wav.write(audio)

                            wrote_audio = True
                            completed_chunks += 1

                            emit({
                                "type": "progress",
                                "chapterIndex": i,
                                "chapterCount": n,
                                "chapterTitle": ch.title,
                                "phase": "audio",
                                "chunksDone": completed_chunks,
                                "chunkCount": total_chunks,
                                "percent": round(
                                    completed_chunks
                                    / total_chunks
                                    * 90
                                ),
                            })

                    if not wrote_audio:
                        wav.write(
                            np.zeros(
                                int(0.5 * SAMPLE_RATE),
                                dtype=np.float32,
                            )
                        )

            else:

                audio = synth.synth_paragraphs(
                    paragraphs
                )

                if len(audio) == 0:
                    audio = np.zeros(
                        int(0.5 * SAMPLE_RATE),
                        dtype=np.float32,
                    )

                write_wav(
                    audio,
                    wav_path,
                )

                completed_chunks += sum(
                    len(p)
                    for p in paragraphs
                )

            os.replace(wav_path, final_wav_path)

            chapter_wavs.append((ch.title, final_wav_path))

            wav_files.append(rel)

    finally:

        if hasattr(synth, "close"):
            synth.close()

    cover_dest = None

    if cover_path:

        cover_dest = os.path.join(
            workdir,
            "cover.jpg",
        )

        procs.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                cover_path,
                "-frames:v",
                "1",
                cover_dest,
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        if (
            os.path.abspath(cover_path)
            != os.path.abspath(cover_dest)
        ):
            try:
                os.remove(cover_path)
            except OSError:
                pass

    if cancelled():
        raise RenderCancelled(
            "render cancelled"
        )

    emit({
        "type": "progress",
        "chapterIndex": n - 1,
        "chapterCount": n,
        "chapterTitle": "Assembling MP4",
        "phase": "video",
        "percent": 92,
    })

    out = library.audio_path(job_id)

    last_pct = [91]

    def on_assemble(fraction):
        pct = 92 + round(fraction * 7)
        if pct != last_pct[0]:
            last_pct[0] = pct
            emit({
                "type": "progress",
                "chapterIndex": n - 1,
                "chapterCount": n,
                "chapterTitle": "Assembling MP4",
                "phase": "video",
                "percent": pct,
            })

    try:
        build_mp4(
            chapter_wavs,
            out,
            book_title=title or None,
            author=author or None,
            cover=cover_dest,
            master=True,
            bitrate=DEFAULT_BITRATE,
            progress=on_assemble,
            cancelled=cancelled,
        )
    except procs.Cancelled as exc:
        raise RenderCancelled("render cancelled") from exc

    chapters_meta = _chapter_meta(
        chapter_wavs
    )

    total_ms = (
        chapters_meta[-1]["endMs"]
        if chapters_meta
        else 0
    )

    manifest = {
        "id": job_id,
        "title": title or "Audiobook",
        "author": author or "",
        "voice": voice,
        "speed": float(speed),
        "created": datetime.datetime.now().isoformat(
            timespec="seconds"
        ),
        "durationSeconds": round(
            total_ms / 1000,
            1,
        ),
        "sizeBytes": os.path.getsize(out),
        "chapters": chapters_meta,
        "coverFile": (
            "cover.jpg"
            if cover_dest
            else None
        ),
        "wavKept": True,
        "wavFiles": wav_files,
        "bitrate": DEFAULT_BITRATE,
        "mastered": True,
        "format": "mp4",
        "video": {
            "codec": "h264",
            "width": 1920,
            "height": 1080,
            "fps": 30,
        },
        "audio": {
            "codec": "aac",
            "bitrate": DEFAULT_BITRATE,
        },
    }

    library.save_manifest(
        job_id,
        manifest,
    )

    emit({
        "type": "done",
        "libraryId": job_id,
        "percent": 100,
    })

    return manifest


def remaster(
    library,
    id,
    *,
    bitrate=DEFAULT_BITRATE,
    master=True,
) -> dict:

    m = library.get(id)

    if m is None:
        raise FileNotFoundError(
            f"no such audiobook: {id}"
        )

    if (
        not m.get("wavKept")
        or not m.get("wavFiles")
    ):
        raise ValueError(
            "source audio was purged; "
            "re-render required to re-master"
        )

    workdir = library.new_dir(id)

    chapter_wavs = [
        (
            c["title"],
            os.path.join(
                workdir,
                rel,
            ),
        )
        for c, rel in zip(
            m["chapters"],
            m["wavFiles"],
        )
    ]

    out = library.audio_path(id)

    build_mp4(
        chapter_wavs,
        out,
        book_title=m["title"] or None,
        author=m["author"] or None,
        cover=(
            os.path.join(
                workdir,
                m["coverFile"],
            )
            if m.get("coverFile")
            else None
        ),
        master=master,
        bitrate=bitrate,
    )

    m["bitrate"] = bitrate
    m["mastered"] = master
    m["sizeBytes"] = os.path.getsize(out)
    m["format"] = "mp4"

    library.save_manifest(
        id,
        m,
    )

    return m


def purge_wavs(
    library,
    id,
) -> dict:

    m = library.get(id)

    if m is None:
        raise FileNotFoundError(
            f"no such audiobook: {id}"
        )

    shutil.rmtree(
        os.path.join(
            library.new_dir(id),
            WAV_SUBDIR,
        ),
        ignore_errors=True,
    )

    m["wavKept"] = False
    m["wavFiles"] = []

    library.save_manifest(
        id,
        m,
    )

    return m


def retag_audio(
    library,
    id,
    title: str,
    author: str,
) -> None:
    """Update MP4 container tags without re-encoding audio/video."""

    source = library.audio_path(id)

    if not os.path.isfile(source):
        raise FileNotFoundError(source)

    temp = source + ".retag.mp4"

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        source,
        "-map",
        "0:a",
        "-map",
        "0:v?",
        "-map_metadata",
        "0",
        "-metadata",
        f"title={title}",
        "-metadata",
        f"artist={author}",
        "-codec",
        "copy",
        temp,
    ]

    try:

        procs.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )

        os.replace(
            temp,
            source,
        )

    finally:

        if os.path.exists(temp):
            os.remove(temp)
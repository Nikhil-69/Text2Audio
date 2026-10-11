"""Audio inspection and distribution-oriented chapter exports."""
import json
import os
import re
import subprocess
from pipeline import procs
import tempfile
import zipfile


def _run(args):
    return procs.run(args, check=True, capture_output=True, text=True)


def inspect_audio(path: str) -> dict:
    probe = _run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                  "-of", "json", path])
    payload = json.loads(probe.stdout)
    audio = next((s for s in payload.get("streams", []) if s.get("codec_type") == "audio"), {})
    measured = procs.run(
        ["ffmpeg", "-hide_banner", "-i", path, "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True)
    text = measured.stderr

    def metric(name):
        match = re.search(rf"{name}:\s*(-?inf|-?\d+(?:\.\d+)?)\s*dB", text)
        if not match:
            return None
        return float("-inf") if match.group(1) == "-inf" else float(match.group(1))

    fmt = payload.get("format", {})
    return {
        "path": os.path.basename(path),
        "codec": audio.get("codec_name"),
        "sampleRate": int(audio.get("sample_rate", 0) or 0),
        "channels": int(audio.get("channels", 0) or 0),
        "bitrate": int(audio.get("bit_rate") or fmt.get("bit_rate") or 0),
        "durationSeconds": round(float(fmt.get("duration", 0) or 0), 3),
        "meanDb": metric("mean_volume"),
        "peakDb": metric("max_volume"),
    }


def quality_report(library, id: str) -> dict:
    manifest = library.get(id)
    if manifest is None:
        raise FileNotFoundError(id)
    audio = inspect_audio(library.audio_path(id))
    personal_checks = {
        "decodable": audio["durationSeconds"] > 0 and bool(audio["codec"]),
        "hasChapters": bool(manifest.get("chapters")),
        "peakAtOrBelowMinus2Db": audio["peakDb"] is not None and audio["peakDb"] <= -2.0,
    }
    # ACX accepts individual 44.1 kHz, >=192 kbps CBR MP3 files, not this M4B.
    acx_checks = {
        "individualChapterMp3": False,
        "sampleRate44100": audio["sampleRate"] == 44100,
        "bitrateAtLeast192k": audio["bitrate"] >= 192000,
        "peakAtOrBelowMinus3Db": audio["peakDb"] is not None and audio["peakDb"] <= -3.0,
        "rmsBetweenMinus23AndMinus18Db": audio["meanDb"] is not None and -23 <= audio["meanDb"] <= -18,
    }
    return {"audio": audio, "personalM4b": personal_checks,
            "personalM4bPass": all(personal_checks.values()),
            "acxSubmission": acx_checks, "acxSubmissionPass": all(acx_checks.values()),
            "note": "Automated measurements do not replace editorial listening or distributor review."}


def build_chapter_export(library, id: str, profile: str) -> str:
    manifest = library.get(id)
    if manifest is None:
        raise FileNotFoundError(id)
    if not manifest.get("wavKept") or not manifest.get("wavFiles"):
        raise ValueError("source audio was purged; re-render required for chapter export")
    if profile not in {"wav", "acx-review"}:
        raise ValueError("profile must be wav or acx-review")

    root = library.new_dir(id)
    handle, zip_path = tempfile.mkstemp(prefix=f"text2audio-{profile}-", suffix=".zip")
    os.close(handle)
    try:
        with tempfile.TemporaryDirectory(prefix="text2audio-export-") as temp_dir:
            exported = []
            for index, (chapter, rel) in enumerate(zip(manifest["chapters"], manifest["wavFiles"]), 1):
                source = os.path.join(root, rel)
                safe_title = re.sub(r"[^A-Za-z0-9._-]+", "_", chapter.get("title", "chapter")).strip("_.")
                safe_title = safe_title or "chapter"
                if profile == "wav":
                    exported.append((source, f"{index:03d}_{safe_title}.wav"))
                else:
                    name = f"{index:03d}_{safe_title}.mp3"
                    output = os.path.join(temp_dir, name)
                    _run(["ffmpeg", "-y", "-i", source, "-af",
                          "loudnorm=I=-20:TP=-3:LRA=11,aresample=44100",
                          "-ac", "1", "-c:a", "libmp3lame", "-b:a", "192k", output])
                    exported.append((output, name))
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for source, name in exported:
                    archive.write(source, name)
                archive.writestr("README.txt",
                    "ACX-review files are technical starting points only. Add required opening/closing "
                    "credits and room tone, listen editorially, and validate with the distributor before submission.\n"
                    if profile == "acx-review" else
                    "Lossless chapter source files exported by Text2Audio.\n")
        return zip_path
    except Exception:
        if os.path.exists(zip_path):
            os.remove(zip_path)
        raise

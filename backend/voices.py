"""Persistent store of user-cloned voices (reference clip + metadata)."""
import datetime
import json
import os
import re
import shutil
import subprocess
from pipeline import procs
import uuid

import soundfile as sf

SAMPLE_NAME = "sample.wav"
META_NAME = "meta.json"

# Voice ids are generated as "v" + 9 hex chars. Validate any client-supplied id
# before it touches the filesystem (defends rmtree / arbitrary reads).
_VALID_ID = re.compile(r"^v[0-9a-f]{9}$")


def new_voice_id() -> str:
    return "v" + uuid.uuid4().hex[:9]


class VoiceStore:
    def __init__(self, base_dir: str):
        self.base = base_dir
        os.makedirs(self.base, exist_ok=True)

    def _dir(self, id: str) -> str:
        return os.path.join(self.base, id)

    def ref_path(self, id: str) -> str:
        return os.path.join(self._dir(id), SAMPLE_NAME)

    def get(self, id: str) -> dict | None:
        if not _VALID_ID.match(id):
            return None
        p = os.path.join(self._dir(id), META_NAME)
        if not os.path.isfile(p):
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                value = json.load(f)
            return value if isinstance(value, dict) else None
        except (json.JSONDecodeError, OSError):
            return None

    def list(self) -> list[dict]:
        items = []
        if os.path.isdir(self.base):
            for name in os.listdir(self.base):
                m = self.get(name)
                if m:
                    items.append(m)
        items.sort(key=lambda m: m.get("created", ""), reverse=True)
        return items

    def delete(self, id: str) -> None:
        if not _VALID_ID.match(id):
            return
        shutil.rmtree(self._dir(id), ignore_errors=True)

    def create(self, name: str, audio_bytes: bytes, src_ext: str, ref_text: str = "") -> dict:
        vid = new_voice_id()
        d = self._dir(vid)
        os.makedirs(d, exist_ok=True)
        ext = src_ext.lower() if re.fullmatch(r"\.[a-z0-9]{1,10}", src_ext or "") else ".bin"
        src = os.path.join(d, "_src" + ext)
        with open(src, "wb") as f:
            f.write(audio_bytes)
        out = self.ref_path(vid)
        try:
            procs.run(["ffmpeg", "-y", "-i", src, "-ac", "1", "-ar", "24000", out],
                           check=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            shutil.rmtree(d, ignore_errors=True)
            raise ValueError("could not read that audio file") from exc
        finally:
            if os.path.exists(src):
                os.remove(src)
        info = sf.info(out)
        duration = info.frames / info.samplerate if info.samplerate else 0
        if duration < 1 or duration > 120:
            shutil.rmtree(d, ignore_errors=True)
            raise ValueError("reference audio must be between 1 and 120 seconds")
        manifest = {"id": vid, "name": name, "refText": ref_text or "",
                    "created": datetime.datetime.now().isoformat(timespec="seconds")}
        meta_path = os.path.join(d, META_NAME)
        with open(meta_path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(meta_path + ".tmp", meta_path)
        return manifest

"""Persistence for generated audiobooks under a library directory."""
import json
import os
import shutil
import uuid

AUDIO_NAME = "book.mp4"
COVER_NAME = "cover.jpg"
MANIFEST_NAME = "manifest.json"


def new_id() -> str:
    return uuid.uuid4().hex[:10]


class Library:
    def __init__(self, base_dir: str):
        self.base = base_dir
        os.makedirs(self.base, exist_ok=True)

    def _dir(self, id: str) -> str:
        return os.path.join(self.base, id)

    def new_dir(self, id: str) -> str:
        d = self._dir(id)
        os.makedirs(d, exist_ok=True)
        return d

    def save_manifest(self, id: str, manifest: dict) -> None:
        path = os.path.join(self._dir(id), MANIFEST_NAME)
        tmp = path + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp, path)

    def get(self, id: str) -> dict | None:
        path = os.path.join(self._dir(id), MANIFEST_NAME)

        if not os.path.isfile(path):
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
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

        items.sort(
            key=lambda m: m.get("created", ""),
            reverse=True,
        )

        return items

    def incomplete(self) -> "list[str]":
        """Ids of renders that crashed or were interrupted before finishing."""
        ids = []
        if os.path.isdir(self.base):
            for name in os.listdir(self.base):
                if (os.path.isfile(os.path.join(self.base, name, "request.json"))
                        and not os.path.isfile(os.path.join(self.base, name, MANIFEST_NAME))):
                    ids.append(name)
        return ids

    def audio_path(self, id: str) -> str:
        return os.path.join(self._dir(id), AUDIO_NAME)

    def cover_path(self, id: str) -> str | None:
        p = os.path.join(self._dir(id), COVER_NAME)

        return p if os.path.isfile(p) else None

    def delete(self, id: str) -> None:
        shutil.rmtree(self._dir(id), ignore_errors=True)
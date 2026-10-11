"""Self-update from the latest GitHub Release.

On launch the app asks GitHub for the newest release. If it is newer than the VERSION file,
the release's source is downloaded and copied over the app folder (never touching the
virtualenv or your data: library, voices, batches...), dependencies are refreshed if
requirements.txt changed, and the app restarts into the new version.

Skipped for git checkouts (developers update with `git pull`) and when T2A_NO_UPDATE=1.
Any failure is swallowed: the app always starts, with whatever version it has.
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

REPO = "Nikhil-69/Text2Audio"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Never overwritten or deleted by an update.
PROTECTED = {".venv", "venv", ".git", "library", "data", "voices", "batches", "output", "dist"}
MAX_ZIP_BYTES = 200 * 1024 * 1024


def current_version(root: str = ROOT) -> str:
    try:
        with open(os.path.join(root, "VERSION"), encoding="utf-8") as f:
            return f.read().strip() or "0"
    except OSError:
        return "0"


def parse_version(text: str) -> tuple:
    nums = re.findall(r"\d+", (text or "").split("-")[0])
    return tuple(int(n) for n in nums) or (0,)


def is_newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


def _get(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"User-Agent": "Text2Audio-updater",
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def latest_release(timeout: float = 4.0) -> dict | None:
    """{'version', 'zip_url', 'url'} of the newest release, or None if unreachable."""
    try:
        with _get(API, timeout) as r:
            data = json.load(r)
        assets = [a for a in data.get("assets", []) if a.get("name", "").endswith(".zip")]
        zip_url = assets[0]["browser_download_url"] if assets else data["zipball_url"]
        return {"version": data["tag_name"].lstrip("vV"), "zip_url": zip_url,
                "url": data.get("html_url", "")}
    except Exception:
        return None


def _download(url: str, timeout: float = 120.0) -> bytes:
    with _get(url, timeout) as r:
        data = r.read(MAX_ZIP_BYTES + 1)
    if len(data) > MAX_ZIP_BYTES:
        raise ValueError("update download is too large")
    return data


def _app_root_in(extracted: str) -> str:
    """GitHub zips wrap everything in one folder; find the one holding server.py."""
    for base, _dirs, files in os.walk(extracted):
        if "server.py" in files and "pyproject.toml" in files:
            return base
    raise ValueError("the download does not look like Text2Audio")


def apply_zip(data: bytes, root: str = ROOT) -> bool:
    """Copy a release zip over `root`. Returns True if requirements.txt changed."""
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for name in z.namelist():                      # refuse path traversal
                target = os.path.realpath(os.path.join(tmp, name))
                if not target.startswith(os.path.realpath(tmp) + os.sep):
                    raise ValueError("unsafe path in update")
            z.extractall(tmp)
        src = _app_root_in(tmp)
        old_req = _read(os.path.join(root, "requirements.txt"))
        for base, dirs, files in os.walk(src):
            rel = os.path.relpath(base, src)
            top = rel.split(os.sep)[0]
            if top in PROTECTED or "__pycache__" in rel.split(os.sep):
                dirs[:] = []
                continue
            os.makedirs(os.path.join(root, rel), exist_ok=True)
            for f in files:
                if f.endswith(".pyc") or f == ".DS_Store":
                    continue
                shutil.copy2(os.path.join(base, f), os.path.join(root, rel, f))
        return old_req != _read(os.path.join(root, "requirements.txt"))


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _refresh_deps(root: str = ROOT) -> None:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r",
                    os.path.join(root, "requirements.txt")], check=False, timeout=900)


def update_available(root: str = ROOT) -> dict | None:
    rel = latest_release()
    if rel and is_newer(rel["version"], current_version(root)):
        return rel
    return None


def auto_update(root: str = ROOT) -> bool:
    """Install a newer release if there is one. True if files were updated."""
    if os.environ.get("T2A_NO_UPDATE") == "1" or os.path.isdir(os.path.join(root, ".git")):
        return False
    try:
        rel = update_available(root)
        if not rel:
            return False
        print(f"Updating Text2Audio {current_version(root)} -> {rel['version']} ...", flush=True)
        if apply_zip(_download(rel["zip_url"]), root):
            _refresh_deps(root)
        with open(os.path.join(root, "VERSION"), "w", encoding="utf-8") as f:
            f.write(rel["version"] + "\n")
        print("Updated.", flush=True)
        return True
    except Exception as exc:
        print(f"Update skipped: {exc}", file=sys.stderr, flush=True)
        return False


def restart() -> None:
    """Re-run this process so the new code is loaded."""
    os.environ["T2A_NO_UPDATE"] = "1"                  # don't loop if something is off
    os.execv(sys.executable, [sys.executable] + sys.argv)

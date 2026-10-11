"""Startup housekeeping: stop leftovers of a previous run that didn't exit cleanly."""
import os
import signal
import subprocess
import time

try:
    import fcntl
except ImportError:          # Windows: no advisory locks, treat every start as the only instance
    fcntl = None

PARTIAL_SUFFIXES = (".part.wav", ".tmp", ".tmp.wav", ".tmp.jpg")
PARTIAL_NAMES = ("_concat.txt", "_ffmeta.txt")


def acquire_instance_lock(path: str):
    """Hold a lock for the life of the process. Returns the handle, or None when another
    Text2Audio instance already holds it (that one owns the working files)."""
    if fcntl is None:
        return object()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    handle = open(path, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def find_orphan_ffmpeg(listing: str, dirs) -> "list[int]":
    """PIDs in a `ps -axo pid=,ppid=,command=` listing that are orphaned (PPID 1) ffmpeg
    processes working on files inside `dirs`. Matched by path, so never someone else's
    ffmpeg and never a live app's child."""
    roots = [os.path.abspath(d) for d in dirs]
    found = []
    for line in listing.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3 or parts[1] != "1" or not parts[0].isdigit():
            continue
        command = parts[2]
        if os.path.basename(command.split(" ", 1)[0]) == "ffmpeg" and any(r + os.sep in command for r in roots):
            found.append(int(parts[0]))
    return found


def reap_orphan_ffmpeg(dirs, grace: float = 2.0, listing: "str | None" = None) -> int:
    """Stop orphaned ffmpeg processes left by an earlier run that didn't exit cleanly."""
    if listing is None:
        if os.name == "nt":
            return 0
        try:
            listing = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True,
                                     text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            return 0
    victims = find_orphan_ffmpeg(listing, dirs)
    for pid in victims:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.time() + grace
    for pid in victims:
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                break
            time.sleep(0.1)
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    return len(victims)


def remove_stale_partials(dirs) -> int:
    """Delete half-written files that no running job can own any more."""
    removed = 0
    for base in dirs:
        for root, _dirs, files in os.walk(base):
            for name in files:
                if ".building." in name or name.endswith(PARTIAL_SUFFIXES) or name in PARTIAL_NAMES:
                    try:
                        os.remove(os.path.join(root, name))
                        removed += 1
                    except OSError:
                        pass
    return removed

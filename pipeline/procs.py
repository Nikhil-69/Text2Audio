"""Run ffmpeg & co. so they can never outlive the app.

Drop-in for subprocess.run that registers every child it starts. terminate_all()
(called on app shutdown and at interpreter exit) stops them, and refuses to start
new ones, so a closed app doesn't leave an encoder running in the background.
"""
import atexit
import subprocess
import threading

class Cancelled(Exception):
    """The caller's cancel flag was raised while a child process was running."""


_live: "set[subprocess.Popen]" = set()
_lock = threading.Lock()
_shutting_down = False


def run(cmd, *, check=False, capture_output=False, text=False, **kwargs):
    if _shutting_down:
        raise RuntimeError("Text2Audio is shutting down")
    if capture_output:
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.PIPE)
    proc = subprocess.Popen(cmd, text=text, **kwargs)
    with _lock:
        _live.add(proc)
    try:
        out, err = proc.communicate()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    finally:
        with _lock:
            _live.discard(proc)
    if check and proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, cmd, out, err)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def run_with_progress(cmd, *, total_seconds, on_progress, cancelled=None):
    """Run ffmpeg and call on_progress(fraction 0..1) as it encodes (ffmpeg -progress).

    cancelled: optional callable; when it turns true ffmpeg is stopped within ~0.3 s and
    Cancelled is raised. Raises CalledProcessError on failure, like run(check=True)."""
    if _shutting_down:
        raise RuntimeError("Text2Audio is shutting down")
    cmd = [cmd[0], "-progress", "pipe:1", "-nostats", *cmd[1:]]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    with _lock:
        _live.add(proc)
    errors = []
    reader = threading.Thread(target=lambda: errors.append(proc.stderr.read()), daemon=True)
    reader.start()                       # drain stderr so ffmpeg never blocks on a full pipe
    stop, was_cancelled = threading.Event(), []

    def watch():
        while not stop.wait(0.3):
            if cancelled():
                was_cancelled.append(True)
                proc.terminate()
                return

    if cancelled is not None:
        threading.Thread(target=watch, daemon=True).start()
    try:
        for line in proc.stdout:
            key, _, value = line.strip().partition("=")
            # out_time_us (ffmpeg >= 4.x) is microseconds of output written so far
            if key in ("out_time_us", "out_time_ms") and value.lstrip("-").isdigit() and total_seconds > 0:
                on_progress(max(0.0, min(1.0, int(value) / 1_000_000 / total_seconds)))
        proc.wait()
        reader.join(timeout=2)
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    finally:
        stop.set()
        with _lock:
            _live.discard(proc)
    if was_cancelled:
        raise Cancelled("cancelled")
    if proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, cmd, "", "".join(errors))
    on_progress(1.0)
    return subprocess.CompletedProcess(cmd, 0, "", "".join(errors))


def terminate_all(grace: float = 3.0) -> int:
    """Stop every child still running; returns how many there were."""
    global _shutting_down
    _shutting_down = True
    with _lock:
        procs = list(_live)
    for p in procs:
        if p.poll() is None:
            p.terminate()
    for p in procs:
        try:
            p.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            p.kill()
    return len(procs)


def reset() -> None:
    """Allow new children again (app start; tests that reload the server)."""
    global _shutting_down
    _shutting_down = False


atexit.register(terminate_all)

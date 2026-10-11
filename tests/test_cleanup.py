import os
import subprocess
import sys
import time

import pytest

from backend.cleanup import (acquire_instance_lock, find_orphan_ffmpeg, reap_orphan_ffmpeg,
                             remove_stale_partials)
from pipeline import procs


@pytest.fixture(autouse=True)
def _fresh_procs():
    procs.reset()
    yield
    procs.reset()


def test_run_behaves_like_subprocess_run():
    r = procs.run([sys.executable, "-c", "print('hi')"], capture_output=True, text=True, check=True)
    assert r.stdout.strip() == "hi" and r.returncode == 0
    with pytest.raises(subprocess.CalledProcessError):
        procs.run([sys.executable, "-c", "raise SystemExit(3)"], check=True, capture_output=True)


def test_terminate_all_stops_running_children_and_blocks_new_ones():
    import threading
    result = {}

    def worker():
        try:
            procs.run([sys.executable, "-c", "import time; time.sleep(60)"], check=True, capture_output=True)
        except Exception as exc:
            result["err"] = exc

    t = threading.Thread(target=worker)
    t.start()
    for _ in range(50):
        if procs._live:
            break
        time.sleep(0.05)
    assert procs.terminate_all() == 1
    t.join(timeout=5)
    assert not t.is_alive() and isinstance(result["err"], subprocess.CalledProcessError)
    with pytest.raises(RuntimeError):               # no new encoders during shutdown
        procs.run([sys.executable, "-c", "pass"])


def test_find_orphan_ffmpeg_matches_only_our_orphans():
    ours = "/data/batches"
    listing = "\n".join([
        "100     1 ffmpeg -y -i /data/batches/b1/outputs/_concat.txt out.mp4",          # ours, orphan
        "101     1 /opt/homebrew/bin/ffmpeg -y -i /data/batches/b2/items/1.wav x.m4a",  # ours, orphan
        "102   555 ffmpeg -y -i /data/batches/b1/items/1.wav y.m4a",                     # live parent
        "103     1 ffmpeg -y -i /Users/me/Movies/film.mov out.mp4",                     # someone else's
        "104     1 python /data/batches/script.py",                                      # not ffmpeg
        "105     1 ffmpeg -i /data/batches-other/x.wav out.mp4",                         # similar prefix
        "junk line",
    ])
    assert find_orphan_ffmpeg(listing, [ours]) == [100, 101]


@pytest.mark.skipif(os.name == "nt", reason="POSIX process model")
def test_reap_stops_a_real_orphan_and_spares_others(tmp_path):
    def orphan():   # the shell exits, so the sleeper is re-parented to PID 1
        out = subprocess.run(["sh", "-c", "sleep 60 >/dev/null 2>&1 & echo $!"],
                             capture_output=True, text=True)
        return int(out.stdout.strip())

    victim, bystander = orphan(), orphan()
    try:
        listing = f"{victim} 1 ffmpeg -i {tmp_path}/b/x.wav out.mp4"     # only the victim is "ours"
        assert reap_orphan_ffmpeg([str(tmp_path)], listing=listing) == 1
        time.sleep(0.3)
        assert subprocess.run(["ps", "-p", str(victim)], capture_output=True).returncode != 0
        assert subprocess.run(["ps", "-p", str(bystander)], capture_output=True).returncode == 0
    finally:
        for pid in (victim, bystander):
            subprocess.run(["kill", "-9", str(pid)], capture_output=True)


def test_remove_stale_partials_keeps_real_files(tmp_path):
    out = tmp_path / "b1" / "outputs"
    items = tmp_path / "b1" / "items"
    out.mkdir(parents=True)
    items.mkdir()
    stale = [out / "a.mp4.building.mp4", out / "_concat.txt", out / "_ffmeta.txt",
             items / "001.part.wav", items / "001.m4a.building.m4a"]
    keep = [out / "a.mp4", out / "b.m4b", items / "001.wav", items / "001.m4a", items / "001.txt",
            tmp_path / "b1" / "batch.json"]
    for f in stale + keep:
        f.write_bytes(b"x")
    assert remove_stale_partials([str(tmp_path)]) == len(stale)
    assert all(not f.exists() for f in stale) and all(f.exists() for f in keep)


def test_second_instance_does_not_get_the_lock(tmp_path):
    path = str(tmp_path / "instance.lock")
    first = acquire_instance_lock(path)
    assert first is not None
    probe = subprocess.run([sys.executable, "-c",
                            "import sys; sys.path.insert(0, %r); "
                            "from backend.cleanup import acquire_instance_lock as a; "
                            "print(a(%r) is None)" % (os.getcwd(), path)],
                           capture_output=True, text=True)
    assert probe.stdout.strip() == "True"


def test_run_with_progress_reports_fractions_and_fails_loudly(tmp_path):
    import shutil
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    seen = []
    out = tmp_path / "o.wav"
    procs.run_with_progress(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=d=4", str(out)],
                            total_seconds=4, on_progress=seen.append)
    assert seen and seen[-1] == 1.0 and all(0 <= f <= 1 for f in seen)
    assert seen == sorted(seen)                                    # never goes backwards
    with pytest.raises(subprocess.CalledProcessError):
        procs.run_with_progress(["ffmpeg", "-y", "-i", str(tmp_path / "missing.wav"), str(out)],
                                total_seconds=1, on_progress=seen.append)


def test_run_with_progress_cancel_stops_ffmpeg_quickly(tmp_path):
    import shutil
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    start = time.time()
    # -re = real time: this would take a full minute if it weren't cancelled
    cmd = ["ffmpeg", "-y", "-re", "-f", "lavfi", "-i", "sine=d=60", str(tmp_path / "o.wav")]
    with pytest.raises(procs.Cancelled):
        procs.run_with_progress(cmd, total_seconds=60, on_progress=lambda f: None,
                                cancelled=lambda: time.time() - start > 1.0)
    assert time.time() - start < 6
    assert not procs._live                       # nothing left running

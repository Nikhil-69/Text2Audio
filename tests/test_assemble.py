import json
import shutil
import subprocess

import numpy as np
import pytest

from pipeline.assemble import write_wav, build_m4b, _build_ffmetadata
from pipeline.synth import SAMPLE_RATE

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def test_ffmetadata_has_chapters_and_escapes():
    meta = _build_ffmetadata(["Intro", "Chap=Two"], [1000, 2000],
                             book_title="My Book", author="Me")
    assert ";FFMETADATA1" in meta
    assert "title=My Book" in meta
    assert meta.count("[CHAPTER]") == 2
    assert "START=0" in meta and "END=1000" in meta
    assert "START=1000" in meta and "END=3000" in meta
    assert "Chap\\=Two" in meta  # '=' escaped


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_build_m4b_produces_two_chapters(tmp_path):
    wav1 = str(tmp_path / "c1.wav")
    wav2 = str(tmp_path / "c2.wav")
    t1 = np.linspace(0, 1.0, SAMPLE_RATE, dtype=np.float32)
    t2 = np.linspace(0, 0.5, SAMPLE_RATE // 2, dtype=np.float32)
    write_wav(0.1 * np.sin(2 * np.pi * 440 * t1), wav1)   # 1.0s sine
    write_wav(0.1 * np.sin(2 * np.pi * 440 * t2), wav2)   # 0.5s sine
    out = str(tmp_path / "book.m4b")

    build_m4b([("Chapter One", wav1), ("Chapter Two", wav2)], out,
              book_title="Test Book", author="Tester")

    probe = subprocess.run(
        ["ffprobe", "-print_format", "json", "-show_chapters", out],
        capture_output=True, text=True, check=True)
    chapters = json.loads(probe.stdout)["chapters"]
    assert len(chapters) == 2
    assert chapters[0]["tags"]["title"] == "Chapter One"
    assert chapters[1]["tags"]["title"] == "Chapter Two"


def test_build_m4b_missing_ffmpeg_raises(tmp_path):
    with pytest.raises(RuntimeError):
        build_m4b([("A", "x.wav")], str(tmp_path / "o.m4b"),
                  ffmpeg="definitely-not-ffmpeg-xyz")


def test_build_m4b_applies_master_filters_and_bitrate(tmp_path, monkeypatch):
    import pipeline.assemble as asm
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        open(cmd[-1], "wb").close()
        class R: pass
        return R()

    # write a real wav so duration probing works, but stub ffmpeg invocation
    import numpy as np
    from pipeline.synth import SAMPLE_RATE
    wav = str(tmp_path / "c.wav")
    asm.write_wav(np.zeros(SAMPLE_RATE, dtype=np.float32), wav)
    monkeypatch.setattr(asm.procs, "run", fake_run)
    monkeypatch.setattr(asm.shutil, "which", lambda x: "ffmpeg")

    asm.build_m4b([("C1", wav)], str(tmp_path / "out.m4b"), master=True)
    cmd = captured["cmd"]
    assert "-af" in cmd
    af = cmd[cmd.index("-af") + 1]
    assert "loudnorm" in af and "highpass" in af
    assert "128k" in cmd  # default bitrate


def test_build_m4b_master_false_omits_filters(tmp_path, monkeypatch):
    import pipeline.assemble as asm
    captured = {}
    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        open(cmd[-1], "wb").close()
    monkeypatch.setattr(asm.procs, "run", fake_run)
    monkeypatch.setattr(asm.shutil, "which", lambda x: "ffmpeg")
    import numpy as np
    from pipeline.synth import SAMPLE_RATE
    wav = str(tmp_path / "c.wav")
    asm.write_wav(np.zeros(SAMPLE_RATE, dtype=np.float32), wav)
    asm.build_m4b([("C1", wav)], str(tmp_path / "out.m4b"), master=False, bitrate="96k")
    cmd = captured["cmd"]
    assert "-af" not in cmd
    assert "96k" in cmd


def test_build_failure_preserves_existing_output(tmp_path, monkeypatch):
    import pipeline.assemble as asm
    wav = str(tmp_path / "c.wav")
    asm.write_wav(np.zeros(SAMPLE_RATE, dtype=np.float32), wav)
    output = tmp_path / "book.m4b"
    output.write_bytes(b"known-good")
    monkeypatch.setattr(asm.shutil, "which", lambda x: "ffmpeg")
    def fail(cmd, **kwargs):
        open(cmd[-1], "wb").write(b"partial")
        raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(asm.procs, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        asm.build_m4b([("C1", wav)], str(output), master=False)
    assert output.read_bytes() == b"known-good"
    assert not (tmp_path / "book.m4b.building.m4b").exists()


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_build_m4b_master_true_survives_pure_silence(tmp_path):
    # loudnorm NaN on silence must not crash: fall back to a valid unmastered file.
    wav = str(tmp_path / "silent.wav")
    write_wav(np.zeros(SAMPLE_RATE, dtype=np.float32), wav)
    out = str(tmp_path / "silent.m4b")
    build_m4b([("C1", wav)], out, master=True)
    probe = subprocess.run(["ffprobe", "-print_format", "json", "-show_chapters", out],
                           capture_output=True, text=True, check=True)
    assert len(json.loads(probe.stdout)["chapters"]) == 1


# ---- joining already-mastered chapter files without re-encoding ----

def _chapters_with_m4a(tmp_path, seconds=(3.0, 2.0, 4.0)):
    """Chapter WAVs plus the mastered .m4a the batch pipeline would have produced."""
    from backend.batch import _encode_m4a
    chapters, m4as = [], []
    for i, secs in enumerate(seconds, 1):
        t = np.linspace(0, secs, int(SAMPLE_RATE * secs), dtype=np.float32)
        wav = str(tmp_path / f"c{i}.wav")
        write_wav(0.1 * np.sin(2 * np.pi * (300 + 100 * i) * t), wav)
        m4a = str(tmp_path / f"c{i}.m4a")
        _encode_m4a(wav, m4a, f"Chapter {i}")
        chapters.append((f"Chapter {i}", wav))
        m4as.append(m4a)
    return chapters, m4as


def _probe(path, *args):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", *args, path],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def _spy(monkeypatch):
    """Record every ffmpeg command line the builders run (they still really run)."""
    import pipeline.assemble as asm
    cmds = []
    real = asm._ffmpeg

    def spy(cmd, *a, **k):
        cmds.append(list(cmd))
        return real(cmd, *a, **k)

    monkeypatch.setattr(asm, "_ffmpeg", spy)
    return cmds


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_build_m4b_joins_mastered_files_without_reencoding(tmp_path, monkeypatch):
    chapters, m4as = _chapters_with_m4a(tmp_path)
    cmds = _spy(monkeypatch)
    out = str(tmp_path / "book.m4b")
    build_m4b(chapters, out, book_title="T", audio_files=m4as)

    assert len(cmds) == 1
    cmd = cmds[0]
    assert cmd[cmd.index("-c:a") + 1] == "copy" and "-af" not in cmd      # no loudnorm, no re-encode
    info = _probe(out, "-show_chapters", "-show_format")
    assert [c["tags"]["title"] for c in info["chapters"]] == ["Chapter 1", "Chapter 2", "Chapter 3"]
    assert abs(float(info["format"]["duration"]) - 9.0) < 0.5
    ends = [float(c["end_time"]) for c in info["chapters"]]
    assert abs(ends[-1] - float(info["format"]["duration"])) < 0.5       # markers line up with the audio


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_build_m4b_falls_back_to_wavs_when_files_cannot_be_joined(tmp_path, monkeypatch):
    chapters, m4as = _chapters_with_m4a(tmp_path, seconds=(2.0, 2.0))
    cmds = _spy(monkeypatch)
    # not AAC at all -> can't be stream-copied -> must re-master from the WAVs
    wavs = [p for _, p in chapters]
    out = str(tmp_path / "book.m4b")
    build_m4b(chapters, out, audio_files=wavs)
    assert len(cmds) == 1 and "-af" in cmds[0]
    assert len(_probe(out, "-show_chapters")["chapters"]) == 2


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_build_m4b_falls_back_when_joined_length_is_wrong(tmp_path, monkeypatch):
    import pipeline.assemble as asm
    chapters, m4as = _chapters_with_m4a(tmp_path, seconds=(2.0, 2.0))
    cmds = _spy(monkeypatch)

    def bad_check(path, expected):
        raise asm.CopyMismatch("simulated drift")

    monkeypatch.setattr(asm, "_check_joined_duration", bad_check)
    out = str(tmp_path / "book.m4b")
    build_m4b(chapters, out, audio_files=m4as)
    assert len(cmds) == 2 and "-af" not in cmds[0] and "-af" in cmds[1]   # tried the join, then re-mastered
    assert abs(float(_probe(out, "-show_format")["format"]["duration"]) - 4.0) < 0.5
    assert not [f for f in tmp_path.iterdir() if ".building." in f.name]


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_build_mp4_loops_a_still_clip_and_copies_audio(tmp_path, monkeypatch):
    from pipeline.assemble import build_mp4, VIDEO_FPS, STILL_CLIP_SECONDS
    chapters, m4as = _chapters_with_m4a(tmp_path)
    cmds = _spy(monkeypatch)
    out = str(tmp_path / "v.mp4")
    build_mp4(chapters, out, book_title="T", audio_files=m4as)

    clip_cmd, mux_cmd = cmds                                  # 1) encode the clip once  2) loop + copy
    assert clip_cmd[clip_cmd.index("-t") + 1] == str(STILL_CLIP_SECONDS)
    assert "-stream_loop" in mux_cmd and mux_cmd[mux_cmd.index("-c") + 1] == "copy"
    assert "-af" not in mux_cmd and "libx264" not in mux_cmd      # nothing is re-encoded
    info = _probe(out, "-show_streams", "-show_chapters", "-show_format")
    kinds = {s["codec_type"]: s for s in info["streams"]}
    assert kinds["video"]["codec_name"] == "h264" and kinds["audio"]["codec_name"] == "aac"
    assert (kinds["video"]["width"], kinds["video"]["height"]) == (1920, 1080)
    assert kinds["video"]["r_frame_rate"] == f"{VIDEO_FPS}/1"
    assert len(info["chapters"]) == 3
    assert abs(float(info["format"]["duration"]) - 9.0) < 0.5
    # a keyframe at the start of every loop: seeking works anywhere
    kf = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                         "packet=pts_time,flags", "-of", "csv=p=0", out],
                        capture_output=True, text=True, check=True).stdout
    times = [float(l.split(",")[0]) for l in kf.splitlines() if l.endswith("K__") or ",K" in l]
    assert len(times) >= 4 and all(abs(b - a - STILL_CLIP_SECONDS) < 0.1 for a, b in zip(times, times[1:]))
    assert subprocess.run(["ffmpeg", "-v", "error", "-i", out, "-f", "null", "-"],
                          capture_output=True, text=True).stderr.strip() == ""    # decodes cleanly


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_build_mp4_loop_failure_falls_back_to_the_wav_path(tmp_path, monkeypatch):
    import pipeline.assemble as asm
    chapters, m4as = _chapters_with_m4a(tmp_path, seconds=(2.0, 2.0))
    cmds = _spy(monkeypatch)
    monkeypatch.setattr(asm, "_check_joined_duration",
                        lambda path, expected: (_ for _ in ()).throw(asm.CopyMismatch("drift")))
    out = str(tmp_path / "v.mp4")
    asm.build_mp4(chapters, out, audio_files=m4as)
    assert len(cmds) == 3 and "-stream_loop" in cmds[1] and "-af" in cmds[2]   # clip, loop (rejected), re-encode
    assert abs(float(_probe(out, "-show_format")["format"]["duration"]) - 4.0) < 0.5
    assert not [f for f in tmp_path.iterdir() if ".building." in f.name]


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_build_mp4_without_audio_files_keeps_the_old_behaviour(tmp_path, monkeypatch):
    from pipeline.assemble import build_mp4, VIDEO_FPS
    chapters, _ = _chapters_with_m4a(tmp_path, seconds=(1.0,))
    cmds = _spy(monkeypatch)
    build_mp4(chapters, str(tmp_path / "v.mp4"))
    cmd = cmds[0]
    assert cmd[cmd.index("-r") + 1] == str(VIDEO_FPS) and "-af" in cmd
    assert cmd[cmd.index("-c:a") + 1] == "aac"

import os
import time

import numpy as np
import pytest

from backend.batch import (BatchError, BatchStore, merge_batch, natural_key, synthesize_batch,
                           video_batch)
from backend.render import RenderCancelled
from pipeline.synth import ChunkSynthError, SAMPLE_RATE


class Fake:
    def __init__(self, voice, speed=1.0, boom=None, bad=None):
        self.boom, self.bad, self.calls = boom, bad, []

    def synth_chunk_with_retry(self, text):
        self.calls.append(text)
        if self.boom and self.boom in text:
            raise RuntimeError("worker died")
        if self.bad and self.bad in text:
            raise ChunkSynthError("bad text")
        return np.zeros(int(0.2 * SAMPLE_RATE), dtype=np.float32)


WORDS = ["one", "two", "three", "four", "five", "six"]


def _make(tmp_path, n=3):
    store = BatchStore(str(tmp_path))
    items = [(f"file{i}", f"## file{i}\nSentence {WORDS[i - 1]} here.") for i in range(1, n + 1)]
    store.create("b1", title="T", author="A", voice="af_heart", speed=1.0, items=items)
    return store


def _synth(store, **kw):
    return synthesize_batch(store, "b1", emit=kw.pop("emit", lambda e: None),
                            synth_factory=kw.pop("factory", lambda v, s: Fake(v)), **kw)


def _statuses(store):
    return [i["status"] for i in store.get("b1")["items"]]


def test_natural_sort():
    assert sorted(["c10", "c2", "c1"], key=natural_key) == ["c1", "c2", "c10"]


def test_synth_each_file_then_merge_and_video_all(tmp_path):
    store = _make(tmp_path)
    events = []
    _synth(store, emit=events.append)
    assert _statuses(store) == ["done"] * 3
    assert events[-1]["type"] == "done" and events[-1]["failed"] == 0

    merge_batch(store, "b1", emit=lambda e: None)
    video_batch(store, "b1", emit=lambda e: None)
    outs = store.get("b1")["outputs"]
    assert [o["kind"] for o in outs] == ["audio", "video"]
    assert all(os.path.getsize(store.output_file("b1", o)) > 0 for o in outs)
    assert outs[0]["first"] == 1 and outs[0]["last"] == 3 and outs[0]["count"] == 3


def test_stopped_midway_then_merge_only_finished_range(tmp_path):
    """Stop while file 3 is in flight: 1-2 are kept and can be merged / made into a video."""
    store = _make(tmp_path, n=4)
    calls = []

    def cancelled():
        return len(calls) >= 2          # allow two files, then stop

    class Counting(Fake):
        def synth_chunk_with_retry(self, text):
            calls.append(text)
            return super().synth_chunk_with_retry(text)

    with pytest.raises(RenderCancelled):
        _synth(store, factory=lambda v, s: Counting(v), cancelled=cancelled)
    assert _statuses(store) == ["done", "done", "pending", "pending"]

    out = merge_batch(store, "b1", emit=lambda e: None, indexes=[1, 2, 3])   # 3 isn't done: skipped
    assert (out["first"], out["last"], out["count"]) == (1, 2, 2)
    video = video_batch(store, "b1", emit=lambda e: None, indexes=[1, 2], name="Part one")
    assert video["name"] == "Part one"
    with pytest.raises(BatchError):
        merge_batch(store, "b1", emit=lambda e: None, indexes=[3, 4])        # nothing finished


def test_crash_keeps_done_files_and_rerun_skips_them(tmp_path):
    store = _make(tmp_path)
    with pytest.raises(RuntimeError):
        _synth(store, factory=lambda v, s: Fake(v, boom="two"))
    assert _statuses(store) == ["done", "pending", "pending"]
    fake = Fake("x")
    _synth(store, factory=lambda v, s: fake)
    assert len(fake.calls) == 2          # file 1 was not redone
    assert _statuses(store) == ["done"] * 3


def test_one_bad_file_does_not_stop_the_rest(tmp_path):
    store = _make(tmp_path)
    ev = []
    _synth(store, emit=ev.append, factory=lambda v, s: Fake(v, bad="two"))
    assert _statuses(store) == ["done", "error", "done"]
    assert ev[-1]["failed"] == 1


def test_generate_only_selected_files(tmp_path):
    store = _make(tmp_path, n=4)
    _synth(store, only=[2, 3])
    assert _statuses(store) == ["pending", "done", "done", "pending"]


def test_edit_text_rename_reorder_delete_add(tmp_path):
    store = _make(tmp_path)
    _synth(store)

    st = store.update_item("b1", 2, name="Renamed")
    assert st["items"][1]["name"] == "Renamed" and st["items"][1]["status"] == "done"

    st = store.update_item("b1", 2, text="## x\nCompletely new text.")
    assert st["items"][1]["status"] == "pending"            # audio discarded: text changed
    assert not os.path.exists(store.item_file("b1", st["items"][1], "wav"))
    assert store.read_text("b1", 2).endswith("Completely new text.")

    st = store.move_item("b1", 3, 1)                         # old file 3 becomes first
    assert [i["name"] for i in st["items"]] == ["file3", "file1", "Renamed"]
    assert [i["index"] for i in st["items"]] == [1, 2, 3]
    assert _statuses(store) == ["done", "done", "pending"]  # audio travels with the file

    st = store.delete_items("b1", [2])
    assert [i["name"] for i in st["items"]] == ["file3", "Renamed"]
    assert [i["index"] for i in st["items"]] == [1, 2]

    st = store.add_items("b1", [("late", "## l\nAdded later.")])
    assert [i["index"] for i in st["items"]] == [1, 2, 3]
    uids = [i["uid"] for i in st["items"]]
    assert len(set(uids)) == 3                              # uids never reused

    st = store.reset_items("b1", [1])
    assert _statuses(store) == ["pending", "pending", "pending"]

    with pytest.raises(BatchError):
        store.update_item("b1", 99, name="x")
    with pytest.raises(BatchError):
        store.update_item("b1", 1, text="   ")


def test_outputs_survive_edits_and_can_be_renamed_and_deleted(tmp_path):
    store = _make(tmp_path)
    _synth(store)
    out = merge_batch(store, "b1", emit=lambda e: None)
    store.delete_items("b1", [1])                            # output is a snapshot
    assert os.path.isfile(store.output_file("b1", out))
    st = store.rename_output("b1", out["id"], "Final")
    assert st["outputs"][0]["name"] == "Final"
    st = store.delete_output("b1", out["id"])
    assert st["outputs"] == [] and not os.path.exists(store.output_file("b1", out))
    with pytest.raises(BatchError):
        store.delete_output("b1", out["id"])


def test_old_state_format_is_migrated(tmp_path):
    import json
    store = BatchStore(str(tmp_path))
    d = tmp_path / "old1"
    (d / "items").mkdir(parents=True)
    (d / "items" / "001.txt").write_text("hello")
    (d / "merged.m4b").write_bytes(b"x")
    (d / "batch.json").write_text(json.dumps({
        "id": "old1", "title": "Old", "author": "", "voice": "af_heart", "speed": 1.0,
        "created": "2026-01-01T00:00:00",
        "items": [{"index": 1, "name": "a", "chars": 5, "status": "done",
                   "durationSeconds": 1.0, "error": None}],
        "merged": {"sizeBytes": 1, "durationSeconds": 1.0}, "video": None}))
    st = store.get("old1")
    assert st["items"][0]["uid"] == 1 and st["nextUid"] == 2
    assert len(st["outputs"]) == 1 and os.path.isfile(store.output_file("old1", st["outputs"][0]))


def test_merge_and_video_report_steadily_increasing_progress(tmp_path):
    store = _make(tmp_path)
    _synth(store)
    for fn in (merge_batch, video_batch):
        events = []
        fn(store, "b1", emit=events.append)
        pcts = [e["percent"] for e in events if e["type"] == "progress"]
        assert len(pcts) >= 2 and pcts == sorted(pcts) and pcts[0] == 0 and pcts[-1] == 98
        assert all("fraction" in e and "ts" in e for e in events if e["type"] == "progress")
        assert events[-1]["type"] == "done"


def test_cancel_during_video_leaves_no_output_no_partials_and_keeps_audio(tmp_path):
    """Cancel as soon as the video starts: nothing registered, nothing left behind, audio kept.
    (Stopping ffmpeg mid-encode is covered by test_run_with_progress_cancel_stops_ffmpeg_quickly.)"""
    import subprocess
    store = _make(tmp_path, n=3)
    _synth(store)
    seen = []

    with pytest.raises(RenderCancelled):
        video_batch(store, "b1", emit=seen.append, cancelled=lambda: bool(seen))   # true once started

    assert store.get("b1")["outputs"] == []
    assert _statuses(store) == ["done"] * 3
    assert [f for r, _, fs in os.walk(tmp_path) for f in fs if ".building." in f] == []
    ps = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True).stdout
    assert str(tmp_path) not in ps                                                  # no ffmpeg running


def test_cancel_during_merge_leaves_no_output(tmp_path):
    store = _make(tmp_path, n=3)
    _synth(store)
    seen = []
    # merge is a stream copy (instant), so cancel through the same flag the encoder polls
    import pipeline.procs as procs
    real = procs.run_with_progress

    def cancelling(cmd, **kw):
        raise procs.Cancelled("cancelled")

    procs.run_with_progress = cancelling
    try:
        with pytest.raises(RenderCancelled):
            merge_batch(store, "b1", emit=seen.append, cancelled=lambda: True)
    finally:
        procs.run_with_progress = real
    assert store.get("b1")["outputs"] == []
    assert [f for r, _, fs in os.walk(tmp_path) for f in fs if ".building." in f] == []

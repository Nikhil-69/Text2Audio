"""Batch mode: one audio file per uploaded text file, then optional merge and video.

Layout (everything for a batch lives under <base>/<id>/):
    batch.json          state; rewritten atomically after every change
    items/007.txt       narrated source text of the item with uid 7
    items/007.wav       lossless audio (merge and video are built from these)
    items/007.m4a       mastered, shareable audio for that file
    outputs/<oid>.m4b   a merge of any chosen files (a snapshot, one per merge)
    outputs/<oid>.mp4   a video of any chosen files

An item has a stable `uid` (used for file names, never reused) and a `index`,
its 1-based position in the list, which is renumbered after every reorder /
delete. Each stage is separate, so a crash only costs the item in flight:
finished items are skipped the next time audio is generated.
"""
import datetime
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid

import numpy as np
import soundfile as sf

from backend.render import RenderCancelled
from pipeline.assemble import (DEFAULT_BITRATE, MASTER_FILTERS, build_m4b,
                               build_mp4)
from pipeline.chunk import chunk_paragraphs
from pipeline.normalize import normalize_text
from pipeline.synth import PARAGRAPH_GAP, SAMPLE_RATE, SENTENCE_GAP, ChunkSynthError
from pipeline import procs

STATE_NAME = "batch.json"


class BatchError(ValueError):
    """A request that can't be applied (bad index, nothing selected, ...)."""


def natural_key(text: str):
    """'ch2.txt' sorts before 'ch10.txt'."""
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", text)]


def _renumber(state: dict) -> None:
    for pos, it in enumerate(state["items"], 1):
        it["index"] = pos


class BatchStore:
    def __init__(self, base_dir: str):
        self.base = base_dir
        self.lock = threading.RLock()      # serializes edits made through the API
        os.makedirs(base_dir, exist_ok=True)

    # ---- paths ----
    def dir(self, bid: str) -> str:
        return os.path.join(self.base, bid)

    def path(self, bid: str, *parts: str) -> str:
        return os.path.join(self.dir(bid), *parts)

    def item_file(self, bid: str, it: dict, ext: str) -> str:
        return self.path(bid, "items", f"{it['uid']:03d}.{ext}")

    def cover_file(self, bid: str) -> str:
        return self.path(bid, "cover.jpg")

    def set_cover(self, bid: str, image_bytes: bytes, ffmpeg: str = "ffmpeg") -> None:
        """Store the video background image, converting any JPEG/PNG/WebP to JPEG."""
        self._load(bid)
        self._store_image(bid, image_bytes, self.cover_file(bid), ffmpeg)

    def _store_image(self, bid: str, image_bytes: bytes, dest: str, ffmpeg: str = "ffmpeg") -> None:
        src = self.path(bid, "_cover_upload")
        tmp = dest + ".tmp.jpg"
        with open(src, "wb") as f:
            f.write(image_bytes)
        try:
            procs.run([ffmpeg, "-y", "-i", src, "-frames:v", "1", "-q:v", "2", tmp],
                           check=True, capture_output=True, text=True)
            os.replace(tmp, dest)
        except subprocess.CalledProcessError as exc:
            raise BatchError("that file is not a usable image (use JPEG, PNG or WebP)") from exc
        finally:
            for p in (src, tmp):
                if os.path.exists(p):
                    os.remove(p)

    def remove_cover(self, bid: str) -> None:
        self._load(bid)
        try:
            os.remove(self.cover_file(bid))
        except OSError:
            pass

    # ---- video images: cover.jpg (the original single image) then covers/*.jpg in upload order ----
    def image_ids(self, bid: str) -> "list[str]":
        ids = ["cover"] if os.path.isfile(self.cover_file(bid)) else []
        try:
            ids += sorted(n[:-4] for n in os.listdir(self.path(bid, "covers")) if n.endswith(".jpg")
                          and not n.endswith(".tmp.jpg"))
        except OSError:
            pass
        return ids

    def image_file(self, bid: str, image_id: str) -> str:
        if image_id == "cover":
            return self.cover_file(bid)
        if not re.fullmatch(r"\d{1,24}", image_id):
            raise BatchError("unknown image")
        return self.path(bid, "covers", image_id + ".jpg")

    def add_image(self, bid: str, image_bytes: bytes, ffmpeg: str = "ffmpeg") -> str:
        self._load(bid)
        os.makedirs(self.path(bid, "covers"), exist_ok=True)
        image_id = f"{time.time_ns():024d}"
        self._store_image(bid, image_bytes, self.image_file(bid, image_id), ffmpeg)
        return image_id

    def remove_image(self, bid: str, image_id: str) -> None:
        self._load(bid)
        try:
            os.remove(self.image_file(bid, image_id))
        except OSError:
            pass

    def output_file(self, bid: str, out: dict) -> str:
        return self.path(bid, "outputs", out["id"] + (".mp4" if out["kind"] == "video" else ".m4b"))

    # ---- persistence ----
    def save(self, state: dict) -> None:
        path = self.path(state["id"], STATE_NAME)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def get(self, bid: str) -> dict | None:
        path = self.path(bid, STATE_NAME)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                value = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict):
            return None
        return self._migrate(value)

    def _migrate(self, state: dict) -> dict:
        # Batches saved by the first version: items had only `index` (== file
        # number) and at most one merged.m4b / video.mp4 at the batch root.
        for it in state.get("items", []):
            it.setdefault("uid", it["index"])
            # A crash leaves "running" behind; the audio file decides the truth.
            if it["status"] == "running":
                it["status"] = "pending"
        state["nextUid"] = max([state.get("nextUid", 1)] + [it["uid"] + 1 for it in state["items"]])
        state.setdefault("outputs", [])
        for key, kind, fname in (("merged", "audio", "merged.m4b"), ("video", "video", "video.mp4")):
            old = state.pop(key, None)
            src = self.path(state["id"], fname)
            if old and os.path.isfile(src):
                oid = uuid.uuid4().hex[:8]
                os.makedirs(self.path(state["id"], "outputs"), exist_ok=True)
                os.replace(src, self.path(state["id"], "outputs",
                                          oid + (".mp4" if kind == "video" else ".m4b")))
                state["outputs"].append({
                    "id": oid, "kind": kind, "name": state["title"], "uids": [], "first": 1,
                    "last": len(state["items"]), "count": len(state["items"]),
                    "durationSeconds": old.get("durationSeconds"),
                    "sizeBytes": old.get("sizeBytes"), "created": state.get("created", "")})
        _renumber(state)
        return state

    def list(self) -> "list[dict]":
        out = []
        if os.path.isdir(self.base):
            for name in os.listdir(self.base):
                state = self.get(name)
                if state:
                    out.append(state)
        out.sort(key=lambda s: s.get("created", ""), reverse=True)
        return out

    def delete(self, bid: str) -> None:
        shutil.rmtree(self.dir(bid), ignore_errors=True)

    # ---- create / read / update batch ----
    def _new_item(self, state: dict, name: str, text: str) -> dict:
        uid = state["nextUid"]
        state["nextUid"] += 1
        it = {"uid": uid, "index": 0, "name": name, "chars": len(text), "status": "pending",
              "durationSeconds": None, "error": None}
        with open(self.item_file(state["id"], it, "txt"), "w", encoding="utf-8") as f:
            f.write(text)
        return it

    def create(self, bid: str, *, title, author, voice, speed, items) -> dict:
        """items: [(display_name, narrated_text)] already in final order."""
        os.makedirs(self.path(bid, "items"), exist_ok=True)
        os.makedirs(self.path(bid, "outputs"), exist_ok=True)
        state = {"id": bid, "title": title or "Batch", "author": author or "",
                 "voice": voice, "speed": float(speed),
                 "created": datetime.datetime.now().isoformat(timespec="seconds"),
                 "nextUid": 1, "items": [], "outputs": []}
        for name, text in items:
            state["items"].append(self._new_item(state, name, text))
        _renumber(state)
        self.save(state)
        return state

    def _load(self, bid: str) -> dict:
        state = self.get(bid)
        if state is None:
            raise FileNotFoundError(f"no such batch: {bid}")
        return state

    def update_batch(self, bid: str, *, title=None, author=None, voice=None, speed=None) -> dict:
        with self.lock:
            state = self._load(bid)
            if title is not None:
                state["title"] = title.strip() or state["title"]
            if author is not None:
                state["author"] = author.strip()
            if voice is not None:
                state["voice"] = voice
            if speed is not None:
                state["speed"] = max(0.5, min(1.5, float(speed)))
            self.save(state)
            return state

    def add_items(self, bid: str, items) -> dict:
        with self.lock:
            state = self._load(bid)
            for name, text in items:
                state["items"].append(self._new_item(state, name, text))
            _renumber(state)
            self.save(state)
            return state

    # ---- edit items (by 1-based position) ----
    def _find(self, state: dict, index: int) -> dict:
        for it in state["items"]:
            if it["index"] == index:
                return it
        raise BatchError(f"no file number {index}")

    def read_text(self, bid: str, index: int) -> str:
        it = self._find(self._load(bid), index)
        with open(self.item_file(bid, it, "txt"), encoding="utf-8") as f:
            return f.read()

    def _drop_audio(self, bid: str, it: dict) -> None:
        for ext in ("wav", "m4a", "part.wav"):
            try:
                os.remove(self.item_file(bid, it, ext))
            except OSError:
                pass
        it["status"], it["durationSeconds"], it["error"] = "pending", None, None

    def update_item(self, bid: str, index: int, *, name=None, text=None) -> dict:
        with self.lock:
            state = self._load(bid)
            it = self._find(state, index)
            if name is not None:
                it["name"] = name.strip() or it["name"]
            if text is not None:
                if not text.strip():
                    raise BatchError("text can't be empty")
                with open(self.item_file(bid, it, "txt"), "w", encoding="utf-8") as f:
                    f.write(text)
                it["chars"] = len(text)
                self._drop_audio(bid, it)        # old audio no longer matches the text
            self.save(state)
            return state

    def delete_items(self, bid: str, indexes) -> dict:
        with self.lock:
            state = self._load(bid)
            doomed = {self._find(state, i)["uid"] for i in indexes}
            if not doomed:
                raise BatchError("nothing selected")
            for it in [x for x in state["items"] if x["uid"] in doomed]:
                self._drop_audio(bid, it)
                try:
                    os.remove(self.item_file(bid, it, "txt"))
                except OSError:
                    pass
            state["items"] = [x for x in state["items"] if x["uid"] not in doomed]
            _renumber(state)
            self.save(state)
            return state

    def move_item(self, bid: str, index: int, to: int) -> dict:
        with self.lock:
            state = self._load(bid)
            it = self._find(state, index)
            if not 1 <= to <= len(state["items"]):
                raise BatchError("position out of range")
            state["items"].remove(it)
            state["items"].insert(to - 1, it)
            _renumber(state)
            self.save(state)
            return state

    def reset_items(self, bid: str, indexes) -> dict:
        """Throw away audio so the files are generated again."""
        with self.lock:
            state = self._load(bid)
            for i in indexes:
                self._drop_audio(bid, self._find(state, i))
            self.save(state)
            return state

    # ---- outputs (merged audio / videos) ----
    def _output(self, state: dict, oid: str) -> dict:
        for out in state["outputs"]:
            if out["id"] == oid:
                return out
        raise BatchError("no such output")

    def rename_output(self, bid: str, oid: str, name: str) -> dict:
        with self.lock:
            state = self._load(bid)
            out = self._output(state, oid)
            out["name"] = name.strip() or out["name"]
            self.save(state)
            return state

    def delete_output(self, bid: str, oid: str) -> dict:
        with self.lock:
            state = self._load(bid)
            out = self._output(state, oid)
            try:
                os.remove(self.output_file(bid, out))
            except OSError:
                pass
            state["outputs"].remove(out)
            self.save(state)
            return state


# ------------------------------------------------------------------ synthesis

def _chunk_total(texts, custom_rules) -> int:
    return sum(len(p) for t in texts for p in chunk_paragraphs(normalize_text(t, custom_rules)))


def _synth_item_wav(synth, text, wav_path, *, custom_rules, voice, speed, chunk_cache,
                    cancelled, on_chunk):
    """Synthesize one text to wav_path (atomically: .part.wav then rename)."""
    paragraphs = chunk_paragraphs(normalize_text(text, custom_rules))
    part = wav_path[:-4] + ".part.wav"
    wrote = False
    with sf.SoundFile(part, mode="w", samplerate=SAMPLE_RATE, channels=1, subtype="PCM_16") as wav:
        for pi, paragraph in enumerate(paragraphs):
            for ci, chunk in enumerate(paragraph):
                if cancelled():
                    raise RenderCancelled("cancelled")
                if wrote:
                    gap = PARAGRAPH_GAP if ci == 0 and pi > 0 else SENTENCE_GAP
                    wav.write(np.zeros(int(gap * SAMPLE_RATE), dtype=np.float32))
                key = {"version": 1, "voice": voice, "speed": float(speed), "text": chunk}
                audio = chunk_cache.get(key) if chunk_cache else None
                if audio is None:
                    audio = synth.synth_chunk_with_retry(chunk)
                    if chunk_cache:
                        chunk_cache.put(key, audio)
                wav.write(audio)
                wrote = True
                on_chunk()
        if not wrote:
            wav.write(np.zeros(int(0.5 * SAMPLE_RATE), dtype=np.float32))
    os.replace(part, wav_path)


def _encode_m4a(wav_path: str, m4a_path: str, title: str, ffmpeg: str = "ffmpeg") -> None:
    tmp = m4a_path + ".building.m4a"
    base = [ffmpeg, "-y", "-i", wav_path, "-metadata", f"title={title}",
            "-c:a", "aac", "-b:a", DEFAULT_BITRATE, tmp]
    try:
        try:
            procs.run(base[:3] + ["-af", MASTER_FILTERS] + base[3:], check=True,
                           capture_output=True, text=True)
        except subprocess.CalledProcessError:
            # loudnorm can fail on near-silent audio; fall back to unmastered.
            procs.run(base, check=True, capture_output=True, text=True)
        os.replace(tmp, m4a_path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def synthesize_batch(store: BatchStore, bid: str, *, emit, synth_factory, custom_rules=None,
                     cancelled=lambda: False, chunk_cache=None, only=None) -> dict:
    """Generate audio for every item that isn't done yet (this is also 'resume').

    only: optional collection of 1-based positions; other items are left alone.
    """
    state = store._load(bid)
    items = state["items"]
    only = set(only) if only else None

    texts = {}
    for it in items:
        with open(store.item_file(bid, it, "txt"), encoding="utf-8") as f:
            # build_book_text prefixes a "## name" chapter marker; it is not narration.
            texts[it["uid"]] = re.sub(r"\A\s*## [^\n]*\n?", "", f.read())
        # Trust the file system: a finished wav + m4a means the item is done.
        if os.path.isfile(store.item_file(bid, it, "wav")) and os.path.isfile(store.item_file(bid, it, "m4a")):
            it["status"] = "done"
            if it["durationSeconds"] is None:
                info = sf.info(store.item_file(bid, it, "wav"))
                it["durationSeconds"] = round(info.frames / info.samplerate, 1)
        elif it["status"] == "done":
            it["status"] = "pending"

    todo = [it for it in items if it["status"] != "done" and (only is None or it["index"] in only)]
    total_chunks = max(1, _chunk_total([texts[it["uid"]] for it in todo], custom_rules))
    item_totals = {it["uid"]: max(1, _chunk_total([texts[it["uid"]]], custom_rules)) for it in todo}
    item_done = 0
    done_chunks = 0
    failed = 0
    synth = synth_factory(state["voice"], float(state["speed"]))

    def progress(it):
        emit({"type": "progress", "phase": "audio", "chapterIndex": it["index"] - 1,
              "chapterCount": len(items), "chapterTitle": it["name"],
              "chunksDone": done_chunks, "chunkCount": total_chunks,
              "ts": time.time(), "itemIndex": it["index"], "itemChunksDone": item_done,
              "itemChunkCount": item_totals[it["uid"]],
              "percent": round(done_chunks / total_chunks * 100)})

    try:
        for it in todo:
            if cancelled():
                raise RenderCancelled("cancelled")
            it["status"], it["error"] = "running", None
            item_done = 0
            progress(it)

            def tick():
                nonlocal done_chunks, item_done
                done_chunks += 1
                item_done += 1
                progress(it)

            wav, m4a = store.item_file(bid, it, "wav"), store.item_file(bid, it, "m4a")
            try:
                _synth_item_wav(synth, texts[it["uid"]], wav,
                                custom_rules=custom_rules, voice=state["voice"],
                                speed=state["speed"], chunk_cache=chunk_cache,
                                cancelled=cancelled, on_chunk=tick)
                _encode_m4a(wav, m4a, it["name"])
                info = sf.info(wav)
                it["durationSeconds"] = round(info.frames / info.samplerate, 1)
                it["status"] = "done"
            except RenderCancelled:
                it["status"] = "pending"
                store.save(state)
                raise
            except ChunkSynthError as exc:
                # One bad file must not sink the other 49; report it and go on.
                it["status"], it["error"] = "error", str(exc)
                failed += 1
            # Any other exception (e.g. the TTS worker died) aborts; finished
            # items are already saved and are skipped on the next run.
            except Exception:
                it["status"] = "pending"
                store.save(state)
                raise
            store.save(state)
            emit({"type": "item", "index": it["index"], "status": it["status"],
                  "durationSeconds": it["durationSeconds"], "error": it["error"]})
    finally:
        if hasattr(synth, "close"):
            synth.close()

    emit({"type": "done", "batchId": bid, "failed": failed, "percent": 100})
    return state


# ------------------------------------------------------------ merge and video

def _select_done(state: dict, indexes) -> list:
    """Finished items chosen by position (all finished items when indexes is None)."""
    chosen = set(indexes) if indexes else None
    items = [it for it in state["items"]
             if it["status"] == "done" and (chosen is None or it["index"] in chosen)]
    if not items:
        raise BatchError("none of the selected files have finished audio")
    return items


def _default_name(state: dict, items: list, kind: str) -> str:
    first, last = items[0]["index"], items[-1]["index"]
    span = f"file {first}" if first == last else f"files {first}-{last}"
    return f"{state['title']} - {span}" + (" (video)" if kind == "video" else "")


def _build_output(store: BatchStore, bid: str, kind: str, *, emit, indexes, name,
                  cancelled=lambda: False):
    state = store._load(bid)
    items = _select_done(state, indexes)
    title = (name or "").strip() or _default_name(state, items, kind)
    out = {"id": uuid.uuid4().hex[:8], "kind": kind, "name": title,
           "uids": [it["uid"] for it in items], "first": items[0]["index"],
           "last": items[-1]["index"], "count": len(items),
           "durationSeconds": round(sum(it["durationSeconds"] or 0 for it in items), 1),
           "sizeBytes": 0, "created": datetime.datetime.now().isoformat(timespec="seconds")}
    phase = "video" if kind == "video" else "merge"
    title_txt = "Rendering video" if kind == "video" else "Merging audio"
    last = [-1]

    def on_progress(fraction):
        pct = round(fraction * 98)               # the last 2% is finishing the file
        if pct != last[0]:                       # one event per whole percent, not per ffmpeg tick
            last[0] = pct
            emit({"type": "progress", "phase": phase, "chapterTitle": title_txt,
                  "fraction": round(fraction, 4), "ts": time.time(), "percent": pct})

    on_progress(0.0)
    os.makedirs(store.path(bid, "outputs"), exist_ok=True)
    path = store.output_file(bid, out)
    chapters = [(it["name"], store.item_file(bid, it, "wav")) for it in items]
    # Each finished chapter already has a mastered AAC file; joining those is far faster
    # than re-mastering the WAVs (the builders fall back to the WAVs if they can't be joined).
    mastered = [store.item_file(bid, it, "m4a") for it in items]
    try:
        if kind == "video":
            images = [store.image_file(bid, i) for i in store.image_ids(bid)]
            out["cover"] = bool(images)
            out["imageCount"] = len(images)
            build_mp4(chapters, path, book_title=title, author=state["author"] or None,
                      cover=images[0] if images else None, cover_fit="contain",
                      progress=on_progress, cancelled=cancelled, audio_files=mastered,
                      slideshow=images if len(images) > 1 else None)
        else:
            build_m4b(chapters, path, book_title=title, author=state["author"] or None,
                      progress=on_progress, cancelled=cancelled, audio_files=mastered)
    except procs.Cancelled as exc:
        raise RenderCancelled("cancelled") from exc
    out["sizeBytes"] = os.path.getsize(path)
    with store.lock:
        state = store._load(bid)            # re-read: other edits may have landed
        state["outputs"].append(out)
        store.save(state)
    emit({"type": "done", "batchId": bid, "outputId": out["id"], "percent": 100})
    return out


def merge_batch(store: BatchStore, bid: str, *, emit, indexes=None, name=None,
                cancelled=lambda: False) -> dict:
    """Join the chosen finished files (default: all) into one chaptered .m4b."""
    return _build_output(store, bid, "audio", emit=emit, indexes=indexes, name=name,
                         cancelled=cancelled)


def video_batch(store: BatchStore, bid: str, *, emit, indexes=None, name=None,
                cancelled=lambda: False) -> dict:
    """Render a 1080p video of the chosen finished files (default: all)."""
    return _build_output(store, bid, "video", emit=emit, indexes=indexes, name=name,
                         cancelled=cancelled)

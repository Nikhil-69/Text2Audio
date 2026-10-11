"""Kokoro-based synthesis and audio assembly helpers."""
import os
import numpy as np

SAMPLE_RATE = 24000
SENTENCE_GAP = 0.15
PARAGRAPH_GAP = 0.6


class FatalSynthError(Exception):
    """An unrecoverable synthesis failure (e.g. the cloning worker died).

    The per-chunk retry/skip logic deliberately does NOT swallow this — it aborts
    the render so the failure is surfaced rather than yielding a silent book."""


class ChunkSynthError(FatalSynthError):
    """Raised after a text chunk exhausts its synthesis retries."""

# voice_id -> lang_code (lang_code must match the voice's language prefix).
PRESET_VOICES: dict[str, str] = {
    # American English — female
    "af_heart": "a", "af_bella": "a", "af_nicole": "a", "af_sarah": "a", "af_sky": "a",
    # American English — male
    "am_michael": "a", "am_adam": "a", "am_echo": "a", "am_liam": "a",
    # British English — female
    "bf_emma": "b", "bf_isabella": "b", "bf_alice": "b",
    # British English — male
    "bm_george": "b", "bm_lewis": "b", "bm_daniel": "b",
}


def concat_with_gaps(audio_arrays, gap_seconds: float = 0.3, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    arrays = [a for a in audio_arrays if a is not None and len(a) > 0]
    if not arrays:
        return np.zeros(0, dtype=np.float32)
    gap = np.zeros(int(gap_seconds * sample_rate), dtype=np.float32)
    out = []
    for i, a in enumerate(arrays):
        if i:
            out.append(gap)
        out.append(np.asarray(a, dtype=np.float32))
    return np.concatenate(out)


class BaseSynthesizer:
    """Engine-agnostic synthesis: subclasses implement synth_chunk(text)."""

    def synth_chunk(self, text: str) -> np.ndarray:
        raise NotImplementedError

    def synth_chunk_with_retry(self, text: str) -> np.ndarray:
        """Synthesize one chunk, retrying once and never silently dropping text."""
        last_error = None
        audio = None
        for _attempt in range(2):
            try:
                audio = self.synth_chunk(text)
                break
            except FatalSynthError:
                raise
            except Exception as exc:
                last_error = exc
        if audio is None or len(audio) == 0:
            detail = f": {last_error}" if last_error else " (empty audio returned)"
            raise ChunkSynthError(f"synthesis failed after 2 attempts{detail}") from last_error
        return np.asarray(audio, dtype=np.float32)

    def synth_chunks(self, chunks, progress=None) -> np.ndarray:
        out = []
        for i, chunk in enumerate(chunks):
            audio = self.synth_chunk_with_retry(chunk)
            out.append(audio)
            if progress is not None:
                progress(i + 1, len(chunks))
        return concat_with_gaps(out, gap_seconds=0.3)

    def synth_paragraphs(self, paragraphs, progress=None) -> np.ndarray:
        total = sum(len(p) for p in paragraphs)
        done = 0
        para_audios = []
        for para in paragraphs:
            chunk_audios = []
            for chunk in para:
                audio = self.synth_chunk_with_retry(chunk)
                chunk_audios.append(audio)
                done += 1
                if progress is not None:
                    progress(done, total)
            joined = concat_with_gaps(chunk_audios, gap_seconds=SENTENCE_GAP)
            if len(joined) > 0:
                para_audios.append(joined)
        return concat_with_gaps(para_audios, gap_seconds=PARAGRAPH_GAP)

    def release_memory(self) -> None:
        """Hand cached memory back to the OS (call between long jobs/items)."""
        free_memory()

    def preview(self, text: str = "This is a sample of the selected narrator voice.") -> np.ndarray:
        return self.synth_chunk(text)


class Synthesizer(BaseSynthesizer):
    def __init__(self, voice: str = "af_heart", lang_code: str = "a",
                 device: str | None = None, speed: float = 1.0):
        if device is None:
            device = select_device()
        from kokoro import KPipeline
        self.pipeline = KPipeline(lang_code=lang_code, device=device)
        self.voice = voice
        self.speed = speed

    def synth_chunk(self, text: str) -> np.ndarray:
        parts = [audio for _, _, audio in self.pipeline(text, voice=self.voice, speed=self.speed)]
        return concat_with_gaps(parts, gap_seconds=0.0)


def free_memory() -> None:
    """Run the GC and flush torch's device allocator caches, if torch is loaded."""
    import gc
    import sys
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is None:
        return
    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        mps = getattr(torch, "mps", None)
        if mps is not None and torch.backends.mps.is_available():
            mps.empty_cache()
    except Exception:
        pass


def select_device() -> str:
    """Select the best available torch device, with an explicit env override."""
    override = os.environ.get("T2A_DEVICE", "").strip().lower()
    if override:
        if override not in {"cuda", "mps", "cpu"}:
            raise ValueError("T2A_DEVICE must be cuda, mps, or cpu")
        return override
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"

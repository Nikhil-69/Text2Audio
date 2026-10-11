"""Local Gradio web app: plain-text book -> YouTube-ready .mp4 audiobook video."""
import os
import re

import numpy as np
import gradio as gr

from pipeline.parse import parse_chapters
from pipeline.chunk import chunk_text
from pipeline.synth import Synthesizer, PRESET_VOICES, SAMPLE_RATE
from pipeline.assemble import write_wav, build_mp4

OUTPUT_DIR = "output"


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _require_voice(voice):
    if voice not in PRESET_VOICES:
        raise gr.Error("Please select a narrator voice.")


def _safe_filename(name: str) -> str:
    """Make an output filename safe on Windows/macOS/Linux."""
    cleaned = re.sub(
        r"[^\w.-]+",
        "_",
        (name or "").strip(),
    ).strip("_.")

    return cleaned or "audiobook"


def detect_chapters(txt_file, marker, book_title):
    if txt_file is None:
        return [["(upload a .txt file first)"]]

    chapters = parse_chapters(
        _read_text(txt_file),
        marker=marker or "## ",
        default_title=book_title or "Audiobook",
    )

    if not chapters:
        return [["(no text found)"]]

    return [
        [
            i + 1,
            c.title,
            len(c.text),
        ]
        for i, c in enumerate(chapters)
    ]


def preview_voice(voice):
    _require_voice(voice)

    synth = Synthesizer(
        voice=voice,
        lang_code=PRESET_VOICES[voice],
    )

    return (
        SAMPLE_RATE,
        synth.preview(),
    )


def generate(
    txt_file,
    voice,
    book_title,
    author,
    cover,
    speed,
    marker,
    progress=gr.Progress(),
):
    if txt_file is None:
        raise gr.Error(
            "Please upload a .txt file first."
        )

    _require_voice(voice)

    chapters = parse_chapters(
        _read_text(txt_file),
        marker=marker or "## ",
        default_title=book_title or "Audiobook",
    )

    if not chapters:
        raise gr.Error(
            "No readable text found in the file."
        )

    os.makedirs(
        OUTPUT_DIR,
        exist_ok=True,
    )

    synth = Synthesizer(
        voice=voice,
        lang_code=PRESET_VOICES[voice],
        speed=float(speed),
    )

    chapter_wavs = []

    try:

        for ci, ch in enumerate(chapters):

            progress(
                ci / len(chapters),
                desc=(
                    f"Chapter {ci + 1}/"
                    f"{len(chapters)}: "
                    f"{ch.title}"
                ),
            )

            audio = synth.synth_chunks(
                chunk_text(ch.text)
            )

            if len(audio) == 0:
                audio = np.zeros(
                    int(0.5 * SAMPLE_RATE),
                    dtype=np.float32,
                )

            wav_path = os.path.join(
                OUTPUT_DIR,
                f"chapter_{ci + 1:03d}.wav",
            )

            write_wav(
                audio,
                wav_path,
            )

            chapter_wavs.append(
                (
                    ch.title,
                    wav_path,
                )
            )

    finally:

        if hasattr(synth, "close"):
            synth.close()

    out_path = os.path.join(
        OUTPUT_DIR,
        f"{_safe_filename(book_title)}.mp4",
    )

    progress(
        0.99,
        desc="Creating YouTube-ready MP4...",
    )

    build_mp4(
        chapter_wavs,
        out_path,
        book_title=book_title or None,
        author=author or None,
        cover=cover,
        master=True,
    )

    progress(
        1.0,
        desc="MP4 complete.",
    )

    return out_path


VOICE_CHOICES = list(
    PRESET_VOICES.keys()
)


with gr.Blocks(
    title="Text2Audio"
) as demo:

    gr.Markdown(
        "# Text2Audio\n"
        "Turn a plain-text book into a "
        "YouTube-ready audiobook video (.mp4)."
    )

    with gr.Row():

        with gr.Column():

            txt_file = gr.File(
                label="Book (.txt)",
                file_types=[".txt"],
                type="filepath",
            )

            voice = gr.Dropdown(
                VOICE_CHOICES,
                value="af_heart",
                label="Narrator voice",
            )

            preview_btn = gr.Button(
                "Preview voice"
            )

            preview_audio = gr.Audio(
                label="Voice preview"
            )

            book_title = gr.Textbox(
                label="Book title (optional)"
            )

            author = gr.Textbox(
                label="Author (optional)"
            )

            cover = gr.Image(
                label="Background image (optional)",
                type="filepath",
            )

            speed = gr.Slider(
                0.5,
                1.5,
                value=1.0,
                step=0.05,
                label="Speaking speed",
            )

            marker = gr.Textbox(
                value="## ",
                label="Chapter marker prefix",
            )

        with gr.Column():

            detect_btn = gr.Button(
                "Detect chapters"
            )

            chapters_table = gr.Dataframe(
                headers=[
                    "#",
                    "Title",
                    "Chars",
                ],
                label="Detected chapters",
                interactive=False,
            )

            generate_btn = gr.Button(
                "Generate Audiobook MP4",
                variant="primary",
            )

            result = gr.File(
                label="Your YouTube-ready MP4"
            )

    preview_btn.click(
        preview_voice,
        inputs=voice,
        outputs=preview_audio,
    )

    detect_btn.click(
        detect_chapters,
        inputs=[
            txt_file,
            marker,
            book_title,
        ],
        outputs=chapters_table,
    )

    generate_btn.click(
        generate,
        inputs=[
            txt_file,
            voice,
            book_title,
            author,
            cover,
            speed,
            marker,
        ],
        outputs=result,
    )


if __name__ == "__main__":
    demo.launch(
        inbrowser=True
    )
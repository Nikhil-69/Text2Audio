# Installing Text2Audio — the friendly guide

This is the plain-English guide for getting Text2Audio running on **Windows**, with
no command-line knowledge needed. If you're comfortable with a terminal, the
[README](README.md) has the developer setup and the `pip` install instead.

---

## On a Mac

Download and unzip the project, then double-click **`Install Text2Audio.command`**
(right-click → **Open** the first time, since it isn't signed). It installs everything
(Python, ffmpeg, espeak-ng, the app's dependencies) and puts **Text2Audio** in your
Applications folder. Keep the unzipped folder where it is — the app runs from it. Move it
out of Downloads/Desktop/Documents if macOS blocks access to it.

---

## 1. Download

1. Go to the project page: <https://github.com/mooja77/Text2Audio>
2. Click the green **Code** button → **Download ZIP**
   (or use this direct link: **[Download ZIP](https://github.com/mooja77/Text2Audio/archive/refs/heads/master.zip)**).
3. Find the downloaded `Text2Audio-master.zip`, right-click it → **Extract All…**, and
   pick a sensible spot like your **Desktop** or **Documents** folder.

You'll end up with a folder called `Text2Audio-master` (or similar) containing
`Install Text2Audio.bat`, `Start Text2Audio.bat`, and the rest of the app.

---

## 2. Install (one time)

Double-click **`Install Text2Audio.bat`**.

A black window opens and walks through the setup automatically:

- Installs **Python** if you don't already have it.
- Creates a private workspace for the app (a `.venv` folder) so it doesn't touch the
  rest of your system.
- Detects whether you have an **NVIDIA graphics card**. If you do, it installs the fast
  GPU version; if not, it installs the CPU version. At runtime Text2Audio automatically
  chooses CUDA, Apple MPS, or CPU (CPU still works — just slower).
- Installs the app's dependencies, plus **ffmpeg** and **espeak-ng** (the tools that
  build the audiobook file and help pronounce words).
- Adds a **Text2Audio** shortcut to your Desktop.

This first install downloads a fair amount and can take several minutes. Let it run
until you see the green **"Done!"** message, then close the window.

### "Windows protected your PC" (SmartScreen)

Because these `.bat` files aren't signed by a big company, Windows may show a blue
**"Windows protected your PC"** box. This is normal for small open-source tools.

- Click **More info**, then **Run anyway** (see the picture below).
- The `.bat` files are short, plain-text scripts — you're welcome to open them in
  Notepad first and read exactly what they do.

![How to get past the Windows SmartScreen prompt: click "More info", then "Run anyway"](assets/smartscreen.png)

### If something needs admin permission

The installer uses Windows' built-in **winget** to fetch Python/ffmpeg/espeak-ng. If a
step asks for permission or fails because you're not an administrator, right-click
`Install Text2Audio.bat` and choose **Run as administrator**, then try again. The
installer is safe to re-run — it skips anything that's already done.

---

## 3. Run

Double-click **`Start Text2Audio`** on your Desktop (or `Start Text2Audio.bat` in the
folder).

- A small black window opens and stays open — that's the app running. **Leave it open**
  while you use Text2Audio; closing it stops the app.
- Your web browser opens automatically to the Text2Audio Studio.
- Drag in your manuscript (`.txt`, Markdown, DOCX, EPUB or text-based PDF), review the
  detected chapters, optionally edit the prepared text/add cover art, pick a voice,
  and click **Generate**.

When you're finished, just close the black window.

> The **first** time you generate audio, the voice model (a few hundred MB) downloads
> once. After that, Text2Audio works completely offline.

---

## Where your files live

Everything stays inside the Text2Audio folder you unzipped:

- **`library/`** — your finished audiobooks (`.m4b` files with chapter markers).
- **`voices/`** — any voices you've cloned.
- **`output/`** — working/intermediate audio.
- **`data/chunk-cache/`** — reusable local narration chunks that make unchanged
  rerenders faster. Delete this folder while Text2Audio is stopped to clear the cache.

Nothing is uploaded anywhere — your text and audio never leave your computer.

---

## Adding voice cloning (optional)

Text2Audio can narrate in a cloned voice using a heavier optional engine called
**F5-TTS**. It's not installed by default (it's large and slower). To add it:

1. Open the Text2Audio folder.
2. Hold **Shift**, right-click an empty area, and choose
   **Open PowerShell window here** (or **Open in Terminal**).
3. Install F5-TTS:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install f5-tts
   ```

4. Re-pin `torchaudio` so it matches the rest of the app (F5-TTS sometimes pulls a
   mismatched version). Use the line that matches your machine:

   - **NVIDIA GPU** (the installer set up the CUDA build):

     ```powershell
     .\.venv\Scripts\python.exe -m pip install "torchaudio==2.6.0" --index-url https://download.pytorch.org/whl/cu124
     ```

   - **No NVIDIA GPU** (CPU build — leave off the CUDA index, or you'll pull a GPU build
     onto a CPU-only setup and break it):

     ```powershell
     .\.venv\Scripts\python.exe -m pip install "torchaudio==2.6.0"
     ```

Then start the app and go to **Voices → Clone a voice**: give it a name, upload about
10–30 seconds of clean speech, and optionally paste a transcript of that clip for the
best quality.

> ⚠️ **Only clone voices you have the right to use** — your own voice, voices you have
> permission to clone, or public-domain recordings. Don't impersonate people or create
> deceptive audio.

---

## Uninstalling

Text2Audio doesn't scatter files around your system. To remove it:

1. **Delete the Text2Audio folder** you unzipped. That removes the app, its private
   `.venv` workspace, and your library/voices (back up anything in `library/` first if
   you want to keep your audiobooks).
2. **Delete the Desktop shortcut** ("Text2Audio").
3. *(Optional)* The installer may have added **Python**, **ffmpeg**, and **espeak-ng**
   via winget. If you don't want those anymore, remove them from
   **Settings → Apps → Installed apps**. (Leave them if other programs use them.)
4. *(Optional)* The downloaded voice model is cached in your user folder under
   `.cache\huggingface`. You can delete that folder to reclaim the space.

---

## Troubleshooting

**"Text2Audio is not installed yet."**
You double-clicked **Start** before **Install** finished. Run
`Install Text2Audio.bat` first and wait for the green "Done!" message.

**The browser didn't open.**
The app might still be running — open your browser and go to
<http://127.0.0.1:8765> manually.

**The black window flashes and disappears.**
Run the `.bat` from a PowerShell window so you can read the error: Shift-right-click in
the folder → **Open PowerShell window here**, then type `.\"Start Text2Audio.bat"` and
press Enter. Copy any error message into a
[GitHub issue](https://github.com/mooja77/Text2Audio/issues).

**Generation is very slow.**
You're probably on the CPU build (no NVIDIA GPU detected). It still works, just slowly —
try a shorter chapter first, or run on a machine with an NVIDIA graphics card.

**Words are mispronounced.**
Use the **Pronunciations** tab in the app to add a fix for tricky names, then re-generate.

**Still stuck?**
Open an issue at <https://github.com/mooja77/Text2Audio/issues> with what you tried and
any error text — we're happy to help.

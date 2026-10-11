# Text2Audio help

Use your browser's page search (`Ctrl+F` or `Cmd+F`) here, or open the searchable **Help** tab in Text2Audio Studio.

## Start in five steps

1. Add `.txt`, Markdown, DOCX, EPUB or text-based PDF files. **Use safe example** is available in the Create tab.
2. Check the detected chapters and their order.
3. Choose and preview a narrator.
4. Generate, leaving the Text2Audio process open.
5. Listen to the finished `.m4b` in Library.

Experienced users can skip the guide and install with `pip`; the [README](../README.md) has the fast path.

## Recovery

An active render continues if only the browser page is refreshed. The page stores the non-sensitive job ID and reconnects to the server's event history. If the Text2Audio process itself stops, start that render again. Finished books remain in `library/` with durable manifests.

Text2Audio remembers title, author, voice, speed and guide progress in local browser storage. It deliberately does not store manuscript text there.

## Common problems

- **Browser did not open:** visit <http://127.0.0.1:8765> while Text2Audio is running.
- **ffmpeg or espeak-ng missing:** rerun the installer, or install both and restart.
- **Slow generation:** CPU works slowly; try the short safe example or an NVIDIA GPU.
- **Wrong device selected:** set `T2A_DEVICE` to `cuda`, `mps`, or `cpu` before starting.
- **Need to stop:** use **Cancel render**; completed books and prior remasters remain intact.
- **Publishing:** use the quality report and chapter export in Library. Distribution
  presets still require editorial listening and the distributor's own validation.
- **Wrong pronunciation:** add a rule in the Pronounce tab, preview, and regenerate.
- **First model download:** the first narration downloads model files; later runs use the local cache.

## Privacy and safe support

The server binds to `127.0.0.1`. Manuscripts, generated audio, cloned voices, and manifests stay on this computer. Text2Audio has no account system, telemetry, lifecycle email, or hosted processing.

GitHub issues are public. Do not attach private manuscripts, cloned voices, copyrighted source text, or logs containing private text. Use the synthetic example or another tiny synthetic reproduction.

- [Ask for help](https://github.com/Nikhil-69/Text2Audio/issues/new?template=bug_report.yml)
- [Request a feature](https://github.com/Nikhil-69/Text2Audio/issues/new?template=feature_request.yml)

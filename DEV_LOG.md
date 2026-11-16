# Studio G Development Log

## 2026-11-16 — Verified Spruked production artifact

- Imported `https://www.spruked.com` into the local source store.
- Generated a valid 429-word Phil/Jim script using `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`.
- Stored the episode record and script under `%USERPROFILE%\PodcastStudio\episodes\2507d69a-724d-4688-a7c9-b6fa6bda3632\record.json`.
- VoiceStudio generated 15 WAV segments and G normalized them to MP3.
- FFmpeg concatenated the segments into `episode.mp3`.
- ffprobe measured the final MP3 at 151.01 seconds.
- API download verification returned HTTP 200 with `audio/mpeg`.

## Known runtime notes

- Runtime artifacts are outside the repository under `%USERPROFILE%\PodcastStudio\`.
- NVIDIA API authentication is supplied by `NVIDIA_API_KEY`; the launcher supplies the hosted G default model.
- The hosted endpoint returned empty dialogue content with `finish_reason=length` for the production reasoning request. G now logs the stage and returns HTTP 502 instead of HTTP 500.
- Windows Tesseract executable is still not installed/on PATH; `pytesseract` alone is insufficient.
- VoiceStudio STT is not implemented; VoiceStudio TTS is integrated and verified.

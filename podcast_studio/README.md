# The Dandy Show Studio — desktop production source

A native Tauri 2 / Rust / React interface backed by local FastAPI, NVIDIA Nemotron, VoiceStudio and FFmpeg. The normal Windows entrypoint is `start-windows.ps1`, which launches the API and Tauri desktop window. The app does not require a browser to use its interface.

## What's implemented

- Create Show: ingest HTTPS websites, paste text, import PDF/DOCX/PPTX/TXT/MD/CSV/JSON/HTML; image analysis when a compatible NVIDIA vision model is configured. Choose sources, title, target length and optional editorial direction.
- Episodes: Nemotron creates an outline and two-host dialogue. Browse saved episodes, review transcript, assign up to four independent ad files, and initiate a production job with progress and failure status.
- Voice Library: import and normalize voice reference samples to 24 kHz mono WAV; persist a `voices.json` handoff manifest; assign samples to Phil/Jim; store a VoiceStudio voice ID; preview a voice through VoiceStudio. **Voice registration/cloning is not automatically performed**: the endpoint's supported reference/registration contract must be established first. VoiceStudio receives the configured voice ID or name, not arbitrary reference audio.
- Ad Studio: generate, edit, synthesize and download reusable commercials independently of episodes. Opening, two midrolls and closing slots can be replaced and the episode remixed without re-rendering dialogue.
- Exports: finished MP3, branded static-cover vertical MP4, 9:16 social clips, episode script, approximate dialogue SRT, and AI-written social copy.
- Health: connection reporting for VoiceStudio, Orbot, Nemotron configuration, and FFmpeg.
- Local OCR: PNG, JPG/JPEG, TIFF and BMP uploads are transcribed with CPU-only `pytesseract` when the Windows Tesseract executable is installed; original images are retained and OCR is marked as unverified.

## Installation (Windows)

1. Install Python 3.11+, Node.js 20+, Rust, Microsoft C++ Build Tools, WebView2, and FFmpeg/ffprobe on PATH.
   Install the maintained Python wrapper with `pip install -r backend/requirements.txt`, and install the Windows Tesseract OCR executable separately. Verify both with `python -c "import pytesseract; print(pytesseract.get_tesseract_version())"` and `tesseract --version`. If Tesseract is installed outside PATH, set `pytesseract.pytesseract.tesseract_cmd` in the backend environment before starting the API.
2. Open PowerShell in this folder and run `./setup-windows.ps1` once.
3. Set `NVIDIA_API_KEY` and `NEMOTRON_MODEL` in the backend environment (or a local `.env` not committed to source control). Do not put the API key in React or distribute it in the ZIP.
4. Start VoiceStudio at `http://127.0.0.1:3900`. Its actual model and request payload may differ from the default OpenAI-compatible speech format in `.env.example`; update `VOICESTUDIO_PAYLOAD_JSON` to match the service.
5. Run `./start-windows.ps1` to start FastAPI and launch the Tauri desktop window.

## VoiceStudio handoff

Samples are stored under `%USERPROFILE%/PodcastStudio/voice_library/samples/` by default. `voice_library/voices.json` persists host assignments and VoiceStudio voice IDs. `GET /voices/{id}/handoff` provides the absolute sample path and its local URL. Once VoiceStudio registers a voice, set its returned ID in the Voice Library UI. This is deliberately an explicit handoff, not a claim of automatic voice cloning.

## Verification and limitations

Backend tests: `python -m pytest -q tests`.
Frontend build: `cd frontend && npm install && npm run build`.
Tauri desktop build: `cd frontend && npm run tauri build` (requires Windows desktop toolchain).

**Do not call this production-ready until a live Nemotron → VoiceStudio → FFmpeg → MP3 → MP4 run has been completed and listened to.** The NVIDIA key and VoiceStudio services are not available in this build environment. A browser/Vite preview is optional for development only. Current video uses branded static artwork; it is not a fully animated visual editor. The generated SRT is approximate dialogue timing and does not account for inserted commercial offsets. Audio quality, voice cloning, and factual accuracy need review before publication.

Security: the API binds to loopback, source URL ingestion blocks private addresses, and credentials stay server-side. For use beyond a trusted local desktop, add authentication, robust DNS-rebinding defenses, upload scanning and resource limits.

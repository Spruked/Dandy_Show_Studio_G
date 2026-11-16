# The Dandy Show Studio — desktop production source

A native Tauri 2 / Rust / React desktop application backed by local FastAPI, NVIDIA Nemotron, VoiceStudio and FFmpeg. The normal Windows entrypoint is `start-windows.ps1`, which launches the API and Tauri desktop window. A browser is not the product runtime.

## What's implemented

- Create Show: ingest HTTPS websites, paste text, import PDF/DOCX/PPTX/TXT/MD/CSV/JSON/HTML; image analysis when a compatible NVIDIA vision model is configured. Choose sources, title, target length and optional editorial direction.
- Episodes: Nemotron creates an outline and two-host dialogue. Browse saved episodes, review transcript, assign up to four independent ad files, and initiate a production job with progress and failure status.
- Voice Library: import and normalize voice reference samples to 24 kHz mono WAV; persist a `voices.json` handoff manifest; assign samples to Phil/Jim; store a VoiceStudio voice ID; preview a voice through VoiceStudio. G currently contains the imported Phil Dandy (`0c3188b8`), Jim Dandy (`d745d34c`), male announcer (`echo`), and female announcer (`shimmer`) profiles.
- Ad Studio: generate, edit, synthesize and download reusable commercials independently of episodes. Opening, two midrolls and closing slots can be replaced and the episode remixed without re-rendering dialogue.
- Exports: finished MP3, branded static-cover vertical MP4, 9:16 social clips, episode script, approximate dialogue SRT, and AI-written social copy.
- Health: connection reporting for VoiceStudio, Orbot, Nemotron configuration, and FFmpeg.
- Local OCR: PNG, JPG/JPEG, TIFF and BMP uploads are transcribed with CPU-only `pytesseract` when the Windows Tesseract executable is installed; original images are retained and OCR is marked as unverified.

## Installation (Windows)

1. Install Python 3.11+, Node.js 20+, Rust, Microsoft C++ Build Tools, WebView2, and FFmpeg/ffprobe on PATH.
   Install the maintained Python wrapper with `pip install -r backend/requirements.txt`, and install the Windows Tesseract OCR executable separately. Verify both with `python -c "import pytesseract; print(pytesseract.get_tesseract_version())"` and `tesseract --version`. If Tesseract is installed outside PATH, set `pytesseract.pytesseract.tesseract_cmd` in the backend environment before starting the API.
2. Open PowerShell in this folder and run `./setup-windows.ps1` once.
3. Set `NVIDIA_API_KEY` in the backend environment (or a local `.env` not committed to source control). G supplies the hosted default model `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`; an optional `NEMOTRON_MODEL` override is supported. Do not put the API key in React or distribute it in the ZIP.
4. Start VoiceStudio at `http://127.0.0.1:3900`. Its actual model and request payload may differ from the default OpenAI-compatible speech format in `.env.example`; update `VOICESTUDIO_PAYLOAD_JSON` to match the service.
5. Run `./start-windows.ps1` to start FastAPI and launch the Tauri desktop window.

### Optional development fallback (not the product runtime)

Only for troubleshooting frontend/API work, if Rust/Cargo is not installed, the services can be started separately:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --app-dir backend --host 127.0.0.1 --port 8765
cd frontend
npm run dev -- --host 127.0.0.1
```

This exposes a temporary Vite browser preview at `http://127.0.0.1:5173`; the API is available at `http://127.0.0.1:8765`. It does not replace the Tauri application. The supported user experience is the native Tauri window and requires Cargo plus the Windows desktop toolchain.

## VoiceStudio handoff

Samples are stored under `%USERPROFILE%/PodcastStudio/voice_library/samples/` by default. `voice_library/voices.json` persists host assignments and VoiceStudio voice IDs. `GET /voices/{id}/handoff` provides the absolute sample path and its local URL. Once VoiceStudio registers a voice, set its returned ID in the Voice Library UI. This is deliberately an explicit handoff, not a claim of automatic voice cloning.

## Runtime data and verified production artifact

The repository contains source code only. Runtime records and generated media are stored at `%USERPROFILE%\PodcastStudio\`, not inside the Git working tree.

The verified Spruked test episode is stored at:

```text
%USERPROFILE%\PodcastStudio\episodes\2507d69a-724d-4688-a7c9-b6fa6bda3632\
```

Its `record.json` contains the 429-word Phil/Jim script. The folder contains 15 VoiceStudio WAV/MP3 segments, `concat.txt`, `dialogue.json`, and `episode.mp3`. The final MP3 was validated with ffprobe at 151.01 seconds and served successfully through the API download route.

## Verification and limitations

Backend tests: `python -m pytest -q tests` (currently 10 passing).
Frontend build: `cd frontend && npm install && npm run build`.
Tauri desktop build: `cd frontend && npm run tauri build` (requires Windows desktop toolchain).

The Nemotron → VoiceStudio → FFmpeg → MP3 path has been exercised successfully for the Spruked test episode. The current machine has `pytesseract` in the project virtual environment, but the separate Windows `tesseract.exe` executable is not installed/on PATH; OCR uploads will report that requirement until it is installed. Full three-minute dialogue generation requires a reasoning control that the hosted endpoint rejected with HTTP 422; the verified script was generated with reasoning disabled for that direct request. Current video uses branded static artwork; it is not a fully animated visual editor. The generated SRT is approximate dialogue timing and does not account for inserted commercial offsets. Audio quality, voice cloning, and factual accuracy need review before publication.

Security: the API binds to loopback, source URL ingestion blocks private addresses, and credentials stay server-side. For use beyond a trusted local desktop, add authentication, robust DNS-rebinding defenses, upload scanning and resource limits.

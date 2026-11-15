# The Dandy Show Studio V4 — Session Handoff

Active project: `C:\\Users\\bryan\\Desktop\\podcast_ studio_G`

Git checkpoints:

- `ff8d2f6` — untouched V4 baseline
- `947d450` — VoiceStudio reusable profile integration
- `8a10dcf` — NVIDIA credential normalization

## Verified

- Backend tests: 8 passed.
- Frontend production build: passed.
- Python compilation: passed.
- VoiceStudio 0.5.8-beta.2 is reachable at `http://127.0.0.1:3900`.
- VoiceStudio `/profiles` registration passed.
- VoiceStudio `/v1/audio/speech` synthesis passed.
- Generated WAV validated with ffprobe: 4.15 seconds, 199 KB.

## Current blocker

NVIDIA model discovery succeeds, including `nvidia/llama-3.1-nemotron-51b-instruct`, but every discovered Nemotron inference attempt returns HTTP 404 `Function not found for account`. Confirm the NVIDIA account's invokable deployment or correct inference endpoint before rerunning generation. Do not guess a model ID.

## Next session

1. Resolve NVIDIA model entitlement/endpoint.
2. Set `NEMOTRON_MODEL` only in the backend process or ignored local `.env`.
3. Run source → Nemotron outline/dialogue → two VoiceStudio voices → FFmpeg → playable MP3.
4. Validate and listen to the MP3, then commit the production checkpoint.
5. Continue visual redesign and additional exports only after the audio checkpoint passes.

## Toolchain

Python and Node dependencies are installed locally. FFmpeg/ffprobe are available. Rust/Cargo are not installed, so the native Tauri build remains unverified.

Never commit NVIDIA credentials or local `.env` files. Generated media stays outside Git. The old folder may remain as an empty Windows-locked directory; use the renamed folder above.

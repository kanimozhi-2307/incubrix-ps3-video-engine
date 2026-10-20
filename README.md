# IncuBrix PS3 — Script and Voiceover to B-roll Video Engine

Assessment implementation scaffold. **Do not claim all requirements pass until you run the test pack and verify output videos.**

## Current implementation scope
- FastAPI endpoints for health/readiness, JSON job submission, uploaded audio, status, and downloads.
- In-process background worker and idempotency key handling.
- Script-to-speech using eSpeak NG.
- Optional audio transcription using faster-whisper.
- Optional Pexels video search (`PEXELS_API_KEY`); otherwise original procedural motion-graphic fallback.
- FFmpeg rendering for 16:9, 9:16, and 1:1; SRT; `timeline.json`; `manifest.json`.
- pytest starter tests and a batch runner.

## Known limitations 
- Current jobs/idempotency are in memory, not durable across restarts. Redis is in Compose but the app does not yet use it as a queue.
- MinIO is provisioned but output files currently use local volume storage; S3-compatible output integration remains to be implemented.
- The callback URL and HMAC signature are not implemented.
- `/api/v1/broll/jobs/{job_id}/scenes/{scene_index}/swap` explicitly returns 501.
- Burn-in captions are not implemented yet; SRT is generated. `captions.burn_in` is not currently honoured.
- The audio-only path needs faster-whisper successfully installed and a model downloaded. If transcription fails, it uses a generic fallback scene rather than fabricating a transcript.
- The Pexels adapter is optional and requires a key. Verify the current Pexels licence and asset-level terms before commercial use. The API search is not a guarantee that a chosen clip is suitable or cleared for every use.
- Relevance scores are not independently model-scored. They are `null`; human ratings must be added to the test pack.
- Pacing, scene-boundary accuracy, aspect-ratio subject framing, silent-audio detection and colour normalisation need further verification.
- Not a production deployment: add durable queue, authenticated/rate-limited public endpoint, SSRF protection, bounded downloads, storage lifecycle, and secrets management before public exposure.

## Local start
Requires Docker Desktop with Compose.
1. `cp .env.example .env`
2. `docker compose up --build`
3. Open `http://localhost:8000/docs`
4. Health: `http://localhost:8000/health`

Without Docker, install FFmpeg and eSpeak NG, then:
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Submit a script job
```bash
curl -X POST http://localhost:8000/api/v1/broll/jobs \
  -H "Content-Type: application/json" -H "Idempotency-Key: demo-001" \
  -d '{"script":"Saving money starts with a budget. Track your spending each week.","aspect_ratios":["16:9","9:16"],"captions":{"burn_in":false,"srt":true}}'
```
Poll the returned `status_url` until status is `succeeded` or `failed`. Result contains links to video(s), captions, timeline and manifest.

Upload audio using `/api/v1/broll/jobs/upload` with multipart field `file` and `Idempotency-Key` header. URL audio must use HTTPS.

## Tests
```bash
pytest -q
```
Batch runner:
```bash
python scripts/batch_runner.py --input-dir test_inputs --output-dir test_results
```

## Configuration
See `.env.example`. `PEXELS_API_KEY` is optional. Do not commit `.env` or secrets.

## Error format
Validation errors from FastAPI currently use FastAPI's default validation envelope; domain errors use `{"error":{"code","message","retryable","details"}}`. Normalize all validation errors before claiming Integration Standard point 5 is complete.

## Architecture
Request → validation → background job → narration/transcription → scene timing → provider adapter/fallback → FFmpeg → video/SRT/timeline/manifest → result links.

## Security
Only expose a demo with authentication or a random access token and rate limiting. Do not accept arbitrary untrusted URLs without SSRF controls in a public deployment. Use only assets whose commercial-use rights you have verified.

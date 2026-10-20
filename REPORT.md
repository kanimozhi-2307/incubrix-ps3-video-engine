# Technical Report — IncuBrix PS3

## Objective
Build an HTTP service that accepts narration audio or text and returns B-roll videos, captions, a per-scene timeline, and a provider/settings manifest.

## Approach
FastAPI provides the HTTP contract. FFmpeg handles transcoding and composition. eSpeak NG provides a local no-key TTS path. faster-whisper is an optional transcription adapter. Pexels is an optional footage adapter; a locally generated motion-graphic fallback allows no-key smoke testing without fetching third-party media.

## Current verified status
Not yet measured in this repository package. Run the tests, create outputs, inspect every output, and fill the table below with actual values.

| Metric | Result | Evidence file |
|---|---|---|
| 60-second cold render | NOT MEASURED | add benchmark log |
| 60-second warm render | NOT MEASURED | add benchmark log |
| Cost per finished minute | NOT MEASURED | provider billing + compute estimate |
| Narration coverage with human relevance >=3 | NOT MEASURED | human scene-rating CSV |
| Mean human relevance (1–5) | NOT MEASURED | human scene-rating CSV |
| Render success rate | NOT MEASURED | test-pack summary |
| Repeat-clip rate | NOT MEASURED | timeline inspection |
| Peak memory / GPU time | NOT MEASURED | system measurement |

## Known limitations
See README. Important incomplete integration items include durable Redis jobs, MinIO output storage, callback signing, burn-in captions, scene swapping, and complete normalised errors. The fallback motion graphics are not equivalent to semantically relevant real-world stock footage.

## Next steps
1. Make job state durable with Redis.
2. Integrate MinIO/S3-compatible object storage.
3. Implement and test callback URL signing with HMAC-SHA256.
4. Add robust transcript alignment and per-sentence timestamps.
5. Add independent vision-language relevance scoring and human agreement analysis.
6. Implement scene-only replacement, burned-in captions, colour matching, and aspect-aware cropping.
7. Run ten varied inputs and publish all outputs and failures in the test pack.

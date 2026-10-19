import os, uuid, json, hashlib, threading, logging, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Literal
from fastapi import FastAPI, HTTPException, Header, UploadFile, File, Form, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from app.pipeline import process_job

ROOT = Path(os.getenv("DATA_DIR", "/data"))
INPUTS = ROOT / "inputs"
OUTPUTS = ROOT / "outputs"
for p in (INPUTS, OUTPUTS): p.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("incubrix.ps3")
app = FastAPI(title="IncuBrix Script and Voiceover to B-roll Engine", version="0.1.0",
              description="Assessment implementation; see README for limitations and configuration.")

JOBS = {}
IDEMPOTENCY = {}
LOCK = threading.Lock()

class Scene(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    visual_hint: Optional[str] = Field(default=None, max_length=500)

class Captions(BaseModel):
    burn_in: bool = True
    srt: bool = True

class JobRequest(BaseModel):
    audio_url: Optional[str] = None
    script: Optional[str] = None
    scenes: Optional[list[Scene]] = None
    voice: str = "en"
    aspect_ratios: list[Literal["16:9", "9:16", "1:1"]] = ["16:9"]
    style: str = "bright, modern, real-world footage"
    avoid: list[str] = []
    captions: Captions = Captions()
    callback_url: Optional[str] = None

    @model_validator(mode="after")
    def input_required(self):
        if not self.audio_url and not self.script and not self.scenes:
            raise ValueError("Provide audio_url, script, or scenes")
        if self.audio_url and (self.script or self.scenes):
            raise ValueError("Use one input mode: audio_url OR script/scenes")
        if not self.aspect_ratios:
            raise ValueError("aspect_ratios cannot be empty")
        return self

def err(code, message, retryable=False, details=None):
    return {"error": {"code": code, "message": message, "retryable": retryable, "details": details or {}}}

@app.middleware("http")
async def correlation(request: Request, call_next):
    cid = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
    request.state.correlation_id = cid
    response = await call_next(request)
    response.headers["X-Correlation-Id"] = cid
    return response

@app.get("/health")
def health():
    return {"status":"ok", "service":"incubrix-ps3"}

@app.get("/ready")
def ready():
    return {"status":"ready", "data_dir_writable": os.access(ROOT, os.W_OK)}

def _worker(job_id, payload, correlation_id):
    with LOCK:
        JOBS[job_id]["status"] = "running"
        JOBS[job_id]["progress"] = 5
    try:
        result = process_job(job_id, payload, INPUTS, OUTPUTS,
            progress_cb=lambda n: _progress(job_id, n))
        with LOCK:
            JOBS[job_id].update(status="succeeded", progress=100, result=result,
                                finished_at=datetime.now(timezone.utc).isoformat())
    except Exception as e:
        log.exception("job_failed job_id=%s correlation_id=%s", job_id, correlation_id)
        with LOCK:
            JOBS[job_id].update(status="failed", progress=100,
                error=err("JOB_FAILED", str(e), retryable=True), finished_at=datetime.now(timezone.utc).isoformat())

def _progress(job_id, value):
    with LOCK:
        if job_id in JOBS: JOBS[job_id]["progress"] = max(0, min(99, int(value)))

@app.post("/api/v1/broll/jobs", status_code=202)
def create_job(body: JobRequest, request: Request, idempotency_key: str = Header(..., alias="Idempotency-Key")):
    if len(idempotency_key) > 200:
        raise HTTPException(400, detail=err("INVALID_IDEMPOTENCY_KEY","Key too long"))
    payload = body.model_dump()
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    with LOCK:
        if idempotency_key in IDEMPOTENCY:
            old_digest, old_id = IDEMPOTENCY[idempotency_key]
            if old_digest != digest:
                raise HTTPException(409, detail=err("IDEMPOTENCY_CONFLICT","Key was used with a different request"))
            return {"job_id":old_id, "status_url":f"/api/v1/jobs/{old_id}", "idempotent_replay":True}
        job_id = str(uuid.uuid4())
        IDEMPOTENCY[idempotency_key] = (digest, job_id)
        JOBS[job_id] = {"job_id":job_id,"status":"queued","progress":0,
            "created_at":datetime.now(timezone.utc).isoformat(),"correlation_id":request.state.correlation_id}
    threading.Thread(target=_worker, args=(job_id,payload,request.state.correlation_id), daemon=True).start()
    log.info(json.dumps({"timestamp":datetime.now(timezone.utc).isoformat(),"level":"INFO",
        "job_id":job_id,"correlation_id":request.state.correlation_id,"event":"job_queued"}))
    return {"job_id":job_id, "status_url":f"/api/v1/jobs/{job_id}", "idempotent_replay":False}

@app.post("/api/v1/broll/jobs/upload", status_code=202)
async def create_upload_job(
    request: Request,
    file: UploadFile = File(...),
    aspect_ratios: str = Form("16:9"),
    voice: str = Form("en"),
    idempotency_key: str = Header(..., alias="Idempotency-Key")
):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in {".mp3",".wav",".m4a"}:
        raise HTTPException(415, detail=err("UNSUPPORTED_MEDIA_TYPE","Accepted audio types: mp3, wav, m4a"))
    content = await file.read()
    if not content:
        raise HTTPException(400, detail=err("EMPTY_AUDIO","Uploaded file is empty"))
    if len(content) > int(os.getenv("MAX_UPLOAD_BYTES", str(100*1024*1024))):
        raise HTTPException(413, detail=err("FILE_TOO_LARGE","Audio exceeds configured size limit"))
    key = hashlib.sha256((idempotency_key + hashlib.sha256(content).hexdigest()).encode()).hexdigest()
    payload = {"_uploaded_audio_b64": __import__("base64").b64encode(content).decode(),
               "_uploaded_ext": ext, "aspect_ratios":[x.strip() for x in aspect_ratios.split(",") if x.strip()],
               "voice":voice,"style":"bright, modern, real-world footage","avoid":[],"captions":{"burn_in":True,"srt":True}}
    # Reuse the same idempotency registry semantics for upload requests.
    digest = hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    with LOCK:
        if idempotency_key in IDEMPOTENCY:
            old_digest, old_id = IDEMPOTENCY[idempotency_key]
            if old_digest != digest: raise HTTPException(409, detail=err("IDEMPOTENCY_CONFLICT","Key was used with a different request"))
            return {"job_id":old_id,"status_url":f"/api/v1/jobs/{old_id}","idempotent_replay":True}
        job_id=str(uuid.uuid4()); IDEMPOTENCY[idempotency_key]=(digest,job_id)
        JOBS[job_id]={"job_id":job_id,"status":"queued","progress":0,"created_at":datetime.now(timezone.utc).isoformat(),"correlation_id":request.state.correlation_id}
    threading.Thread(target=_worker,args=(job_id,payload,request.state.correlation_id),daemon=True).start()
    return {"job_id":job_id,"status_url":f"/api/v1/jobs/{job_id}","idempotent_replay":False}

@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str):
    with LOCK: job = JOBS.get(job_id)
    if not job: raise HTTPException(404, detail=err("JOB_NOT_FOUND","No job with this ID"))
    return job

@app.get("/api/v1/files/{job_id}/{filename}")
def get_file(job_id: str, filename: str):
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(400, detail=err("INVALID_FILENAME","Invalid filename"))
    path = OUTPUTS / job_id / filename
    if not path.is_file(): raise HTTPException(404, detail=err("FILE_NOT_FOUND","File not found"))
    return FileResponse(path)

@app.post("/api/v1/broll/jobs/{job_id}/scenes/{scene_index}/swap", status_code=501)
def swap_scene(job_id: str, scene_index: int):
    raise HTTPException(501, detail=err("SCENE_SWAP_NOT_IMPLEMENTED","Advanced scene swap is not implemented in this build"))

@app.get("/api/v1/jobs")
def list_jobs():
    with LOCK: return {"jobs":list(JOBS.values())[-100:]}

@app.get("/openapi.json")
def openapi_json():
    return app.openapi()

from fastapi.testclient import TestClient
from app.main import app, JOBS, IDEMPOTENCY
client=TestClient(app)

def setup_function():
    JOBS.clear(); IDEMPOTENCY.clear()

def test_health_ready():
    assert client.get("/health").status_code == 200
    assert client.get("/ready").json()["status"] == "ready"

def test_empty_input_rejected():
    r=client.post("/api/v1/broll/jobs",headers={"Idempotency-Key":"empty-1"},json={})
    assert r.status_code == 422

def test_idempotency_conflict():
    headers={"Idempotency-Key":"same-key"}
    a=client.post("/api/v1/broll/jobs",headers=headers,json={"script":"Saving money matters.","aspect_ratios":["16:9"]})
    b=client.post("/api/v1/broll/jobs",headers=headers,json={"script":"A different script.","aspect_ratios":["16:9"]})
    assert a.status_code == 202
    assert b.status_code == 409

def test_job_not_found():
    assert client.get("/api/v1/jobs/not-real").status_code == 404

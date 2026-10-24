import os
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Dict, Any, Optional, List
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from .config import config
from .pipeline import OrchestratorPipeline
from .night_worker import NightVisionWorker
from .holding_watcher import HoldingQueueWatcher
from .db import PipelineDB

pipeline = OrchestratorPipeline()
night_worker = NightVisionWorker()
holding_watcher = HoldingQueueWatcher(pipeline=pipeline)
db = PipelineDB()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start background holding queue watcher task
    watcher_task = asyncio.create_task(holding_watcher.start())
    yield
    holding_watcher.stop()
    watcher_task.cancel()

app = FastAPI(
    title="pdf-orchestrator",
    description="PDF Security Gateway & Processing Pipeline",
    version=config.service_version,
    lifespan=lifespan
)

class ProcessPathRequest(BaseModel):
    file_path: str
    target_override: Optional[str] = None

@app.get("/", response_class=HTMLResponse)
def index_page():
    template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h1>PDF Gateway</h1><p>Template not found</p>")

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": config.service_name,
        "version": config.service_version,
        "shield_url": config.shield_url,
        "sanitizer_url": config.sanitizer_url,
        "ocr_url": config.ocr_url,
        "enricher_url": config.enricher_url,
        "postgres_host": config.postgres_host,
        "paperless_url": config.paperless_url,
        "apprise_url": config.apprise_url
    }

@app.post("/api/v1/process")
async def process_file(file: UploadFile = File(...), force: bool = Form(False)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    file_bytes = await file.read()
    try:
        report = await pipeline.process_document(file_bytes, file.filename, force_reprocess=force)
        return JSONResponse(report)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pipeline error: {str(e)}")

@app.post("/api/v1/documents/{sha256}/vision")
async def trigger_document_vision(sha256: str):
    result = await night_worker.run_single_document(sha256)
    return JSONResponse(result)

@app.post("/api/v1/documents/{sha256}/reprocess")
async def reprocess_document(sha256: str):
    doc = db.check_deduplication(sha256)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    filename = doc["original_filename"]
    file_path = night_worker._find_document_file(filename, sha256)
    if not file_path:
        raise HTTPException(status_code=404, detail="Binary PDF file not found on disk")
    with open(file_path, "rb") as f:
        file_bytes = f.read()
    report = await pipeline.process_document(file_bytes, filename, force_reprocess=True)
    return JSONResponse(report)

@app.post("/api/v1/process-path")
async def process_path(request: ProcessPathRequest):
    if not os.path.exists(request.file_path):
        raise HTTPException(status_code=404, detail=f"File not found: {request.file_path}")

    filename = os.path.basename(request.file_path)
    with open(request.file_path, "rb") as f:
        file_bytes = f.read()

    try:
        report = await pipeline.process_document(file_bytes, filename)
        return JSONResponse(report)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pipeline error: {str(e)}")

@app.post("/api/v1/night-batch")
async def run_night_batch(background_tasks: BackgroundTasks, limit: int = 50):
    result = await night_worker.run_batch(batch_size=limit)
    return JSONResponse(result)

@app.get("/api/v1/stats")
def get_stats():
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status, count(*) as count FROM documents GROUP BY status;")
                status_counts = {r["status"]: r["count"] for r in cur.fetchall()}

                cur.execute("SELECT count(*) as count FROM documents WHERE needs_vision_review = TRUE;")
                pending_vision = cur.fetchone()["count"]

                cur.execute("SELECT count(*) as count FROM documents;")
                total_docs = cur.fetchone()["count"]

                return {
                    "total_documents": total_docs,
                    "by_status": status_counts,
                    "pending_night_vision": pending_vision
                }
    except Exception as e:
        return {"error": f"Database unavailable: {str(e)}"}

@app.get("/api/v1/queue")
def get_queue(limit: int = 50):
    try:
        query = """
        SELECT sha256, original_filename, file_size_bytes, status,
               verified_title, verified_author, domain, subdomain, tags,
               target_system, lab2_dispatch_id, needs_vision_review,
               dictionary_validity_ratio, created_at, updated_at
        FROM documents
        ORDER BY updated_at DESC
        LIMIT %s;
        """
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (limit,))
                results = []
                for r in cur.fetchall():
                    row = dict(r)
                    if row.get("created_at"):
                        row["created_at"] = row["created_at"].isoformat()
                    if row.get("updated_at"):
                        row["updated_at"] = row["updated_at"].isoformat()
                    results.append(row)
                return results
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

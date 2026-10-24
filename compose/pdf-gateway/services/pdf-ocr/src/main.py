import os
import asyncio
import tempfile
import shutil
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse

from .config import config
from .engine import OcrCoordinator

app = FastAPI(
    title="pdf-ocr",
    description="Microservice for lossless OCR injection, vector detection, and garbage/quality gating",
    version=config.service_version
)

coordinator = OcrCoordinator()

def cleanup_dir(dir_path: str):
    if os.path.exists(dir_path):
        shutil.rmtree(dir_path, ignore_errors=True)

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": config.service_name,
        "version": config.service_version
    }

@app.post("/api/v1/ocr")
async def process_ocr(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """
    Process PDF:
    - If native vector, passes through instantly.
    - If scanned image, runs lossless OCR without image recompression.
    - Assesses dictionary validity and flags 'X-Needs-Vision-Escalation' if degraded.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    tmp_dir = tempfile.mkdtemp(prefix="pdf_ocr_")
    input_path = os.path.join(tmp_dir, f"in_{file.filename}")
    output_path = os.path.join(tmp_dir, f"ocr_{file.filename}")

    try:
        with open(input_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        report = await asyncio.to_thread(coordinator.process, input_path, output_path)

        background_tasks.add_task(cleanup_dir, tmp_dir)

        q = report["quality_metrics"]
        headers = {
            "X-Status": report["status"],
            "X-Is-Native-Vector": str(report["is_native_vector"]).lower(),
            "X-OCR-Applied": str(report["ocr_applied"]).lower(),
            "X-Dict-Validity-Ratio": str(q["dictionary_validity_ratio"]),
            "X-Noise-Ratio": str(q["noise_ratio"]),
            "X-Needs-Vision-Escalation": str(report["needs_vision_escalation"]).lower(),
            "X-Extracted-Chars": str(report["extracted_chars_count"]),
            "Content-Disposition": f'attachment; filename="ocr_{file.filename}"'
        }

        return FileResponse(
            path=output_path,
            media_type="application/pdf",
            headers=headers
        )

    except Exception as e:
        cleanup_dir(tmp_dir)
        raise HTTPException(status_code=500, detail=f"OCR processing failed: {str(e)}")

import os
import tempfile
import shutil
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse

from .config import config
from .scanner import SecurityEngine

app = FastAPI(
    title="pdf-shield",
    description="Microservice for PDF malware detection and exploit heuristics",
    version=config.service_version
)

engine = SecurityEngine()

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": config.service_name,
        "version": config.service_version
    }

@app.post("/api/v1/scan")
async def scan_pdf(file: UploadFile = File(...)):
    """
    Scan an uploaded PDF file for malware and heuristic exploit tags.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted for security scanning.")

    # Save uploaded file to temporary path safely
    tmp_dir = tempfile.mkdtemp(prefix="pdf_shield_")
    tmp_path = os.path.join(tmp_dir, file.filename)
    
    try:
        with open(tmp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        report = engine.evaluate(tmp_path, original_filename=file.filename)
        return JSONResponse(content=report, status_code=200)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal scan failure: {str(e)}")
        
    finally:
        # Guarantee cleanup of uploaded temp bytes
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)

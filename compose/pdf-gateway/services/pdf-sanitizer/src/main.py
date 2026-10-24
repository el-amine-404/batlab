import os
import tempfile
import shutil
import json
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse

from .config import config
from .scrubber import SanitizerEngine, ExifToolScrubber

app = FastAPI(
    title="pdf-sanitizer",
    description="Microservice for OPSEC metadata stripping, fingerprint purging, and catalog preservation",
    version=config.service_version
)

engine = SanitizerEngine()
scrubber = ExifToolScrubber()

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

@app.post("/api/v1/inspect-metadata")
async def inspect_metadata(file: UploadFile = File(...)):
    """
    Inspect metadata in the PDF: separates safe catalog fields from toxic leaks.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    tmp_dir = tempfile.mkdtemp(prefix="pdf_inspect_")
    tmp_path = os.path.join(tmp_dir, file.filename)

    try:
        with open(tmp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        raw_meta = scrubber.inspect(tmp_path)
        catalog = scrubber.extract_catalog_metadata(raw_meta)
        toxic = scrubber.find_toxic_fingerprints(raw_meta)

        return JSONResponse({
            "filename": file.filename,
            "preserved_catalog_metadata": catalog,
            "toxic_fingerprints_detected": toxic,
            "raw_metadata": raw_meta
        })

    finally:
        cleanup_dir(tmp_dir)

@app.post("/api/v1/sanitize")
async def sanitize_pdf(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """
    Purge all hardware/GPS/user fingerprints while preserving legitimate catalog data.
    Returns the sanitized PDF binary file with verification headers.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    tmp_dir = tempfile.mkdtemp(prefix="pdf_sanitize_")
    input_path = os.path.join(tmp_dir, f"raw_{file.filename}")
    output_path = os.path.join(tmp_dir, f"clean_{file.filename}")

    try:
        with open(input_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        report = engine.process(input_path, output_path)

        background_tasks.add_task(cleanup_dir, tmp_dir)

        catalog = report["preserved_catalog_metadata"]
        headers = {
            "X-Status": report["status"],
            "X-Original-SHA256": report["original_sha256"],
            "X-Clean-SHA256": report["clean_sha256"],
            "X-Toxic-Purged-Count": str(len(report["toxic_fingerprints_purged"])),
            "X-Preserved-Title": catalog.get("title", ""),
            "X-Preserved-Author": catalog.get("author", ""),
            "Content-Disposition": f'attachment; filename="clean_{file.filename}"'
        }

        return FileResponse(
            path=output_path,
            media_type="application/pdf",
            headers=headers
        )

    except Exception as e:
        cleanup_dir(tmp_dir)
        raise HTTPException(status_code=500, detail=f"Sanitization failed: {str(e)}")

@app.post("/api/v1/inject-metadata")
async def inject_metadata_endpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(None),
    author: str = Form(None),
    subject: str = Form(None),
    keywords: str = Form(None),
    created_date: str = Form(None),
    original_sha256: str = Form(None)
):
    """
    Inject vetted canonical metadata into the clean PDF.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    tmp_dir = tempfile.mkdtemp(prefix="pdf_inject_")
    out_path = os.path.join(tmp_dir, f"enriched_{file.filename}")

    try:
        with open(out_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        kw_list = []
        if keywords:
            try:
                kw_list = json.loads(keywords)
            except Exception:
                kw_list = [k.strip() for k in keywords.split(",") if k.strip()]

        scrubber.inject_metadata(
            file_path=out_path,
            title=title,
            author=author,
            subject=subject,
            keywords=kw_list,
            created_date=created_date,
            original_sha256=original_sha256
        )

        background_tasks.add_task(cleanup_dir, tmp_dir)
        return FileResponse(
            path=out_path,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="enriched_{file.filename}"'}
        )
    except Exception as e:
        cleanup_dir(tmp_dir)
        raise HTTPException(status_code=500, detail=f"Metadata injection failed: {str(e)}")


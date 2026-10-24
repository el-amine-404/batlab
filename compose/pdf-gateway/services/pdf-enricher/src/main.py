import os
import io
import base64
import hashlib
import tempfile
import shutil
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
import fitz # PyMuPDF

from .config import config
from .isbn_fetcher import extract_isbns, IsbnFetcher
from .verifier import CandidateMetadataVerifier
from .ai_client import AIEnricherClient

app = FastAPI(
    title="pdf-enricher",
    description="Microservice for dynamic 2-tier taxonomy classification, candidate metadata verification, ISBN enrichment, and vision fallback",
    version=config.service_version
)

isbn_fetcher = IsbnFetcher()
verifier = CandidateMetadataVerifier()
ai_client = AIEnricherClient()

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": config.service_name,
        "version": config.service_version,
        "lm_studio_endpoint": config.lm_studio_url
    }

@app.post("/api/v1/enrich")
async def enrich_text(payload: Dict[str, Any]):
    """
    Enrich metadata from extracted text and candidate parameters.
    """
    sha256 = payload.get("sha256", "")
    filename = payload.get("filename", "unknown.pdf")
    text_sample = payload.get("text_sample", "")
    candidate_meta = payload.get("candidate_metadata", {})

    # 1. Verify candidate metadata
    cand_title = candidate_meta.get("title", "")
    cand_author = candidate_meta.get("author", "")
    t_verdict, clean_t = verifier.evaluate_title(cand_title, text_sample)
    a_verdict, clean_a = verifier.evaluate_author(cand_author, text_sample)

    verified_candidate = {
        "title": clean_t if t_verdict != "REJECTED" else "",
        "author": clean_a if a_verdict != "REJECTED" else "",
        "title_verdict": t_verdict,
        "author_verdict": a_verdict
    }

    # 2. Check for ISBN in text
    detected_isbns = extract_isbns(text_sample)
    isbn_info = None
    detected_isbn = None
    if detected_isbns:
        detected_isbn = detected_isbns[0]
        isbn_info = await isbn_fetcher.lookup(detected_isbn)

    # 3. AI Classification & dynamic taxonomy
    ai_result = await ai_client.classify_text(
        filename=filename,
        text_sample=text_sample,
        candidate_metadata=verified_candidate,
        isbn_info=isbn_info
    )

    return JSONResponse({
        "sha256": sha256,
        "verified_title": ai_result.get("verified_title", filename),
        "verified_author": ai_result.get("verified_author", "Unknown"),
        "document_date": ai_result.get("document_date"),
        "document_type": ai_result.get("document_type", "Document"),
        "correspondent": ai_result.get("correspondent"),
        "domain": ai_result.get("domain", "GENERAL"),
        "subdomain": ai_result.get("subdomain", "UNSORTED"),
        "subject": ai_result.get("subject", ""),
        "tags": ai_result.get("tags", []),
        "markdown_summary": ai_result.get("markdown_summary", ""),
        "target_system": ai_result.get("target_system", "paperless"),
        "isbn": detected_isbn,
        "isbn_metadata": isbn_info,
        "metadata_payload": {
            "ai_confidence": ai_result.get("confidence_score", 0.8),
            "candidate_title_verdict": t_verdict,
            "candidate_author_verdict": a_verdict,
            "isbns_found": detected_isbns
        },
        "vision_used": False
    })

@app.post("/api/v1/enrich-pdf")
async def enrich_pdf(
    file: UploadFile = File(...),
    candidate_title: Optional[str] = Form(None),
    candidate_author: Optional[str] = Form(None),
    force_vision: bool = Form(False)
):
    """
    Accepts full PDF, extracts text sample and/or renders pages for vision,
    then returns full verified taxonomy and enrichment.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    tmp_dir = tempfile.mkdtemp(prefix="pdf_enrich_")
    tmp_path = os.path.join(tmp_dir, file.filename)

    try:
        content = await file.read()
        sha256 = hashlib.sha256(content).hexdigest()

        with open(tmp_path, "wb") as f:
            f.write(content)

        doc = fitz.open(tmp_path)
        extracted_text = ""
        base64_images: List[str] = []

        # Extract text from first 5 pages
        for page_idx in range(min(5, len(doc))):
            page = doc[page_idx]
            extracted_text += page.get_text() + "\n"
            
            if force_vision and page_idx < 2:
                # Render page image at 150 DPI for vision model
                pix = page.get_pixmap(dpi=150)
                img_bytes = pix.tobytes("jpeg")
                base64_images.append(base64.b64encode(img_bytes).decode("utf-8"))

        doc.close()

        candidate_meta = {
            "title": candidate_title or "",
            "author": candidate_author or ""
        }

        # If vision forced
        if force_vision and base64_images:
            ai_result = await ai_client.classify_vision(
                filename=file.filename,
                base64_images=base64_images,
                candidate_metadata=candidate_meta
            )
            return JSONResponse({
                "sha256": sha256,
                "verified_title": ai_result.get("verified_title", file.filename),
                "verified_author": ai_result.get("verified_author", "Unknown"),
                "domain": ai_result.get("domain", "GENERAL"),
                "subdomain": ai_result.get("subdomain", "UNSORTED"),
                "tags": ai_result.get("tags", []),
                "target_system": ai_result.get("target_system", "paperless"),
                "isbn": None,
                "isbn_metadata": None,
                "metadata_payload": {
                    "vision_processed": True,
                    "confidence_score": ai_result.get("confidence_score", 0.9)
                },
                "vision_used": True
            })

        # Standard text enrichment
        t_verdict, clean_t = verifier.evaluate_title(candidate_title or "", extracted_text)
        a_verdict, clean_a = verifier.evaluate_author(candidate_author or "", extracted_text)
        verified_cand = {
            "title": clean_t if t_verdict != "REJECTED" else "",
            "author": clean_a if a_verdict != "REJECTED" else "",
        }

        detected_isbns = extract_isbns(extracted_text)
        isbn_info = None
        detected_isbn = None
        if detected_isbns:
            detected_isbn = detected_isbns[0]
            isbn_info = await isbn_fetcher.lookup(detected_isbn)

        ai_result = await ai_client.classify_text(
            filename=file.filename,
            text_sample=extracted_text,
            candidate_metadata=verified_cand,
            isbn_info=isbn_info
        )

        return JSONResponse({
            "sha256": sha256,
            "verified_title": ai_result.get("verified_title", file.filename),
            "verified_author": ai_result.get("verified_author", "Unknown"),
            "document_date": ai_result.get("document_date"),
            "document_type": ai_result.get("document_type", "Document"),
            "correspondent": ai_result.get("correspondent"),
            "domain": ai_result.get("domain", "GENERAL"),
            "subdomain": ai_result.get("subdomain", "UNSORTED"),
            "subject": ai_result.get("subject", ""),
            "tags": ai_result.get("tags", []),
            "markdown_summary": ai_result.get("markdown_summary", ""),
            "target_system": ai_result.get("target_system", "paperless"),
            "isbn": detected_isbn,
            "isbn_metadata": isbn_info,
            "metadata_payload": {
                "ai_confidence": ai_result.get("confidence_score", 0.8),
                "candidate_title_verdict": t_verdict,
                "candidate_author_verdict": a_verdict,
                "isbns_found": detected_isbns
            },
            "vision_used": False
        })

    finally:
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)

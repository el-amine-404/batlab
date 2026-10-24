import logging
import json
from typing import Dict, Any, Tuple, Optional, List
import httpx
from .config import config

logger = logging.getLogger("pdf-orchestrator.clients")

class MicroserviceClients:
    def __init__(self):
        self.shield_url = config.shield_url
        self.sanitizer_url = config.sanitizer_url
        self.ocr_url = config.ocr_url
        self.enricher_url = config.enricher_url

    async def scan_security(self, file_bytes: bytes, filename: str) -> Dict[str, Any]:
        """
        Calls pdf-shield (Port 8001) for ClamAV + PDFiD scanning.
        """
        files = {"file": (filename, file_bytes, "application/pdf")}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{self.shield_url}/api/v1/scan", files=files)
            resp.raise_for_status()
            return resp.json()

    async def sanitize_metadata(self, file_bytes: bytes, filename: str) -> Tuple[bytes, Dict[str, str]]:
        """
        Calls pdf-sanitizer (Port 8002) for OPSEC fingerprint purging & catalog preservation.
        Returns (clean_pdf_bytes, response_headers).
        """
        files = {"file": (filename, file_bytes, "application/pdf")}
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(f"{self.sanitizer_url}/api/v1/sanitize", files=files)
            resp.raise_for_status()
            headers = dict(resp.headers)
            return resp.content, headers

    async def process_ocr(self, file_bytes: bytes, filename: str) -> Tuple[bytes, Dict[str, str]]:
        """
        Calls pdf-ocr (Port 8003) for trilingual lossless OCR injection and quality gating.
        Returns (ocr_pdf_bytes, response_headers).
        """
        files = {"file": (filename, file_bytes, "application/pdf")}
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(f"{self.ocr_url}/api/v1/ocr", files=files)
            resp.raise_for_status()
            headers = dict(resp.headers)
            return resp.content, headers

    async def enrich_metadata(
        self,
        file_bytes: bytes,
        filename: str,
        candidate_title: Optional[str] = None,
        candidate_author: Optional[str] = None,
        force_vision: bool = False
    ) -> Dict[str, Any]:
        """
        Calls pdf-enricher (Port 8004) for dynamic taxonomy, ISBN resolution, and AI cataloging.
        """
        files = {"file": (filename, file_bytes, "application/pdf")}
        data = {
            "candidate_title": candidate_title or "",
            "candidate_author": candidate_author or "",
            "force_vision": str(force_vision).lower()
        }
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(f"{self.enricher_url}/api/v1/enrich-pdf", files=files, data=data)
            resp.raise_for_status()
            return resp.json()

    async def inject_verified_metadata(
        self,
        file_bytes: bytes,
        filename: str,
        title: Optional[str] = None,
        author: Optional[str] = None,
        subject: Optional[str] = None,
        keywords: Optional[List[str]] = None,
        created_date: Optional[str] = None,
        original_sha256: Optional[str] = None
    ) -> bytes:
        """
        Calls pdf-sanitizer (Port 8002) to inject authoritative catalog metadata into the clean PDF.
        """
        files = {"file": (filename, file_bytes, "application/pdf")}
        data = {
            "title": title or "",
            "author": author or "",
            "subject": subject or "",
            "keywords": json.dumps(keywords) if keywords else "",
            "created_date": created_date or "",
            "original_sha256": original_sha256 or ""
        }
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.post(f"{self.sanitizer_url}/api/v1/inject-metadata", files=files, data=data)
                if resp.status_code == 200:
                    return resp.content
                logger.warning(f"Metadata injection failed ({resp.status_code}): {resp.text}")
        except Exception as e:
            logger.warning(f"Error calling inject-metadata: {e}")
        return file_bytes

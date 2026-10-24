import os
import hashlib
import logging
from typing import Dict, Any, Optional

from .config import config
from .db import PipelineDB
from .clients import MicroserviceClients
from .dispatcher import DocumentDispatcher
from .notifier import notifier

logger = logging.getLogger("pdf-orchestrator.pipeline")

class OrchestratorPipeline:
    def __init__(self, db: Optional[PipelineDB] = None, clients: Optional[MicroserviceClients] = None, dispatcher: Optional[DocumentDispatcher] = None):
        self.db = db or PipelineDB()
        self.clients = clients or MicroserviceClients()
        self.dispatcher = dispatcher or DocumentDispatcher()

    async def process_document(self, file_bytes: bytes, filename: str, force_reprocess: bool = False) -> Dict[str, Any]:
        """
        Executes end-to-end processing pipeline for a PDF document.
        """
        file_size = len(file_bytes)
        sha256 = hashlib.sha256(file_bytes).hexdigest()

        # Step 1: O(1) Instant Deduplication Check
        try:
            existing = self.db.check_deduplication(sha256)
            if not force_reprocess and existing and existing.get("status") in ["ENRICHED", "DISPATCHED"]:
                logger.info(f"Duplicate document skipped (sha256={sha256}): {filename}")
                return {
                    "sha256": sha256,
                    "filename": filename,
                    "status": "DEDUPLICATED",
                    "verified_title": existing.get("verified_title") or filename,
                    "verified_author": existing.get("verified_author") or "Unknown",
                    "domain": existing.get("domain") or "GENERAL",
                    "subdomain": existing.get("subdomain") or "UNSORTED",
                    "tags": existing.get("tags") or [],
                    "target_system": existing.get("target_system") or "paperless",
                    "dispatch_id": existing.get("lab2_dispatch_id"),
                    "message": "Document already processed and dispatched to library"
                }

            # Register document in DB
            self.db.register_or_update(sha256, filename, file_size, status="IN_PROGRESS")
        except Exception as e:
            logger.warning(f"Database unavailable for deduplication check ({e}), continuing pipeline")

        # Step 2: pdf-shield (Malware & Exploit Scanning)
        shield_report = await self.clients.scan_security(file_bytes, filename)
        verdict = shield_report.get("verdict", "CLEAN")
        if verdict in ["INFECTED", "SUSPICIOUS"]:
            threats = ", ".join(shield_report.get("threats", []))
            quarantine_path = self.dispatcher.quarantine(file_bytes, filename, threats)
            try:
                self.db.register_or_update(sha256, filename, file_size, status="QUARANTINED")
            except Exception:
                pass
            # Trigger Apprise alert
            await notifier.notify_quarantine(filename, shield_report.get("threats", []), sha256)
            return {
                "sha256": sha256,
                "filename": filename,
                "status": "QUARANTINED",
                "verdict": verdict,
                "threats": shield_report.get("threats"),
                "quarantine_path": quarantine_path
            }

        # Step 3: pdf-sanitizer (OPSEC Fingerprint Scrubbing & Catalog Preservation)
        clean_bytes, sanitizer_headers = await self.clients.sanitize_metadata(file_bytes, filename)
        cand_title = sanitizer_headers.get("x-preserved-title", "")
        cand_author = sanitizer_headers.get("x-preserved-author", "")

        # Step 4: pdf-ocr (Trilingual Lossless OCR & Quality Gating)
        ocr_bytes, ocr_headers = await self.clients.process_ocr(clean_bytes, filename)
        
        is_vector = ocr_headers.get("x-is-native-vector", "false").lower() == "true"
        ocr_applied = ocr_headers.get("x-ocr-applied", "false").lower() == "true"
        dict_ratio = float(ocr_headers.get("x-dict-validity-ratio", "0.0"))
        noise_ratio = float(ocr_headers.get("x-noise-ratio", "0.0"))
        needs_vision = ocr_headers.get("x-needs-vision-escalation", "false").lower() == "true"

        try:
            self.db.record_ocr_metrics(
                sha256=sha256,
                is_native_vector=is_vector,
                ocr_applied=ocr_applied,
                detected_languages="eng+fra+ara",
                dict_ratio=dict_ratio,
                noise_ratio=noise_ratio,
                needs_vision_review=needs_vision
            )
        except Exception as e:
            logger.warning(f"Failed to record OCR metrics in DB: {e}")

        # Step 5: pdf-enricher (2-Tier Dynamic Taxonomy, ISBN & AI Cataloging)
        enrichment = await self.clients.enrich_metadata(
            file_bytes=ocr_bytes,
            filename=filename,
            candidate_title=cand_title,
            candidate_author=cand_author,
            force_vision=False
        )

        title = enrichment.get("verified_title", filename)
        author = enrichment.get("verified_author", "Unknown")
        domain = enrichment.get("domain", "GENERAL")
        subdomain = enrichment.get("subdomain", "UNSORTED")
        tags = enrichment.get("tags", [])
        target_system = enrichment.get("target_system", "paperless")

        try:
            self.db.record_ai_metadata(
                sha256=sha256,
                verified_title=title,
                verified_author=author,
                domain=domain,
                subdomain=subdomain,
                tags=tags,
                target_system=target_system,
                metadata_payload=enrichment
            )
        except Exception as e:
            logger.warning(f"Failed to record AI metadata in DB: {e}")

        # Step 6: Inject authoritative, clean metadata into final PDF binary
        clean_subject = enrichment.get("subject") or f"{enrichment.get('document_type', 'Document')} - {title}"
        final_pdf_bytes = await self.clients.inject_verified_metadata(
            file_bytes=ocr_bytes,
            filename=filename,
            title=title,
            author=author,
            subject=clean_subject,
            keywords=tags,
            created_date=enrichment.get("document_date"),
            original_sha256=sha256
        )

        # Step 7: Dispatching to Target System (Paperless / Calibre / Archive)
        dispatch_id = await self.dispatcher.route_document(
            file_bytes=final_pdf_bytes,
            filename=filename,
            target_system=target_system,
            title=title,
            domain=domain,
            subdomain=subdomain,
            tags=tags,
            metadata=enrichment
        )

        try:
            self.db.mark_dispatched(sha256, target_system, dispatch_id)
        except Exception as e:
            logger.warning(f"Failed to record dispatch in DB: {e}")

        # Trigger Apprise ingest notification
        await notifier.notify_ingest(filename, title, domain, subdomain, tags, dispatch_id)

        return {
            "sha256": sha256,
            "filename": filename,
            "status": "COMPLETED",
            "verified_title": title,
            "verified_author": author,
            "domain": domain,
            "subdomain": subdomain,
            "tags": tags,
            "target_system": target_system,
            "dispatch_id": dispatch_id,
            "needs_vision_review": needs_vision,
            "ocr_metrics": {
                "is_native_vector": is_vector,
                "ocr_applied": ocr_applied,
                "dictionary_validity_ratio": dict_ratio,
                "noise_ratio": noise_ratio
            }
        }

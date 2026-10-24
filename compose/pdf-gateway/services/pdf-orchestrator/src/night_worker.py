import os
import logging
from typing import Dict, Any, List, Optional
from .config import config
from .db import PipelineDB
from .clients import MicroserviceClients
from .notifier import notifier

logger = logging.getLogger("pdf-orchestrator.night_worker")

class NightVisionWorker:
    def __init__(self, db: PipelineDB = None, clients: MicroserviceClients = None):
        self.db = db or PipelineDB()
        self.clients = clients or MicroserviceClients()

    def _find_document_file(self, filename: str, sha256: str) -> Optional[str]:
        """
        Recursively locates document binary in archive/library or staging directories.
        """
        if os.path.exists(config.archive_dir):
            for root, _, files in os.walk(config.archive_dir):
                for f in files:
                    if f.endswith(".pdf"):
                        # Check exact filename or clean match
                        if filename.lower() in f.lower() or f.lower() in filename.lower():
                            return os.path.join(root, f)

        for d in [config.holding_dir, config.quarantine_dir]:
            if os.path.exists(d):
                p = os.path.join(d, filename)
                if os.path.exists(p):
                    return p
        return None

    async def run_single_document(self, sha256: str) -> Dict[str, Any]:
        """
        Forces visual inspection or AI reprocessing for a specific document on demand.
        """
        doc = self.db.check_deduplication(sha256)
        if not doc:
            return {"status": "NOT_FOUND", "message": f"Document {sha256} not found in database"}

        filename = doc["original_filename"]
        file_path = self._find_document_file(filename, sha256)
        if not file_path:
            return {"status": "FILE_NOT_FOUND", "message": f"Could not locate PDF file on disk for {filename}"}

        try:
            with open(file_path, "rb") as f:
                file_bytes = f.read()

            vision_enrichment = await self.clients.enrich_metadata(
                file_bytes=file_bytes,
                filename=filename,
                force_vision=True
            )

            title = vision_enrichment.get("verified_title", filename)
            author = vision_enrichment.get("verified_author", "Unknown")
            domain = vision_enrichment.get("domain", doc.get("domain", "GENERAL"))
            subdomain = vision_enrichment.get("subdomain", doc.get("subdomain", "UNSORTED"))
            tags = vision_enrichment.get("tags", [])
            target_sys = vision_enrichment.get("target_system", "paperless")

            self.db.record_ai_metadata(
                sha256=sha256,
                verified_title=title,
                verified_author=author,
                domain=domain,
                subdomain=subdomain,
                tags=tags,
                target_system=target_sys,
                metadata_payload=vision_enrichment
            )

            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE documents SET needs_vision_review = FALSE, updated_at = CURRENT_TIMESTAMP WHERE sha256 = %s",
                        (sha256,)
                    )
                    conn.commit()

            return {
                "status": "SUCCESS",
                "sha256": sha256,
                "filename": filename,
                "verified_title": title,
                "verified_author": author,
                "domain": domain,
                "subdomain": subdomain,
                "tags": tags
            }
        except Exception as e:
            logger.error(f"Single-document vision processing failed for {filename}: {e}")
            return {"status": "ERROR", "message": str(e)}

    async def run_batch(self, batch_size: int = 50) -> Dict[str, Any]:
        """
        Processes pending night vision queue using Qwen2.5-VL-3B-Instruct.
        """
        queue = self.db.get_night_vision_queue(limit=batch_size)
        if not queue:
            logger.info("No documents currently pending night vision review.")
            return {"processed_count": 0, "results": []}

        logger.info(f"Night Vision Worker: Found {len(queue)} documents for visual inspection.")
        results = []

        for doc in queue:
            sha256 = doc["sha256"]
            filename = doc["original_filename"]
            logger.info(f"Night Vision Worker: Inspecting {filename} ({sha256[:8]})...")

            file_path = self._find_document_file(filename, sha256)
            if not file_path:
                logger.warning(f"Could not find local file for vision escalation: {filename}")
                continue

            try:
                with open(file_path, "rb") as f:
                    file_bytes = f.read()

                # Call pdf-enricher with vision model (qwen2.5-vl-3b)
                vision_enrichment = await self.clients.enrich_metadata(
                    file_bytes=file_bytes,
                    filename=filename,
                    force_vision=True
                )

                # Update database record
                title = vision_enrichment.get("verified_title", filename)
                author = vision_enrichment.get("verified_author", "Unknown")
                domain = vision_enrichment.get("domain", doc.get("domain", "GENERAL"))
                subdomain = vision_enrichment.get("subdomain", doc.get("subdomain", "UNSORTED"))
                tags = vision_enrichment.get("tags", [])
                target_sys = vision_enrichment.get("target_system", "paperless")

                self.db.record_ai_metadata(
                    sha256=sha256,
                    verified_title=title,
                    verified_author=author,
                    domain=domain,
                    subdomain=subdomain,
                    tags=tags,
                    target_system=target_sys,
                    metadata_payload=vision_enrichment
                )

                # Clear vision escalation flag in DB
                with self.db.get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE documents SET needs_vision_review = FALSE, updated_at = CURRENT_TIMESTAMP WHERE sha256 = %s",
                            (sha256,)
                        )
                        conn.commit()

                results.append({
                    "sha256": sha256,
                    "filename": filename,
                    "upgraded_title": title,
                    "upgraded_tags": tags,
                    "status": "UPGRADED_VIA_VISION"
                })

            except Exception as e:
                logger.error(f"Night vision processing failed for {filename}: {e}")

        if results:
            await notifier.notify_night_vision(len(results), results)

        return {
            "processed_count": len(results),
            "results": results
        }

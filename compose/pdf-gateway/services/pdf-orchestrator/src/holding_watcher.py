import os
import asyncio
import logging
import shutil
from typing import List
from .config import config
from .pipeline import OrchestratorPipeline
from .notifier import notifier

logger = logging.getLogger("pdf-orchestrator.watcher")

class HoldingQueueWatcher:
    def __init__(self, pipeline: OrchestratorPipeline = None):
        self.pipeline = pipeline or OrchestratorPipeline()
        self.holding_dir = config.holding_dir
        self.processing_dir = os.path.join(self.holding_dir, ".processing")
        self.quarantine_dir = os.path.join(self.holding_dir, ".quarantine")
        self._running = False

    def _ensure_dirs(self):
        for d in [self.holding_dir, self.processing_dir, self.quarantine_dir]:
            os.makedirs(d, exist_ok=True)

    async def scan_once(self):
        if not os.path.exists(self.holding_dir):
            return

        self._ensure_dirs()
        try:
            files = [
                f for f in os.listdir(self.holding_dir)
                if f.lower().endswith(".pdf") and not f.startswith(".")
            ]
        except Exception as e:
            logger.warning(f"Could not read holding directory {self.holding_dir}: {e}")
            return

        if not files:
            return

        logger.info(f"Holding watcher: Found {len(files)} document(s) in {self.holding_dir}: {files}")

        for filename in sorted(files):
            src_path = os.path.join(self.holding_dir, filename)
            proc_path = os.path.join(self.processing_dir, filename)

            try:
                # Move to .processing
                shutil.move(src_path, proc_path)
            except Exception as e:
                logger.error(f"Failed to move {filename} to .processing: {e}")
                continue

            try:
                with open(proc_path, "rb") as f:
                    file_bytes = f.read()

                report = await self.pipeline.process_document(file_bytes, filename)
                status = report.get("status")

                if status in ["COMPLETED", "DEDUPLICATED"]:
                    logger.info(f"Holding watcher: Ingested {filename} -> {report.get('dispatch_id')}")
                    if os.path.exists(proc_path):
                        os.remove(proc_path)
                elif status == "QUARANTINED":
                    logger.warning(f"Holding watcher: Quarantined {filename}")
                    dest = os.path.join(self.quarantine_dir, filename)
                    shutil.move(proc_path, dest)
                else:
                    logger.warning(f"Holding watcher: Unhandled status {status} for {filename}, moving to quarantine")
                    dest = os.path.join(self.quarantine_dir, filename)
                    if os.path.exists(proc_path):
                        shutil.move(proc_path, dest)
            except Exception as e:
                logger.error(f"Holding watcher error on {filename}: {e}")
                # Circuit breaker: Move directly to .quarantine to prevent infinite retry storms!
                if os.path.exists(proc_path):
                    dest = os.path.join(self.quarantine_dir, filename)
                    try:
                        shutil.move(proc_path, dest)
                        logger.warning(f"Circuit breaker: Moved failed document {filename} to quarantine: {dest}")
                    except Exception as me:
                        logger.error(f"Failed to quarantine failed document {filename}: {me}")
                await notifier.notify_error(filename, f"Pipeline error ({e}). Quarantined to prevent retry storm.")

    async def start(self):
        self._running = True
        logger.info(f"Holding Queue Watcher started on {self.holding_dir} (interval: 10s)")
        while self._running:
            try:
                await self.scan_once()
            except Exception as e:
                logger.error(f"Unexpected error in holding watcher: {e}")
            await asyncio.sleep(10.0)

    def stop(self):
        self._running = False

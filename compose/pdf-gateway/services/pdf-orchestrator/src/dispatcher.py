import os
import re
import shutil
import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
import httpx
from .config import config

logger = logging.getLogger("pdf-orchestrator.dispatcher")

class DocumentDispatcher:
    def __init__(self):
        self.paperless_url = config.paperless_url.rstrip("/")
        self.paperless_token = config.paperless_token
        self.paperless_host_header = config.paperless_host_header
        self.calibre_import_dir = config.calibre_import_dir
        self.archive_dir = config.archive_dir
        self.quarantine_dir = config.quarantine_dir
        self.library_dir = os.path.join(self.archive_dir, "library")
        self._tag_cache: Dict[str, int] = {}
        self._doc_type_cache: Dict[str, int] = {}
        self._correspondent_cache: Dict[str, int] = {}
        self._storage_path_cache: Dict[str, int] = {}

        for d in [self.calibre_import_dir, self.archive_dir, self.quarantine_dir, self.library_dir]:
            os.makedirs(d, exist_ok=True)

    def quarantine(self, file_bytes: bytes, filename: str, reason: str) -> str:
        """
        Quarantine infected or malicious PDF safely.
        """
        dest_path = os.path.join(self.quarantine_dir, f"quarantine_{filename}")
        with open(dest_path, "wb") as f:
            f.write(file_bytes)
        
        # Write threat metadata file
        meta_path = f"{dest_path}.threat.txt"
        with open(meta_path, "w") as f:
            f.write(f"Threat Reason: {reason}\nOriginal File: {filename}\n")

        logger.warning(f"File quarantined to {dest_path}: {reason}")
        return dest_path

    async def _get_or_create_tag_ids(self, client: httpx.AsyncClient, tags: List[str], headers: Dict[str, str]) -> List[int]:
        """
        Maps tag strings to Paperless tag integer IDs, creating new tags if necessary.
        """
        if not tags:
            return []

        # Refresh cache if empty
        if not self._tag_cache:
            try:
                resp = await client.get(f"{self.paperless_url}/api/tags/", headers=headers, timeout=5.0)
                if resp.status_code == 200:
                    data = resp.json()
                    for t in data.get("results", []):
                        self._tag_cache[t["name"].lower()] = t["id"]
            except Exception as e:
                logger.warning(f"Could not fetch Paperless tags: {e}")

        tag_ids = []
        for tag_name in tags:
            clean = tag_name.strip().lower()
            if not clean:
                continue
            if clean in self._tag_cache:
                tag_ids.append(self._tag_cache[clean])
            else:
                # Create tag in Paperless
                try:
                    create_resp = await client.post(
                        f"{self.paperless_url}/api/tags/",
                        headers=headers,
                        json={"name": clean},
                        timeout=5.0
                    )
                    if create_resp.status_code in [200, 201]:
                        new_id = create_resp.json()["id"]
                        self._tag_cache[clean] = new_id
                        tag_ids.append(new_id)
                except Exception as e:
                    logger.warning(f"Could not create tag '{clean}' in Paperless: {e}")

        return tag_ids

    async def _get_or_create_document_type_id(self, client: httpx.AsyncClient, doc_type: str, headers: Dict[str, str]) -> Optional[int]:
        if not doc_type:
            return None
        clean = doc_type.strip()
        if not self._doc_type_cache:
            try:
                resp = await client.get(f"{self.paperless_url}/api/document_types/", headers=headers, timeout=5.0)
                if resp.status_code == 200:
                    for dt in resp.json().get("results", []):
                        self._doc_type_cache[dt["name"].lower()] = dt["id"]
            except Exception as e:
                logger.warning(f"Could not fetch Paperless document types: {e}")

        if clean.lower() in self._doc_type_cache:
            return self._doc_type_cache[clean.lower()]

        try:
            create_resp = await client.post(
                f"{self.paperless_url}/api/document_types/",
                headers=headers,
                json={"name": clean},
                timeout=5.0
            )
            if create_resp.status_code in [200, 201]:
                new_id = create_resp.json()["id"]
                self._doc_type_cache[clean.lower()] = new_id
                return new_id
        except Exception as e:
            logger.warning(f"Could not create document type '{clean}': {e}")
        return None

    async def _get_or_create_correspondent_id(self, client: httpx.AsyncClient, correspondent: str, headers: Dict[str, str]) -> Optional[int]:
        if not correspondent:
            return None
        clean = correspondent.strip()
        if not self._correspondent_cache:
            try:
                resp = await client.get(f"{self.paperless_url}/api/correspondents/", headers=headers, timeout=5.0)
                if resp.status_code == 200:
                    for c in resp.json().get("results", []):
                        self._correspondent_cache[c["name"].lower()] = c["id"]
            except Exception as e:
                logger.warning(f"Could not fetch Paperless correspondents: {e}")

        if clean.lower() in self._correspondent_cache:
            return self._correspondent_cache[clean.lower()]

        try:
            create_resp = await client.post(
                f"{self.paperless_url}/api/correspondents/",
                headers=headers,
                json={"name": clean},
                timeout=5.0
            )
            if create_resp.status_code in [200, 201]:
                new_id = create_resp.json()["id"]
                self._correspondent_cache[clean.lower()] = new_id
                return new_id
        except Exception as e:
            logger.warning(f"Could not create correspondent '{clean}': {e}")
        return None

    async def _get_or_create_storage_path_id(self, client: httpx.AsyncClient, path_name: str, path_pattern: str, headers: Dict[str, str]) -> Optional[int]:
        if not path_name:
            return None
        clean = path_name.strip()
        if not self._storage_path_cache:
            try:
                resp = await client.get(f"{self.paperless_url}/api/storage_paths/", headers=headers, timeout=5.0)
                if resp.status_code == 200:
                    for sp in resp.json().get("results", []):
                        self._storage_path_cache[sp["name"].lower()] = sp["id"]
            except Exception as e:
                logger.warning(f"Could not fetch Paperless storage paths: {e}")

        if clean.lower() in self._storage_path_cache:
            return self._storage_path_cache[clean.lower()]

        try:
            create_resp = await client.post(
                f"{self.paperless_url}/api/storage_paths/",
                headers=headers,
                json={"name": clean, "path": path_pattern, "matching_algorithm": 0},
                timeout=5.0
            )
            if create_resp.status_code in [200, 201]:
                new_id = create_resp.json()["id"]
                self._storage_path_cache[clean.lower()] = new_id
                return new_id
        except Exception as e:
            logger.warning(f"Could not create storage path '{clean}': {e}")
        return None

    async def _add_document_note(self, client: httpx.AsyncClient, doc_id: int, note_markdown: str, headers: Dict[str, str]):
        if not note_markdown:
            return
        try:
            await client.post(
                f"{self.paperless_url}/api/documents/{doc_id}/notes/",
                headers=headers,
                json={"note": note_markdown},
                timeout=5.0
            )
            logger.info(f"Added Markdown summary note to Paperless document #{doc_id}")
        except Exception as e:
            logger.warning(f"Failed to add note to document #{doc_id}: {e}")

    def save_to_library(self, file_bytes: bytes, clean_filename: str, domain: str, created_date: Optional[str] = None) -> str:
        """
        Saves a 1-level shallow human-browsable copy in /data/archive/library/[Domain]/
        with filesystem timestamps set to the document's true historical date.
        """
        domain_dir = os.path.join(self.library_dir, (domain or "General").capitalize())
        os.makedirs(domain_dir, exist_ok=True)
        dest_path = os.path.join(domain_dir, clean_filename)
        with open(dest_path, "wb") as f:
            f.write(file_bytes)
        
        if created_date:
            try:
                dt = datetime.strptime(created_date[:10], "%Y-%m-%d")
                ts = dt.timestamp()
                os.utime(dest_path, (ts, ts))
            except Exception:
                pass
        logger.info(f"Saved human-browsable document to {dest_path}")
        return dest_path

    async def _resolve_paperless_task(self, client: httpx.AsyncClient, task_id: str, headers: Dict[str, str]) -> Optional[int]:
        """
        Polls Paperless task queue to obtain the final document ID.
        """
        clean_task_id = task_id.strip('"')
        url = f"{self.paperless_url}/api/tasks/?task_id={clean_task_id}"

        for _ in range(15): # poll up to 15s
            await asyncio.sleep(1.0)
            try:
                resp = await client.get(url, headers=headers, timeout=5.0)
                if resp.status_code == 200:
                    data = resp.json()
                    results = data.get("results", [])
                    if results:
                        task_info = results[0]
                        status = task_info.get("status")
                        if status == "success":
                            rel_ids = task_info.get("related_document_ids", [])
                            if rel_ids:
                                return int(rel_ids[0])
                            doc_id = task_info.get("result_data", {}).get("document_id")
                            if doc_id:
                                return int(doc_id)
                        elif status == "failure":
                            logger.error(f"Paperless consumption failed: {task_info.get('result_data')}")
                            return None
            except Exception as e:
                logger.warning(f"Error polling Paperless task status: {e}")

        return None

    async def dispatch_paperless(
        self,
        file_bytes: bytes,
        filename: str,
        title: str,
        tags: List[str],
        created_date: Optional[str] = None,
        document_type: Optional[str] = None,
        correspondent: Optional[str] = None,
        markdown_summary: Optional[str] = None,
        domain: str = "General"
    ) -> str:
        """
        Dispatch document to Paperless-ngx REST API with full metadata:
        title, created date, document type, correspondent, tags, and markdown summary note.
        Also maintains the standalone human-browsable library.
        """
        # Generate sanitized clean filename: [YYYY-MM-DD - ]Title.pdf (safely truncated)
        safe_title = re.sub(r'[/\\:*?"<>|]', '_', title).strip()[:80]
        if created_date:
            clean_filename = f"{created_date[:10]} - {safe_title}.pdf"
        else:
            clean_filename = f"{safe_title}.pdf"

        # 1. Maintain standalone browsable library on Lab2
        self.save_to_library(file_bytes, clean_filename, domain, created_date)

        headers = {}
        if self.paperless_token:
            headers["Authorization"] = f"Token {self.paperless_token}"
        if self.paperless_host_header:
            headers["Host"] = self.paperless_host_header

        # Filter out personal student/user names as correspondent unless it's a vendor or institution
        valid_correspondent = correspondent
        if correspondent:
            corr_lower = correspondent.lower()
            if any(term in corr_lower for term in ["amine", "el houjjaji", "student", "author"]):
                valid_correspondent = None

        # Ensure document_type tag is present in tags list
        clean_tags = list(tags) if tags else []
        if document_type and document_type.lower() not in [t.lower() for t in clean_tags]:
            clean_tags.append(document_type.lower())

        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                tag_ids = await self._get_or_create_tag_ids(client, clean_tags, headers)
                doc_type_id = await self._get_or_create_document_type_id(client, document_type, headers)
                corr_id = await self._get_or_create_correspondent_id(client, valid_correspondent, headers)
                storage_path_id = await self._get_or_create_storage_path_id(
                    client,
                    "Library",
                    "{{ document_type }}/{{ created_year }}/{{ created }} - {{ title }}",
                    headers
                )

                data_fields: Dict[str, Any] = {"title": title[:120]}
                if tag_ids:
                    data_fields["tags"] = tag_ids
                if doc_type_id:
                    data_fields["document_type"] = doc_type_id
                if corr_id:
                    data_fields["correspondent"] = corr_id
                if storage_path_id:
                    data_fields["storage_path"] = storage_path_id
                if created_date:
                    data_fields["created"] = created_date[:10]

                files = {"document": (clean_filename, file_bytes, "application/pdf")}

                resp = await client.post(
                    f"{self.paperless_url}/api/documents/post_document/",
                    headers=headers,
                    files=files,
                    data=data_fields
                )

                if resp.status_code in [200, 202]:
                    raw_task = resp.text.strip()
                    clean_task = raw_task.strip('"')
                    logger.info(f"Paperless accepted document. Task ID: {clean_task}")

                    # Attempt to resolve document ID
                    doc_id = await self._resolve_paperless_task(client, clean_task, headers)
                    if doc_id:
                        deep_link = f"http://{self.paperless_host_header}/documents/{doc_id}/details"
                        logger.info(f"Document registered in Paperless: #{doc_id} ({deep_link})")
                        
                        # Add Markdown summary note
                        if markdown_summary:
                            await self._add_document_note(client, doc_id, markdown_summary, headers)

                        return f"paperless_doc:{doc_id}:{deep_link}"

                    return f"paperless_task:{clean_task}"
                else:
                    logger.warning(f"Paperless rejected upload ({resp.status_code}): {resp.text}")
        except Exception as e:
            logger.warning(f"Paperless API not directly reachable ({e}), writing to drop-in folder")

        # Staging fallback: write to paperless consume directory
        consume_dir = os.path.join(self.archive_dir, "paperless_consume")
        os.makedirs(consume_dir, exist_ok=True)
        dest_path = os.path.join(consume_dir, clean_filename)
        with open(dest_path, "wb") as f:
            f.write(file_bytes)
        return f"staged_consume:{dest_path}"

    def dispatch_calibre(self, file_bytes: bytes, filename: str, metadata: Dict[str, Any]) -> str:
        dest_path = os.path.join(self.calibre_import_dir, filename)
        with open(dest_path, "wb") as f:
            f.write(file_bytes)
        logger.info(f"Dispatched to Calibre import: {dest_path}")
        return f"calibre_import:{dest_path}"

    def dispatch_archive(self, file_bytes: bytes, filename: str, domain: str, subdomain: str) -> str:
        dest_dir = os.path.join(self.archive_dir, domain, subdomain)
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, filename)
        with open(dest_path, "wb") as f:
            f.write(file_bytes)
        logger.info(f"Dispatched to Archive: {dest_path}")
        return f"archive:{dest_path}"

    async def route_document(
        self,
        file_bytes: bytes,
        filename: str,
        target_system: str,
        title: str,
        domain: str,
        subdomain: str,
        tags: List[str],
        metadata: Dict[str, Any]
    ) -> str:
        target = target_system.lower()
        if target == "paperless":
            return await self.dispatch_paperless(
                file_bytes=file_bytes,
                filename=filename,
                title=title,
                tags=tags,
                created_date=metadata.get("document_date"),
                document_type=metadata.get("document_type"),
                correspondent=metadata.get("correspondent"),
                markdown_summary=metadata.get("markdown_summary"),
                domain=domain
            )
        elif target == "calibre":
            return self.dispatch_calibre(file_bytes, filename, metadata)
        else:
            return self.dispatch_archive(file_bytes, filename, domain, subdomain)

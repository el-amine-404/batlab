import os
import json
import shutil
import hashlib
import subprocess
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Tuple
from datetime import datetime

from .config import config

class IMetadataScrubber(ABC):
    @abstractmethod
    def inspect(self, file_path: str) -> Dict[str, Any]:
        """Inspect all metadata tags currently inside the PDF."""
        pass

    @abstractmethod
    def sanitize(self, input_path: str, output_path: str) -> Tuple[bool, Dict[str, Any], List[str]]:
        """
        Selectively purge personal/hardware fingerprints while preserving
        legitimate catalog metadata (Title, Author, Subject, Year, ISBN).
        """
        pass

class ExifToolScrubber(IMetadataScrubber):
    """
    Smart OPSEC Scrubber:
    1. Extracts and preserves valuable catalog metadata (Title, Author, Subject, Date, ISBN).
    2. Identifies and strips toxic fingerprints (GPS, Scanner/Printer models, OS user, local paths).
    3. Re-injects only vetted, clean catalog metadata into the output PDF.
    """
    def __init__(self, exiftool_cmd: str = None):
        self.cmd = exiftool_cmd or config.exiftool_path

    def inspect(self, file_path: str) -> Dict[str, Any]:
        try:
            res = subprocess.run(
                [self.cmd, "-j", "-G1", file_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10
            )
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                if isinstance(data, list) and len(data) > 0:
                    return data[0]
            return {}
        except Exception as e:
            return {"error": str(e)}

    def extract_catalog_metadata(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract legitimate catalog fields that should not be lost.
        """
        catalog = {}
        for key, val in metadata.items():
            k_lower = key.lower()
            if any(k_lower.endswith(field) for field in [":title", "title"]):
                catalog["title"] = str(val).strip()
            elif any(k_lower.endswith(field) for field in [":author", "author"]):
                # Filter out personal OS usernames (e.g. 'bro', 'root', 'user')
                val_str = str(val).strip()
                if val_str.lower() not in ["bro", "root", "user", "admin"]:
                    catalog["author"] = val_str
            elif any(k_lower.endswith(field) for field in [":subject", "subject"]):
                catalog["subject"] = str(val).strip()
            elif any(k_lower.endswith(field) for field in [":keywords", "keywords"]):
                catalog["keywords"] = str(val).strip()
            elif any(k_lower.endswith(field) for field in [":creationdate", "creationdate"]):
                catalog["creation_date"] = str(val).strip()
        return catalog

    def find_toxic_fingerprints(self, metadata: Dict[str, Any]) -> List[str]:
        """
        Detect hardware, GPS, serials, and machine identifiers.
        """
        toxic = []
        for key, val in metadata.items():
            k_lower = key.lower()
            val_lower = str(val).lower()
            if any(pat in k_lower for pat in ["gps", "serial", "mac", "hostname", "scanner", "printer"]):
                toxic.append(f"{key}: {val}")
            elif any(hw in val_lower for hw in ["canon", "hp laserjet", "epson", "xerox", "ricoh", "mac os x", "windows"]):
                toxic.append(f"{key}: {val}")
        return toxic

    def sanitize(self, input_path: str, output_path: str) -> Tuple[bool, Dict[str, Any], List[str]]:
        # 1. Inspect original metadata
        raw_meta = self.inspect(input_path)
        preserved_catalog = self.extract_catalog_metadata(raw_meta)
        toxic_detected = self.find_toxic_fingerprints(raw_meta)

        # 2. Copy file to output destination
        shutil.copy2(input_path, output_path)

        # 3. Complete wipe of all tags (guarantees zero hidden streams / MakerNotes remain)
        wipe_cmd = [self.cmd, "-all:all=", "-overwrite_original", output_path]
        res = subprocess.run(wipe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
        if res.returncode != 0:
            return False, {}, [f"ExifTool wipe failed: {res.stderr.strip()}"]

        # 4. Re-inject ONLY vetted, safe catalog metadata (no hardware/GPS junk)
        if preserved_catalog:
            inject_args = [self.cmd, "-overwrite_original"]
            if "title" in preserved_catalog:
                inject_args.append(f"-Title={preserved_catalog['title']}")
            if "author" in preserved_catalog:
                inject_args.append(f"-Author={preserved_catalog['author']}")
            if "subject" in preserved_catalog:
                inject_args.append(f"-Subject={preserved_catalog['subject']}")
            if "keywords" in preserved_catalog:
                inject_args.append(f"-Keywords={preserved_catalog['keywords']}")
            inject_args.append(output_path)
            
            subprocess.run(inject_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)

        return True, preserved_catalog, toxic_detected

    def inject_metadata(
        self,
        file_path: str,
        title: str = None,
        author: str = None,
        subject: str = None,
        keywords: List[str] = None,
        created_date: str = None,
        original_sha256: str = None
    ) -> bool:
        cmd = [self.cmd, "-overwrite_original"]
        if title:
            cmd.append(f"-Title={title}")
        if author:
            cmd.append(f"-Author={author}")
        if subject:
            clean_sub = subject.replace(" / ", " — ").strip()
            if clean_sub.isupper():
                clean_sub = clean_sub.title()
            cmd.append(f"-Subject={clean_sub}")
        if keywords:
            kw = ", ".join(keywords) if isinstance(keywords, list) else str(keywords)
            cmd.append(f"-Keywords={kw}")
        if created_date:
            c_date = created_date.replace("-", ":")
            if len(c_date) == 10:
                c_date = f"{c_date} 00:00:00"
            cmd.append(f"-CreateDate={c_date}")
            cmd.append(f"-DateTimeOriginal={c_date}")
            cmd.append(f"-ModifyDate={c_date}")
        if original_sha256:
            cmd.append(f"-UserComment=Provenance Original SHA256: {original_sha256}")

        cmd.append(file_path)
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
            return res.returncode == 0
        except Exception:
            return False

class SanitizerEngine:
    def __init__(self, scrubber: IMetadataScrubber = None):
        self.scrubber = scrubber or ExifToolScrubber()

    def calculate_sha256(self, file_path: str) -> str:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()

    def process(self, input_path: str, output_path: str) -> Dict[str, Any]:
        original_hash = self.calculate_sha256(input_path)
        original_size = os.path.getsize(input_path)

        success, preserved_catalog, toxic_detected = self.scrubber.sanitize(input_path, output_path)

        if not success:
            raise RuntimeError("Sanitization execution failed.")

        clean_hash = self.calculate_sha256(output_path)
        clean_size = os.path.getsize(output_path)

        return {
            "status": "SANITIZED",
            "original_sha256": original_hash,
            "clean_sha256": clean_hash,
            "original_size_bytes": original_size,
            "clean_size_bytes": clean_size,
            "preserved_catalog_metadata": preserved_catalog,
            "toxic_fingerprints_purged": toxic_detected,
            "processed_at": datetime.utcnow().isoformat()
        }

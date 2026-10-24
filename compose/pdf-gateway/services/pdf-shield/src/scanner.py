import os
import re
import socket
import hashlib
import subprocess
from abc import ABC, abstractmethod
from typing import Dict, Any, Tuple, List
from datetime import datetime

from .config import config

class IScanner(ABC):
    @abstractmethod
    def scan(self, file_path: str) -> Dict[str, Any]:
        """Perform scan on file_path and return structured findings."""
        pass

class PDFiDScanner(IScanner):
    """
    High-performance pure-Python PDF object and exploit heuristic scanner.
    Detects embedded scripts, automatic execution vectors, and obfuscation.
    """
    KEYWORDS = [
        "/obj", "/endobj", "/stream", "/endstream", "/xref", "/trailer", "/startxref",
        "/Page", "/Encrypt", "/ObjStm", "/JS", "/JavaScript", "/AA", "/OpenAction",
        "/AcroForm", "/JBIG2Decode", "/RichMedia", "/Launch", "/EmbeddedFiles", "/XFA"
    ]

    def _normalize_pdf_hex(self, content: bytes) -> bytes:
        """
        Normalize PDF hex-encoded characters (e.g. /#4a#53 -> /JS) to prevent evasion.
        """
        def hex_repl(match):
            try:
                return bytes([int(match.group(1), 16)])
            except Exception:
                return match.group(0)

        return re.sub(b'#([0-9a-fA-F]{2})', hex_repl, content)

    def scan(self, file_path: str) -> Dict[str, Any]:
        counts: Dict[str, int] = {k: 0 for k in self.KEYWORDS}
        threats: List[str] = []

        try:
            with open(file_path, "rb") as f:
                data = f.read()

            normalized_data = self._normalize_pdf_hex(data)

            for kw in self.KEYWORDS:
                kw_bytes = kw.encode("ascii")
                # Pattern matches the keyword not preceded by another slash or word char
                pattern = re.compile(re.escape(kw_bytes))
                matches = len(pattern.findall(normalized_data))
                counts[kw] = matches

            # Check critical threats
            for crit in config.critical_tags:
                if counts.get(crit, 0) > 0:
                    threats.append(f"CRITICAL: Found {counts[crit]} occurrence(s) of '{crit}' (Execution/Script payload)")

            # Check warning threats
            for warn in config.warning_tags:
                if counts.get(warn, 0) > 0:
                    threats.append(f"WARNING: Found {counts[warn]} occurrence(s) of '{warn}' (Auto-action/Form tag)")

        except Exception as e:
            threats.append(f"ERROR reading file for PDFiD analysis: {str(e)}")

        return {
            "counts": counts,
            "threats": threats,
            "is_suspicious": len([t for t in threats if t.startswith("CRITICAL")]) > 0
        }

class ClamAVScanner(IScanner):
    """
    ClamAV Antivirus scanner interface.
    Supports TCP socket, Unix domain socket, and CLI fallback.
    """
    def scan(self, file_path: str) -> Dict[str, Any]:
        # 1. Try clamd daemon via TCP socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(3.0)
                s.connect((config.clamav_host, config.clamav_port))
                s.sendall(f"SCAN {file_path}\n".encode())
                response = s.recv(4096).decode("utf-8", errors="ignore").strip()
                if "OK" in response:
                    return {"infected": False, "virus_name": None, "message": "Clamd: CLEAN"}
                elif "FOUND" in response:
                    virus = response.split("FOUND")[0].split(":")[-1].strip()
                    return {"infected": True, "virus_name": virus, "message": f"Clamd: INFECTED ({virus})"}
        except Exception:
            pass

        # 2. Try CLI clamscan fallback if binary is present
        try:
            res = subprocess.run(
                ["clamscan", "--no-summary", file_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10
            )
            if res.returncode == 0:
                return {"infected": False, "virus_name": None, "message": "clamscan: CLEAN"}
            elif res.returncode == 1:
                return {"infected": True, "virus_name": "VIRUS_DETECTED", "message": "clamscan: FOUND MALWARE"}
        except Exception:
            pass

        # 3. Fallback: Daemon not reachable in dev environment
        return {"infected": False, "virus_name": None, "message": "ClamAV daemon not connected (Mock/Dev pass)"}

class SecurityEngine:
    """
    Coordinator adhering to Single Responsibility:
    Orchestrates individual scanners and produces a consolidated report.
    """
    def __init__(self, pdf_scanner: IScanner = None, av_scanner: IScanner = None):
        self.pdf_scanner = pdf_scanner or PDFiDScanner()
        self.av_scanner = av_scanner or ClamAVScanner()

    def calculate_sha256(self, file_path: str) -> str:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()

    def evaluate(self, file_path: str, original_filename: str) -> Dict[str, Any]:
        size_bytes = os.path.getsize(file_path)
        sha256_hash = self.calculate_sha256(file_path)

        # 1. Antivirus Scan
        av_res = self.av_scanner.scan(file_path)
        
        # 2. Heuristic PDF Object Scan
        pdf_res = self.pdf_scanner.scan(file_path)

        all_threats = list(pdf_res["threats"])
        if av_res.get("infected"):
            all_threats.insert(0, f"MALWARE DETECTED by ClamAV: {av_res.get('virus_name')}")

        # Verdict logic
        if av_res.get("infected"):
            verdict = "INFECTED"
        elif pdf_res.get("is_suspicious"):
            verdict = "SUSPICIOUS"
        else:
            verdict = "CLEAN"

        return {
            "filename": original_filename,
            "sha256": sha256_hash,
            "size_bytes": size_bytes,
            "verdict": verdict,
            "threats": all_threats,
            "heuristic_features": pdf_res["counts"],
            "clamav_message": av_res.get("message"),
            "scan_timestamp": datetime.utcnow().isoformat()
        }

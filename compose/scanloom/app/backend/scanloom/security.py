from __future__ import annotations

import hashlib
import socket
import struct
from pathlib import Path


class Refused(ValueError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_container(path: Path) -> tuple[str, dict]:
    """Recognize a small allowlist and reject bytes appended after its end."""
    size = path.stat().st_size
    with path.open("rb") as source:
        head = source.read(16)
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            source.seek(8)
            chunks = 0
            while True:
                raw = source.read(8)
                if len(raw) != 8:
                    raise Refused("PNG ended before its IEND chunk")
                length, kind = struct.unpack(">I4s", raw)
                if length > 64 * 1024 * 1024:
                    raise Refused("PNG contains an oversized chunk")
                if len(source.read(length + 4)) != length + 4:
                    raise Refused("PNG contains a truncated chunk")
                chunks += 1
                if kind == b"IEND":
                    if length != 0 or source.read(1):
                        raise Refused("PNG has data after its image end")
                    return "image/png", {"container": "png", "chunks": chunks, "trailing_bytes": 0}
                if chunks > 10000:
                    raise Refused("PNG contains too many chunks")
        if head[:2] == b"\xff\xd8":
            # JPEG metadata and entropy streams make a complete parser needless
            # here; the decoder verifies structure. Enforce an exact final EOI so
            # ZIP/PDF/script payloads cannot be hidden after a valid photograph.
            with path.open("rb") as whole:
                data = whole.read()
            if len(data) < 4 or not data.endswith(b"\xff\xd9"):
                raise Refused("JPEG is incomplete or has data after its image end")
            return "image/jpeg", {"container": "jpeg", "trailing_bytes": 0}
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            declared = struct.unpack("<I", head[4:8])[0] + 8
            if declared != size:
                raise Refused("WebP size does not match its container")
            return "image/webp", {"container": "webp", "trailing_bytes": 0}
        if len(head) >= 12 and head[4:8] == b"ftyp":
            # HEIC/HEIF use ISO-BMFF. Walk every top-level box to ensure the
            # declared container ends exactly at EOF; libvips then performs the
            # full decode in its restricted worker and rewrites a metadata-free
            # PNG before OpenCV sees any pixels.
            source.seek(0)
            brands: set[bytes] = set()
            boxes = 0
            offset = 0
            while offset < size:
                source.seek(offset)
                header = source.read(8)
                if len(header) != 8:
                    raise Refused("HEIC contains a truncated box header")
                length, kind = struct.unpack(">I4s", header)
                header_size = 8
                if length == 1:
                    extended = source.read(8)
                    if len(extended) != 8:
                        raise Refused("HEIC contains a truncated extended box")
                    length = struct.unpack(">Q", extended)[0]
                    header_size = 16
                elif length == 0:
                    length = size - offset
                if length < header_size or offset + length > size:
                    raise Refused("HEIC box size is outside the file")
                if kind == b"ftyp":
                    payload = source.read(length - header_size)
                    if len(payload) < 8 or (len(payload) - 8) % 4:
                        raise Refused("HEIC has an invalid file-type box")
                    brands.update(payload[index:index + 4] for index in range(0, len(payload), 4) if index != 4)
                offset += length
                boxes += 1
                if boxes > 100000:
                    raise Refused("HEIC contains too many boxes")
            heif_brands = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1"}
            if not brands.intersection(heif_brands):
                raise Refused("ISO media file is not a supported HEIC or HEIF image")
            return "image/heic", {"container": "heif", "boxes": boxes, "trailing_bytes": 0}
    raise Refused("Only genuine JPEG, PNG, WebP, HEIC, and HEIF images are accepted")


def clamav(path: Path, host: str, port: int, maximum: int) -> str:
    """Scan by ClamAV INSTREAM; never give the daemon a host path."""
    if path.stat().st_size > maximum:
        raise Refused("Image exceeds the configured upload limit")
    with socket.create_connection((host, port), timeout=15) as stream, path.open("rb") as source:
        stream.settimeout(60)
        stream.sendall(b"zINSTREAM\0")
        for chunk in iter(lambda: source.read(256 * 1024), b""):
            stream.sendall(struct.pack(">I", len(chunk)))
            stream.sendall(chunk)
        stream.sendall(b"\0\0\0\0")
        answer = bytearray()
        while not answer.endswith(b"\0") and len(answer) < 4096:
            part = stream.recv(4096)
            if not part:
                break
            answer.extend(part)
    result = answer.rstrip(b"\0").decode("utf-8", "replace")
    if result.endswith(" OK"):
        return result
    if result.endswith(" FOUND"):
        raise Refused("Malware scanner blocked this image")
    raise RuntimeError(f"Malware scanner could not complete: {result or 'no response'}")

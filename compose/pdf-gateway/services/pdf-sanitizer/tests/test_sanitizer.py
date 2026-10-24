import os
import tempfile
import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.scrubber import ExifToolScrubber, SanitizerEngine

def generate_valid_pdf_with_metadata(title="Test Title", author="Martin Kleppmann", creator="Canon Scanner 4545", producer="Windows 10 Distiller"):
    objs = []
    objs.append(b"%PDF-1.4\n")
    offsets = []

    # Object 1: Catalog
    offsets.append(sum(len(x) for x in objs))
    objs.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")

    # Object 2: Pages
    offsets.append(sum(len(x) for x in objs))
    objs.append(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")

    # Object 3: Page
    offsets.append(sum(len(x) for x in objs))
    objs.append(b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n")

    # Object 4: Info dictionary (Mixed Catalog & Hardware Metadata)
    offsets.append(sum(len(x) for x in objs))
    info_dict = f"4 0 obj\n<< /Title ({title}) /Author ({author}) /Creator ({creator}) /Producer ({producer}) >>\nendobj\n".encode("latin1")
    objs.append(info_dict)

    # Cross-Reference Table
    xref_offset = sum(len(x) for x in objs)
    objs.append(b"xref\n0 5\n0000000000 65535 f \n")
    for off in offsets:
        objs.append(f"{off:010d} 00000 n \n".encode("latin1"))

    # Trailer
    objs.append(f"trailer\n<< /Size 5 /Root 1 0 R /Info 4 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("latin1"))
    return b"".join(objs)

client = TestClient(app)

def test_health_check():
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert data["service"] == "pdf-sanitizer"

def test_metadata_inspection_separates_catalog_from_toxic():
    pdf_bytes = generate_valid_pdf_with_metadata(
        title="Designing Data-Intensive Applications",
        author="Martin Kleppmann",
        creator="Canon imageRUNNER 4545",
        producer="Mac OS X Quartz"
    )

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        f.flush()
        temp_path = f.name

    try:
        scrubber = ExifToolScrubber()
        meta = scrubber.inspect(temp_path)
        catalog = scrubber.extract_catalog_metadata(meta)
        toxic = scrubber.find_toxic_fingerprints(meta)

        # Catalog is preserved
        assert catalog.get("title") == "Designing Data-Intensive Applications"
        assert catalog.get("author") == "Martin Kleppmann"

        # Hardware leaks are detected
        toxic_str = " ".join(toxic).lower()
        assert "canon" in toxic_str or "mac os x" in toxic_str
    finally:
        os.remove(temp_path)

def test_smart_sanitization_preserves_catalog_and_purges_hardware():
    pdf_bytes = generate_valid_pdf_with_metadata(
        title="Designing Data-Intensive Applications",
        author="Martin Kleppmann",
        creator="Canon imageRUNNER 4545",
        producer="Windows 10 Distiller"
    )

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as in_f:
        in_f.write(pdf_bytes)
        in_f.flush()
        in_path = in_f.name

    out_path = in_path + "_clean.pdf"

    try:
        engine = SanitizerEngine()
        result = engine.process(in_path, out_path)

        assert result["status"] == "SANITIZED"
        assert result["preserved_catalog_metadata"]["title"] == "Designing Data-Intensive Applications"
        assert result["preserved_catalog_metadata"]["author"] == "Martin Kleppmann"
        assert len(result["toxic_fingerprints_purged"]) > 0

        # Inspect post-clean output file
        scrubber = ExifToolScrubber()
        post_meta = scrubber.inspect(out_path)
        post_catalog = scrubber.extract_catalog_metadata(post_meta)
        post_toxic = scrubber.find_toxic_fingerprints(post_meta)

        # Clean file retains book Title & Author!
        assert post_catalog.get("title") == "Designing Data-Intensive Applications"
        assert post_catalog.get("author") == "Martin Kleppmann"

        # Clean file has ZERO hardware / scanner leaks!
        assert len(post_toxic) == 0
    finally:
        if os.path.exists(in_path):
            os.remove(in_path)
        if os.path.exists(out_path):
            os.remove(out_path)

def test_api_inspect_and_sanitize_endpoints():
    pdf_bytes = generate_valid_pdf_with_metadata(
        title="Quarterly Statement",
        author="Bank Organization",
        creator="HP LaserJet Scanner",
        producer="Acrobat 9.0"
    )

    # 1. Inspect endpoint
    inspect_res = client.post(
        "/api/v1/inspect-metadata",
        files={"file": ("stmt.pdf", pdf_bytes, "application/pdf")}
    )
    assert inspect_res.status_code == 200
    inspect_data = inspect_res.json()
    assert inspect_data["preserved_catalog_metadata"]["title"] == "Quarterly Statement"

    # 2. Sanitize endpoint
    sanitize_res = client.post(
        "/api/v1/sanitize",
        files={"file": ("stmt.pdf", pdf_bytes, "application/pdf")}
    )
    assert sanitize_res.status_code == 200
    assert sanitize_res.headers["x-status"] == "SANITIZED"
    assert sanitize_res.headers["x-preserved-title"] == "Quarterly Statement"
    assert sanitize_res.headers["x-preserved-author"] == "Bank Organization"
    assert int(sanitize_res.headers["x-toxic-purged-count"]) > 0

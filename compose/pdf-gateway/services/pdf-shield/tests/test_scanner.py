import os
import tempfile
import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.scanner import PDFiDScanner, SecurityEngine, IScanner

MINIMAL_CLEAN_PDF = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R >>
endobj
xref
0 4
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
trailer
<< /Size 4 /Root 1 0 R >>
startxref
163
%%EOF"""

MALICIOUS_LAUNCH_PDF = MINIMAL_CLEAN_PDF.replace(
    b"/Type /Page",
    b"/Type /Page /Launch << /F (cmd.exe) /P (/c echo owned) >>"
)

MALICIOUS_JS_PDF = MINIMAL_CLEAN_PDF.replace(
    b"/Type /Page",
    b"/Type /Page /JS (app.alert('malicious script executed'))"
)

OBFUSCATED_HEX_JS_PDF = MINIMAL_CLEAN_PDF.replace(
    b"/Type /Page",
    b"/Type /Page /#4a#53 (app.alert('obfuscated script'))"
)

client = TestClient(app)

class MockInfectedAV(IScanner):
    def scan(self, file_path: str):
        return {"infected": True, "virus_name": "Win.Trojan.Dropper-1234", "message": "ClamAV: INFECTED"}

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "pdf-shield"

def test_clean_pdf_scan():
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(MINIMAL_CLEAN_PDF)
        f.flush()
        temp_path = f.name

    try:
        scanner = PDFiDScanner()
        res = scanner.scan(temp_path)
        assert res["is_suspicious"] is False
        assert len([t for t in res["threats"] if t.startswith("CRITICAL")]) == 0

        engine = SecurityEngine(pdf_scanner=scanner)
        report = engine.evaluate(temp_path, "clean_test.pdf")
        assert report["verdict"] == "CLEAN"
        assert len(report["sha256"]) == 64
    finally:
        os.remove(temp_path)

def test_malicious_launch_detected():
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(MALICIOUS_LAUNCH_PDF)
        f.flush()
        temp_path = f.name

    try:
        scanner = PDFiDScanner()
        res = scanner.scan(temp_path)
        assert res["is_suspicious"] is True
        assert any("/Launch" in t for t in res["threats"])

        engine = SecurityEngine(pdf_scanner=scanner)
        report = engine.evaluate(temp_path, "evil_launch.pdf")
        assert report["verdict"] == "SUSPICIOUS"
    finally:
        os.remove(temp_path)

def test_malicious_javascript_detected():
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(MALICIOUS_JS_PDF)
        f.flush()
        temp_path = f.name

    try:
        scanner = PDFiDScanner()
        res = scanner.scan(temp_path)
        assert res["is_suspicious"] is True
        assert any("/JS" in t for t in res["threats"])

        engine = SecurityEngine(pdf_scanner=scanner)
        report = engine.evaluate(temp_path, "evil_js.pdf")
        assert report["verdict"] == "SUSPICIOUS"
    finally:
        os.remove(temp_path)

def test_obfuscated_hex_javascript_detected():
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(OBFUSCATED_HEX_JS_PDF)
        f.flush()
        temp_path = f.name

    try:
        scanner = PDFiDScanner()
        res = scanner.scan(temp_path)
        # Should normalize /#4a#53 to /JS and detect threat
        assert res["is_suspicious"] is True
        assert any("/JS" in t for t in res["threats"])
    finally:
        os.remove(temp_path)

def test_mock_clamav_infection():
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(MINIMAL_CLEAN_PDF)
        f.flush()
        temp_path = f.name

    try:
        engine = SecurityEngine(av_scanner=MockInfectedAV())
        report = engine.evaluate(temp_path, "virus.pdf")
        assert report["verdict"] == "INFECTED"
        assert any("Win.Trojan.Dropper-1234" in t for t in report["threats"])
    finally:
        os.remove(temp_path)

def test_api_endpoint_upload():
    response = client.post(
        "/api/v1/scan",
        files={"file": ("test_doc.pdf", MINIMAL_CLEAN_PDF, "application/pdf")}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == "test_doc.pdf"
    assert data["verdict"] == "CLEAN"
    assert "heuristic_features" in data

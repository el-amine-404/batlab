import os
import tempfile
import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.engine import (
    LexicalQualityAssessor,
    OcrCoordinator,
    IVectorDetector,
    IOcrRunner
)

client = TestClient(app)

class MockVectorDetector(IVectorDetector):
    def __init__(self, is_native=True, chars=250, sample="Université Mohammed V Rabat Faculté des Sciences"):
        self._is_native = is_native
        self._chars = chars
        self._sample = sample

    def is_native_vector_pdf(self, file_path: str):
        return self._is_native, self._chars, self._sample

class MockOcrRunner(IOcrRunner):
    def __init__(self, success=True):
        self._success = success

    def run_ocr(self, input_path: str, output_path: str):
        import shutil
        shutil.copy2(input_path, output_path)
        return self._success, "OCR completed"

def test_health_check():
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert data["service"] == "pdf-ocr"

def test_quality_assessor_clean_text():
    assessor = LexicalQualityAssessor()
    clean_text = (
        "Université Mohammed V Rabat Faculté des Sciences. "
        "Rapport de stage et projet informatique de fin d'etudes. "
        "Architecture logicielle et systeme de gestion des donnees en Java et Python."
    )
    result = assessor.assess_quality(clean_text)
    assert result["dictionary_validity_ratio"] >= 0.70
    assert result["noise_ratio"] < 0.10
    assert result["needs_vision_escalation"] is False

def test_quality_assessor_arabic_text():
    assessor = LexicalQualityAssessor()
    clean_arabic = (
        "المملكة المغربية وزارة التعليم العالي والبحث العلمي "
        "جامعة محمد الخامس الرباط كلية العلوم شهادة النجاح الرسمية في سلك الماستر"
    )
    result = assessor.assess_quality(clean_arabic)
    assert result["detected_arabic"] is True
    assert result["dictionary_validity_ratio"] >= 0.70
    assert result["needs_vision_escalation"] is False

def test_quality_assessor_detects_garbage_text():
    assessor = LexicalQualityAssessor()
    # Noisy OCR artifact text
    garbage_text = "!#@?%& 01x l9m~_ z#?^ *!!~ q193498 %%$#@!! &*()_=+"
    result = assessor.assess_quality(garbage_text)
    # Must flag for nighttime vision escalation
    assert result["needs_vision_escalation"] is True
    assert result["dictionary_validity_ratio"] < 0.65 or result["noise_ratio"] > 0.25

def test_quality_assessor_handles_empty_text():
    assessor = LexicalQualityAssessor()
    result = assessor.assess_quality("")
    assert result["needs_vision_escalation"] is True

def test_native_vector_bypasses_ocr():
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as in_f:
        in_f.write(b"%PDF-1.4 mock native vector content")
        in_f.flush()
        in_path = in_f.name

    out_path = in_path + "_out.pdf"

    try:
        coordinator = OcrCoordinator(
            detector=MockVectorDetector(is_native=True, chars=200),
            runner=MockOcrRunner(success=True)
        )
        report = coordinator.process(in_path, out_path)

        assert report["status"] == "PROCESSED"
        assert report["is_native_vector"] is True
        assert report["ocr_applied"] is False  # Bypassed OCR
        assert report["needs_vision_escalation"] is False
    finally:
        if os.path.exists(in_path):
            os.remove(in_path)
        if os.path.exists(out_path):
            os.remove(out_path)

def test_scanned_image_triggers_lossless_ocr():
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as in_f:
        in_f.write(b"%PDF-1.4 mock scanned image")
        in_f.flush()
        in_path = in_f.name

    out_path = in_path + "_out.pdf"

    try:
        coordinator = OcrCoordinator(
            detector=MockVectorDetector(is_native=False, chars=0, sample=""),
            runner=MockOcrRunner(success=True)
        )
        report = coordinator.process(in_path, out_path)

        assert report["status"] == "PROCESSED"
        assert report["is_native_vector"] is False
        assert report["ocr_applied"] is True  # OCR was run
    finally:
        if os.path.exists(in_path):
            os.remove(in_path)
        if os.path.exists(out_path):
            os.remove(out_path)

def test_api_endpoint_headers():
    response = client.post(
        "/api/v1/ocr",
        files={"file": ("test.pdf", b"%PDF-1.4 minimal test", "application/pdf")}
    )
    assert response.status_code == 200
    assert "x-status" in response.headers
    assert "x-is-native-vector" in response.headers
    assert "x-needs-vision-escalation" in response.headers

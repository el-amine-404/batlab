import io
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from src.main import app
from src.pipeline import OrchestratorPipeline
from src.night_worker import NightVisionWorker

client = TestClient(app)

def test_health_check():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"

@pytest.mark.asyncio
async def test_deduplication_skips_processing():
    mock_db = MagicMock()
    mock_db.check_deduplication.return_value = {
        "sha256": "abcdef123456",
        "original_filename": "invoice.pdf",
        "status": "DISPATCHED",
        "target_system": "paperless",
        "lab2_dispatch_id": "uuid-123"
    }

    mock_clients = MagicMock()
    mock_dispatcher = MagicMock()

    pipeline = OrchestratorPipeline(db=mock_db, clients=mock_clients, dispatcher=mock_dispatcher)
    res = await pipeline.process_document(b"%PDF-dummy", "invoice.pdf")

    assert res["status"] == "DEDUPLICATED"
    assert res["target_system"] == "paperless"
    # Downstream services should not have been called
    mock_clients.scan_security.assert_not_called()

@pytest.mark.asyncio
async def test_infected_file_quarantined():
    mock_db = MagicMock()
    mock_db.check_deduplication.return_value = None

    mock_clients = MagicMock()
    mock_clients.scan_security = AsyncMock(return_value={
        "verdict": "INFECTED",
        "threats": ["Exploit.PDF.MaliciousJavaScript"]
    })

    mock_dispatcher = MagicMock()
    mock_dispatcher.quarantine.return_value = "/data/quarantine/infected.pdf"

    pipeline = OrchestratorPipeline(db=mock_db, clients=mock_clients, dispatcher=mock_dispatcher)
    res = await pipeline.process_document(b"%PDF-evil", "evil.pdf")

    assert res["status"] == "QUARANTINED"
    assert "Exploit.PDF.MaliciousJavaScript" in res["threats"]
    mock_dispatcher.quarantine.assert_called_once()
    # Downstream sanitizer & OCR should not be called
    mock_clients.sanitize_metadata.assert_not_called()

@pytest.mark.asyncio
async def test_clean_file_end_to_end_flow():
    mock_db = MagicMock()
    mock_db.check_deduplication.return_value = None

    mock_clients = MagicMock()
    mock_clients.scan_security = AsyncMock(return_value={"verdict": "CLEAN", "threats": []})
    mock_clients.sanitize_metadata = AsyncMock(return_value=(
        b"%PDF-clean",
        {"x-preserved-title": "Invoice 2026", "x-preserved-author": "Cisco"}
    ))
    mock_clients.process_ocr = AsyncMock(return_value=(
        b"%PDF-ocr",
        {
            "x-is-native-vector": "false",
            "x-ocr-applied": "true",
            "x-dict-validity-ratio": "0.85",
            "x-noise-ratio": "0.02",
            "x-needs-vision-escalation": "false"
        }
    ))
    mock_clients.enrich_metadata = AsyncMock(return_value={
        "verified_title": "Cisco Invoice March 2026",
        "verified_author": "Cisco Systems",
        "domain": "FINANCE",
        "subdomain": "INVOICES",
        "tags": ["cisco", "invoice", "networking"],
        "target_system": "paperless"
    })

    mock_dispatcher = MagicMock()
    mock_dispatcher.route_document = AsyncMock(return_value="paperless_task_999")

    pipeline = OrchestratorPipeline(db=mock_db, clients=mock_clients, dispatcher=mock_dispatcher)
    res = await pipeline.process_document(b"%PDF-valid", "cisco_bill.pdf")

    assert res["status"] == "COMPLETED"
    assert res["verified_title"] == "Cisco Invoice March 2026"
    assert res["domain"] == "FINANCE"
    assert res["target_system"] == "paperless"
    assert res["dispatch_id"] == "paperless_task_999"

    # Verify DB registrations
    mock_db.register_or_update.assert_called()
    mock_db.record_ocr_metrics.assert_called_once()
    mock_db.record_ai_metadata.assert_called_once()
    mock_db.mark_dispatched.assert_called_once()

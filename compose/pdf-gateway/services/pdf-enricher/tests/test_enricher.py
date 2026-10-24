import io
import pytest
from fastapi.testclient import TestClient
import fitz

from src.main import app
from src.isbn_fetcher import is_valid_isbn10, is_valid_isbn13, extract_isbns
from src.verifier import CandidateMetadataVerifier
from src.ai_client import extract_json_from_response

client = TestClient(app)

def test_health_check():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"

def test_isbn_validation():
    # K&R C Book ISBN-10: 0131103628
    assert is_valid_isbn10("0131103628")
    assert is_valid_isbn10("0-13-110362-8")
    assert not is_valid_isbn10("0131103629")

    # Clean Code ISBN-13: 9780132350884
    assert is_valid_isbn13("9780132350884")
    assert is_valid_isbn13("978-0-13-235088-4")
    assert not is_valid_isbn13("9780132350885")

def test_isbn_extraction():
    text = (
        "Published by Prentice Hall. ISBN-13: 978-0-13-235088-4. "
        "Also available in older editions under ISBN 0131103628. "
        "Random phone number 123-456-7890 should not match."
    )
    isbns = extract_isbns(text)
    assert "9780132350884" in isbns
    assert "0131103628" in isbns
    assert len(isbns) == 2

def test_verifier_rejects_garbage_titles():
    verifier = CandidateMetadataVerifier()
    doc_text = "This is a technical report on Artificial Intelligence Systems."
    
    # Generic MS Word leftovers
    v1, t1 = verifier.evaluate_title("Microsoft Word - Document1", doc_text)
    assert v1 == "REJECTED"

    # Generic scanner titles
    v2, t2 = verifier.evaluate_title("Scan_20261024", doc_text)
    assert v2 == "REJECTED"

    v3, t3 = verifier.evaluate_title("Untitled", doc_text)
    assert v3 == "REJECTED"

    # URL / website title
    v4, t4 = verifier.evaluate_title("https://example.com/downloads/doc.pdf", doc_text)
    assert v4 == "REJECTED"

    # Legitimate title matching text
    v5, t5 = verifier.evaluate_title("Artificial Intelligence Systems", doc_text)
    assert v5 == "VERIFIED"
    assert t5 == "Artificial Intelligence Systems"

def test_verifier_rejects_scanner_and_system_authors():
    verifier = CandidateMetadataVerifier()
    doc_text = "Written by John Carmack in 1993."

    v1, _ = verifier.evaluate_author("Canon", doc_text)
    assert v1 == "REJECTED"

    v2, _ = verifier.evaluate_author("HP OfficeJet 5000", doc_text)
    assert v2 == "REJECTED"

    v3, _ = verifier.evaluate_author("Administrator", doc_text)
    assert v3 == "REJECTED"

    v4, a4 = verifier.evaluate_author("John Carmack", doc_text)
    assert v4 == "VERIFIED"
    assert a4 == "John Carmack"

def test_json_extractor_handles_markdown_and_raw():
    raw_json = '{"verified_title": "Clean Code", "domain": "TECH"}'
    markdown_json = f"```json\n{raw_json}\n```"
    markdown_no_lang = f"```\n{raw_json}\n```"

    assert extract_json_from_response(raw_json)["verified_title"] == "Clean Code"
    assert extract_json_from_response(markdown_json)["verified_title"] == "Clean Code"
    assert extract_json_from_response(markdown_no_lang)["verified_title"] == "Clean Code"

def test_api_enrich_text_endpoint():
    payload = {
        "sha256": "abc12345",
        "filename": "2026-06-10_facture_beinsport.pdf",
        "text_sample": "FACTURE beIN SPORTS MENA. Montant: 450 MAD. Date: 10/06/2026. Merci pour votre abonnement.",
        "candidate_metadata": {
            "title": "Microsoft Word - facture",
            "author": "HP ScanJet"
        }
    }
    resp = client.post("/api/v1/enrich", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["sha256"] == "abc12345"
    assert "target_system" in data
    assert data["target_system"] in ["paperless", "calibre", "archive"]
    assert "domain" in data
    assert "subdomain" in data
    assert "tags" in data

def test_api_enrich_pdf_endpoint():
    # Create simple in-memory PDF with PyMuPDF
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "UNIVERSITÉ MOHAMMED V\nFaculté des Sciences de Rabat\nExamen Algorithmique et Programmation")
    pdf_bytes = doc.tobytes()
    doc.close()

    files = {"file": ("examen_algo.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
    resp = client.post(
        "/api/v1/enrich-pdf",
        files=files,
        data={"candidate_title": "Examen Algorithmique", "candidate_author": "FSR"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["verified_title"] is not None
    assert data["domain"] in ["ACADEMIC", "TECH", "ADMINISTRATIVE", "GENERAL"]
    assert data["target_system"] in ["paperless", "calibre", "archive"]

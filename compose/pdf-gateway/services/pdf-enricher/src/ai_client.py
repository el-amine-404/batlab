import json
import re
import logging
from typing import Dict, Any, Optional, List
import httpx
from .config import config

logger = logging.getLogger("pdf-enricher.ai")

SYSTEM_PROMPT = """You are an expert document cataloging, metadata extraction, and indexing engine.
Analyze the provided document text, original filename, and any candidate metadata.

Determine:
1. "verified_title": The true, canonical title of the document including subtitle if present (e.g. 'La Logistique Urbaine — Défis et Solutions' or 'Facture beIN Sports').
2. "verified_author": The individual author(s), creators, or issuing entity (e.g., 'Amine El Houjjaji et al.' or 'beIN Sports').
3. "document_date": Creation, presentation, or issue date formatted as YYYY-MM-DD if found in text (e.g. '2024-11-19'), else null.
4. "document_type": Standard classification: 'Presentation', 'Invoice', 'Receipt', 'Contract', 'Academic Paper', 'ID Document', 'Coursework', 'Report', 'Certificate', or 'General'.
5. "correspondent": The external party, vendor, company, or institution (e.g. 'beIN Sports', 'Maroc Telecom', 'ISGA'). For student presentations or personal documents where the student is the author, set correspondent to the institution or null (NEVER the student's personal name).
6. "domain": High-level category in UPPERCASE (e.g., TECH, FINANCE, ADMINISTRATIVE, LEGAL, ACADEMIC, PERSONAL).
7. "subdomain": Granular subcategory in UPPERCASE (e.g., LOGISTICS, NETWORKING, INVOICES, PRESENTATIONS, IDENTITY).
8. "subject": A professional, natural-language topic description in standard Title Case (e.g., 'Logistics and Urban Mobility' or 'Abonnement Sportif beIN'). Do NOT use uppercase slashes or raw internal enums.
9. "tags": List of 3 to 6 lowercase descriptive tags (e.g., ["presentation", "urban-logistics", "academic"]).
10. "markdown_summary": A concise 2-paragraph summary in Markdown in the document language.
11. "target_system": Destination archive system ('calibre', 'paperless', 'archive').
12. "confidence_score": Confidence from 0.1 to 1.0.

You MUST reply with ONLY a single valid JSON object. Do not include markdown code block syntax or conversational text.
Format:
{
  "verified_title": "...",
  "verified_author": "...",
  "document_date": "YYYY-MM-DD",
  "document_type": "...",
  "correspondent": "...",
  "domain": "...",
  "subdomain": "...",
  "subject": "...",
  "tags": ["..."],
  "markdown_summary": "...",
  "target_system": "paperless",
  "confidence_score": 0.95
}
"""

def extract_json_from_response(text: str) -> Dict[str, Any]:
    """
    Extracts valid JSON from LLM response, stripping markdown wrappers if present.
    """
    clean_text = text.strip()
    # Check for markdown code blocks
    match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', clean_text)
    if match:
        clean_text = match.group(1).strip()
    else:
        # Match outermost braces
        brace_match = re.search(r'(\{[\s\S]*\})', clean_text)
        if brace_match:
            clean_text = brace_match.group(1).strip()

    return json.loads(clean_text)

class AIEnricherClient:
    def __init__(self, base_url: str = None, text_model: str = None, vision_model: str = None):
        self.base_url = (base_url or config.lm_studio_url).rstrip("/")
        self.text_model = text_model or config.text_model
        self.vision_model = vision_model or config.vision_model

    async def classify_text(
        self,
        filename: str,
        text_sample: str,
        candidate_metadata: Dict[str, Any],
        isbn_info: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Calls Qwen2.5-3B-Instruct to extract verified metadata, dynamic 2-tier taxonomy, and routing.
        """
        # Trim text sample to ~3500 chars (~800 tokens) for sub-second processing
        truncated_text = text_sample[:3500].strip() if text_sample else "(No extractable text)"

        prompt_content = (
            f"Original Filename: {filename}\n"
            f"Candidate Title: {candidate_metadata.get('title', '')}\n"
            f"Candidate Author: {candidate_metadata.get('author', '')}\n"
        )
        if isbn_info:
            prompt_content += (
                f"Authoritative ISBN Metadata:\n"
                f"- ISBN: {isbn_info.get('isbn')}\n"
                f"- Book Title: {isbn_info.get('title')}\n"
                f"- Authors: {', '.join(isbn_info.get('authors', []))}\n"
                f"- Publishers: {', '.join(isbn_info.get('publishers', []))}\n"
            )

        prompt_content += f"\nDocument Content Sample:\n{truncated_text}\n"

        payload = {
            "model": self.text_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt_content}
            ],
            "temperature": 0.1,
            "max_tokens": 400
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(f"{self.base_url}/chat/completions", json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    parsed = extract_json_from_response(content)
                    
                    # Sanitize target_system
                    target = parsed.get("target_system", "").lower()
                    if target not in ["calibre", "paperless", "archive"]:
                        target = "paperless" if not isbn_info else "calibre"
                    parsed["target_system"] = target

                    return parsed
                else:
                    logger.warning(f"LM Studio API returned status {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Failed to query LM Studio ({type(e).__name__}): {repr(e)}")

        # Heuristic fallback if AI unavailable
        return self._heuristic_fallback(filename, text_sample, candidate_metadata, isbn_info)

    async def classify_vision(
        self,
        filename: str,
        base64_images: List[str],
        candidate_metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Calls Qwen2.5-VL-3B-Instruct for visual OCR and document classification (Night Batch).
        """
        image_contents = [
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}
            }
            for b64_img in base64_images[:2] # Limit to first 2 pages
        ]

        user_content = [
            {
                "type": "text",
                "text": f"Original Filename: {filename}\nCandidate Title: {candidate_metadata.get('title', '')}\nExamine the page images. Read handwritten text, ID cards, forms, or stamps."
            }
        ] + image_contents

        payload = {
            "model": self.vision_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content}
            ],
            "temperature": 0.1,
            "max_tokens": 500
        }

        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.post(f"{self.base_url}/chat/completions", json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    return extract_json_from_response(content)
        except Exception as e:
            logger.error(f"Vision fallback failed: {str(e)}")

        return self._heuristic_fallback(filename, "", candidate_metadata, None)

    def _heuristic_fallback(
        self,
        filename: str,
        text_sample: str,
        candidate_metadata: Dict[str, Any],
        isbn_info: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Guarantees zero pipeline failure if LLM is offline or busy.
        Provides clean fallback extraction without fabrications or URL titles.
        """
        # Determine title safely
        cand_t = candidate_metadata.get("title")
        if cand_t and len(cand_t) <= 80 and not any(ch in cand_t for ch in ["?", "%", "=", "/", "\\", ".com", "http"]):
            title = cand_t
        else:
            # Clean base filename
            cleaned_base = re.sub(r'^\d{4}[-_.]\d{1,2}[-_.]\d{1,2}[-_ ]*', '', filename)
            cleaned_base = re.sub(r'\.pdf$', '', cleaned_base, flags=re.IGNORECASE).replace("_", " ").strip()
            title = cleaned_base.title() or "Untitled Document"

        author = candidate_metadata.get("author") or "Unknown"
        target_system = "paperless"
        domain = "ADMINISTRATIVE"
        subdomain = "GENERAL"
        document_type = "Document"
        correspondent = None
        doc_date = None
        tags = ["document"]

        # Date extraction heuristic (French & ISO formats)
        french_months = {
            "janvier": "01", "fevrier": "02", "février": "02", "mars": "03", "avril": "04",
            "mai": "05", "juin": "06", "juillet": "07", "aout": "08", "août": "08",
            "septembre": "09", "octobre": "10", "novembre": "11", "decembre": "12", "décembre": "12"
        }
        m_fr = re.search(r'\b(\d{1,2})\s+([a-zA-Zéû]+)\s+(\d{4})\b', text_sample, re.IGNORECASE)
        if m_fr:
            d_val, mo_name, y_val = m_fr.groups()
            mo_code = french_months.get(mo_name.lower())
            if mo_code:
                doc_date = f"{y_val}-{mo_code}-{int(d_val):02d}"

        if not doc_date:
            m_iso = re.search(r'\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b', text_sample + " " + filename)
            if m_iso:
                y_val, mo_val, d_val = m_iso.groups()
                doc_date = f"{y_val}-{int(mo_val):02d}-{int(d_val):02d}"

        # Standard classification from text keywords
        text_lower = text_sample.lower()
        if any(k in text_lower or k in filename.lower() for k in ["facture", "invoice", "receipt", "bill", "quittance"]):
            document_type = "Invoice"
            domain = "FINANCE"
            subdomain = "INVOICES"
            tags.extend(["invoice", "finance"])
            if "bein" in text_lower or "bein" in filename.lower():
                correspondent = "beIN Sports"
        elif any(k in text_lower or k in filename.lower() for k in ["diapositive", "slides", "presentation", "présentation"]):
            document_type = "Presentation"
            domain = "ACADEMIC"
            subdomain = "PRESENTATIONS"
            tags.extend(["presentation", "slides"])
        elif any(k in text_lower or k in filename.lower() for k in ["cours", "examen", "tp", "td", "universite", "exercise"]):
            document_type = "Coursework"
            domain = "ACADEMIC"
            subdomain = "COURSEWORK"
            tags.extend(["academic", "studies"])

        if isbn_info:
            target_system = "calibre"
            domain = "ACADEMIC"
            subdomain = "BOOKS"
            document_type = "Book"
            title = isbn_info.get("title", title)
            if isbn_info.get("authors"):
                author = ", ".join(isbn_info["authors"])
            tags.extend(["book", "isbn"])

        clean_subject = f"{document_type} - {title}" if document_type != "Document" else title
        markdown_summary = f"### {title}\n\nDocument classé comme **{document_type}**.\n\n"
        if author and author != "Unknown":
            markdown_summary += f"**Auteur :** {author}\n\n"
        if doc_date:
            markdown_summary += f"**Date :** {doc_date}\n\n"

        return {
            "verified_title": title,
            "verified_author": author,
            "document_date": doc_date,
            "document_type": document_type,
            "correspondent": correspondent,
            "domain": domain,
            "subdomain": subdomain,
            "subject": clean_subject,
            "tags": list(set(tags)),
            "markdown_summary": markdown_summary,
            "target_system": target_system,
            "confidence_score": 0.5
        }

        return {
            "verified_title": title,
            "verified_author": author,
            "document_date": doc_date,
            "document_type": document_type,
            "correspondent": correspondent,
            "domain": domain,
            "subdomain": subdomain,
            "tags": list(set(tags)),
            "markdown_summary": markdown_summary,
            "target_system": target_system,
            "confidence_score": 0.7 if is_presentation else 0.5
        }

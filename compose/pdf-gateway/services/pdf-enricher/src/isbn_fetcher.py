import re
from typing import Optional, Dict, Any, List
import httpx
from .config import config

ISBN_CANDIDATE_REGEX = re.compile(
    r'(?:ISBN(?:-1[03])?:?\s*)?((?:97[89][- ]?)?[0-9]{1,5}[- ]?[0-9]+[- ]?[0-9]+[- ]?[0-9X])\b',
    re.IGNORECASE
)

def is_valid_isbn10(isbn: str) -> bool:
    clean = isbn.replace("-", "").replace(" ", "").upper()
    if len(clean) != 10:
        return False
    try:
        total = sum(int(clean[i]) * (10 - i) for i in range(9))
        last = 10 if clean[9] == 'X' else int(clean[9])
        return ((total + last) % 11) == 0
    except ValueError:
        return False

def is_valid_isbn13(isbn: str) -> bool:
    clean = isbn.replace("-", "").replace(" ", "")
    if len(clean) != 13 or not clean.isdigit():
        return False
    total = sum(int(num) * (1 if idx % 2 == 0 else 3) for idx, num in enumerate(clean))
    return (total % 10) == 0

def extract_isbns(text: str) -> List[str]:
    """
    Finds and validates all ISBN-10 or ISBN-13 strings in text.
    """
    matches = ISBN_CANDIDATE_REGEX.findall(text)
    valid_isbns = []
    seen = set()

    for match in matches:
        clean = re.sub(r'[^0-9X]', '', match.upper())
        if clean in seen:
            continue
        if len(clean) == 13 and is_valid_isbn13(clean):
            valid_isbns.append(clean)
            seen.add(clean)
        elif len(clean) == 10 and is_valid_isbn10(clean):
            valid_isbns.append(clean)
            seen.add(clean)

    return valid_isbns

class IsbnFetcher:
    def __init__(self, timeout: float = None):
        self.timeout = timeout or config.openlibrary_timeout_sec

    async def fetch_openlibrary(self, isbn: str, client: httpx.AsyncClient) -> Optional[Dict[str, Any]]:
        url = f"https://openlibrary.org/isbn/{isbn}.json"
        try:
            resp = await client.get(url, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                title = data.get("title", "")
                if data.get("subtitle"):
                    title = f"{title}: {data['subtitle']}"
                
                publishers = data.get("publishers", [])
                publish_date = data.get("publish_date", "")
                
                authors = []
                for author_ref in data.get("authors", []):
                    author_key = author_ref.get("key")
                    if author_key:
                        try:
                            a_resp = await client.get(f"https://openlibrary.org{author_key}.json", timeout=2.0)
                            if a_resp.status_code == 200:
                                authors.append(a_resp.json().get("name", ""))
                        except Exception:
                            pass

                return {
                    "source": "openlibrary",
                    "isbn": isbn,
                    "title": title,
                    "authors": authors,
                    "publishers": publishers,
                    "publish_date": publish_date,
                    "subjects": data.get("subjects", [])
                }
        except Exception:
            pass
        return None

    async def fetch_google_books(self, isbn: str, client: httpx.AsyncClient) -> Optional[Dict[str, Any]]:
        url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}"
        try:
            resp = await client.get(url, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                if items:
                    v_info = items[0].get("volumeInfo", {})
                    return {
                        "source": "google_books",
                        "isbn": isbn,
                        "title": v_info.get("title", ""),
                        "authors": v_info.get("authors", []),
                        "publishers": [v_info.get("publisher")] if v_info.get("publisher") else [],
                        "publish_date": v_info.get("publishedDate", ""),
                        "subjects": v_info.get("categories", []),
                        "description": v_info.get("description", "")
                    }
        except Exception:
            pass
        return None

    async def lookup(self, isbn: str) -> Optional[Dict[str, Any]]:
        async with httpx.AsyncClient(headers={"User-Agent": "AntigravityPdfPipeline/1.0"}) as client:
            meta = await self.fetch_openlibrary(isbn, client)
            if meta and meta.get("title"):
                return meta
            
            meta_gb = await self.fetch_google_books(isbn, client)
            if meta_gb and meta_gb.get("title"):
                return meta_gb

        return None

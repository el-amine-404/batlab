import re
from typing import Dict, Any, Tuple

GENERIC_TITLES_PATTERN = re.compile(
    r'^(untitled|document\d*|scan\w*|image|page\s*\d+|print(?:out)?|output|new\s*doc|word\s*document|adobe\s*acrobat|untitled\s*document)$',
    re.IGNORECASE
)

MS_WORD_PREFIX_PATTERN = re.compile(r'^microsoft\s+word\s*-\s*', re.IGNORECASE)

URL_OR_FILE_PATTERN = re.compile(
    r'(https?://|www\.|\.pdf$|\.docx?$|\.html?$|\.txt$)',
    re.IGNORECASE
)

HEX_HASH_OR_UUID_PATTERN = re.compile(
    r'^[0-9a-fA-F]{32}$|^[0-9a-fA-F-]{36}$'
)

SCANNER_OR_GENERIC_WORDS = {
    "admin", "administrator", "user", "owner", "root", "guest", "default",
    "canon", "hp", "xerox", "brother", "epson", "ricoh", "konica", "minolta",
    "fujitsu", "kyocera", "lexmark", "scanner", "scan", "scanjet", "officejet",
    "deskjet", "laserjet", "pc", "desktop", "laptop"
}

class CandidateMetadataVerifier:
    """
    Examines candidate metadata extracted from PDF properties.
    Rejects toxic/generic leftovers and confirms valid titles/authors
    against document text content.
    """

    @staticmethod
    def clean_title(title: str) -> str:
        if not title:
            return ""
        t = title.strip()
        # Strip common artifact prefixes
        t = MS_WORD_PREFIX_PATTERN.sub('', t).strip()
        # Strip file extensions if someone named the title filename.pdf
        t = re.sub(r'\.pdf$', '', t, flags=re.IGNORECASE).strip()
        return t

    @classmethod
    def evaluate_title(cls, candidate_title: str, text_sample: str) -> Tuple[str, str]:
        """
        Returns (verdict, cleaned_title) where verdict is:
        - "REJECTED": completely invalid/garbage
        - "PLAUSIBLE": plausible but needs AI cross-check
        - "VERIFIED": exact or high-confidence match found in document text
        """
        cleaned = cls.clean_title(candidate_title)
        if not cleaned or len(cleaned) < 3:
            return "REJECTED", ""

        if GENERIC_TITLES_PATTERN.match(cleaned):
            return "REJECTED", ""

        # Reject URLs, web addresses, query strings, and tracking tokens
        cleaned_lower = cleaned.lower()
        if any(token in cleaned_lower for token in [".com", ".org", ".net", ".gov", ".io", "http:", "https:", "www.", "?", "%", "&"]):
            return "REJECTED", ""

        if "/" in cleaned or "\\" in cleaned or len(cleaned) > 100:
            return "REJECTED", ""

        if HEX_HASH_OR_UUID_PATTERN.match(cleaned):
            return "REJECTED", ""

        # Check if candidate title appears in document text sample
        clean_norm = re.sub(r'[\W_]+', ' ', cleaned.lower()).strip()
        text_norm = re.sub(r'[\W_]+', ' ', text_sample.lower()).strip()

        if clean_norm and clean_norm in text_norm:
            return "VERIFIED", cleaned

        # Check if key words (excluding stopwords) match
        words = [w for w in clean_norm.split() if len(w) > 3]
        if words and sum(1 for w in words if w in text_norm) / len(words) >= 0.7:
            return "PLAUSIBLE", cleaned

        return "PLAUSIBLE", cleaned

    @classmethod
    def evaluate_author(cls, candidate_author: str, text_sample: str) -> Tuple[str, str]:
        """
        Returns (verdict, cleaned_author)
        """
        if not candidate_author:
            return "REJECTED", ""

        cleaned = candidate_author.strip()
        if len(cleaned) < 2:
            return "REJECTED", ""

        # Check for generic / scanner words
        low = cleaned.lower()
        for w in SCANNER_OR_GENERIC_WORDS:
            if re.search(r'\b' + re.escape(w) + r'\b', low):
                return "REJECTED", ""

        if HEX_HASH_OR_UUID_PATTERN.match(cleaned):
            return "REJECTED", ""

        clean_norm = re.sub(r'[\W_]+', ' ', cleaned.lower()).strip()
        text_norm = re.sub(r'[\W_]+', ' ', text_sample.lower()).strip()

        if clean_norm and clean_norm in text_norm:
            return "VERIFIED", cleaned

        return "PLAUSIBLE", cleaned

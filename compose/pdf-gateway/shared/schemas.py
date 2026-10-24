"""
Shared Pydantic data models for the PDF processing microservices.
"""
from enum import Enum
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field
from datetime import datetime

class ScanVerdict(str, Enum):
    CLEAN = "CLEAN"
    SUSPICIOUS = "SUSPICIOUS"
    INFECTED = "INFECTED"

class SecurityScanReport(BaseModel):
    filename: str
    sha256: str
    size_bytes: int
    verdict: ScanVerdict
    threats: List[str] = Field(default_factory=list)
    heuristic_features: Dict[str, int] = Field(default_factory=dict)
    clamav_message: Optional[str] = None
    scan_timestamp: datetime = Field(default_factory=datetime.utcnow)

class HealthStatus(BaseModel):
    status: str
    service: str
    version: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class SanitizeReport(BaseModel):
    original_sha256: str
    clean_sha256: str
    status: str
    preserved_catalog_metadata: Dict[str, Any] = Field(default_factory=dict)
    toxic_fingerprints_purged: List[str] = Field(default_factory=list)

class OcrQualityMetrics(BaseModel):
    dictionary_validity_ratio: float = 0.0
    noise_ratio: float = 0.0

class OcrReport(BaseModel):
    status: str
    is_native_vector: bool
    ocr_applied: bool
    needs_vision_escalation: bool
    extracted_chars_count: int
    quality_metrics: OcrQualityMetrics

class TargetSystem(str, Enum):
    PAPERLESS = "paperless"
    CALIBRE = "calibre"
    ARCHIVE = "archive"

class EnrichmentRequest(BaseModel):
    sha256: str
    filename: str
    text_sample: str
    candidate_metadata: Dict[str, Any] = Field(default_factory=dict)
    force_vision: bool = False

class EnrichmentResponse(BaseModel):
    sha256: str
    verified_title: str
    verified_author: str
    domain: str
    subdomain: str
    tags: List[str] = Field(default_factory=list)
    target_system: TargetSystem
    isbn: Optional[str] = None
    isbn_metadata: Optional[Dict[str, Any]] = None
    metadata_payload: Dict[str, Any] = Field(default_factory=dict)
    vision_used: bool = False
    confidence_score: float = 1.0

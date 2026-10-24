import os
from pydantic import BaseModel

class SanitizerConfig(BaseModel):
    service_name: str = "pdf-sanitizer"
    service_version: str = "1.0.0"
    exiftool_path: str = os.getenv("EXIFTOOL_PATH", "exiftool")
    
    # Metadata tags considered personal/sensitive that MUST never remain
    sensitive_tag_patterns: list = [
        "author", "creator", "producer", "title", "subject", "keywords",
        "gps", "serial", "mac", "hostname", "username", "computer",
        "scanner", "printer", "software", "history", "modifydate", "createdate"
    ]

config = SanitizerConfig()

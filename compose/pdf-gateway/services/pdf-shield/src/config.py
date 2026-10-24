import os
from pydantic import BaseModel

class ShieldConfig(BaseModel):
    service_name: str = "pdf-shield"
    service_version: str = "1.0.0"
    clamav_host: str = os.getenv("CLAMAV_HOST", "localhost")
    clamav_port: int = int(os.getenv("CLAMAV_PORT", "3310"))
    clamd_socket: str = os.getenv("CLAMD_SOCKET", "/var/run/clamav/clamd.ctl")
    
    # Heuristic thresholds: tags that immediately flag a document as SUSPICIOUS
    critical_tags: list = ["/Launch", "/EmbeddedFiles", "/JS", "/JavaScript"]
    warning_tags: list = ["/OpenAction", "/AA", "/AcroForm", "/XFA"]

config = ShieldConfig()

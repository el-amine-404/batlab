import os

class OrchestratorConfig:
    service_name: str = "pdf-orchestrator"
    service_version: str = "1.0.0"
    service_port: int = int(os.getenv("ORCHESTRATOR_PORT", "8000"))

    # Internal Microservices
    shield_url: str = os.getenv("SHIELD_URL", "http://pdf-shield:8001")
    sanitizer_url: str = os.getenv("SANITIZER_URL", "http://pdf-sanitizer:8002")
    ocr_url: str = os.getenv("OCR_URL", "http://pdf-ocr:8003")
    enricher_url: str = os.getenv("ENRICHER_URL", "http://pdf-enricher:8004")

    # PostgreSQL Database
    postgres_host: str = os.getenv("POSTGRES_HOST", "pdf-db")
    postgres_port: int = int(os.getenv("POSTGRES_PORT", "5432"))
    postgres_user: str = os.getenv("POSTGRES_USER", "postgres")
    postgres_password: str = os.getenv("POSTGRES_PASSWORD", "postgres")
    postgres_db: str = os.getenv("POSTGRES_DB", "pipeline")

    # Target Destinations
    paperless_url: str = os.getenv("PAPERLESS_URL", "http://paperless:8000")
    paperless_token: str = os.getenv("PAPERLESS_TOKEN", "")
    paperless_host_header: str = os.getenv("PAPERLESS_HOST_HEADER", "paperless.homelab.lan")
    calibre_import_dir: str = os.getenv("CALIBRE_IMPORT_DIR", "/data/calibre_import")
    archive_dir: str = os.getenv("ARCHIVE_DIR", "/data/archive")
    quarantine_dir: str = os.getenv("QUARANTINE_DIR", "/data/quarantine")
    holding_dir: str = os.getenv("HOLDING_DIR", "/data/holding")

    # Apprise Notifications
    apprise_url: str = os.getenv("APPRISE_URL", "http://apprise:8000/notify/pdf-gateway")
    apprise_alert_tag: str = os.getenv("APPRISE_ALERT_TAG", "alerts")
    apprise_import_tag: str = os.getenv("APPRISE_IMPORT_TAG", "downloads")

    @property
    def postgres_connection_string(self) -> str:
        return f"postgresql://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

config = OrchestratorConfig()

import os

class EnricherConfig:
    service_name: str = "pdf-enricher"
    service_version: str = "1.0.0"
    service_port: int = int(os.getenv("ENRICHER_PORT", "8004"))
    
    # LM Studio Configuration
    # In docker, host.docker.internal maps to host with extra_hosts: host-gateway
    lm_studio_url: str = os.getenv("LM_STUDIO_URL", "http://host.docker.internal:1234/v1")
    text_model: str = os.getenv("TEXT_MODEL", "qwen2.5-3b-instruct")
    vision_model: str = os.getenv("VISION_MODEL", "qwen2.5-vl-3b-instruct")
    
    # OpenLibrary API
    openlibrary_timeout_sec: float = float(os.getenv("OPENLIBRARY_TIMEOUT_SEC", "4.0"))

config = EnricherConfig()

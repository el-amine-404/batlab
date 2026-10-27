from __future__ import annotations

DEFAULTS = {
    "retention_hours": 24,
    "link_ttl_minutes": 60,
    "max_files_per_project": 30,
    "max_upload_bytes": 25 * 1024 * 1024,
    "max_pixels": 40_000_000,
    "preview_long_edge": 1600,
    "jpeg_quality": 92,
    "default_profile": "faithful",
    "auto_accept_confidence": 0.82,
    "worker_max_attempts": 3,
}

LIMITS = {
    "retention_hours": (1, 168),
    "link_ttl_minutes": (5, 1440),
    "max_files_per_project": (1, 100),
    "max_upload_bytes": (1_048_576, 104_857_600),
    "max_pixels": (1_000_000, 100_000_000),
    "preview_long_edge": (640, 3000),
    "jpeg_quality": (70, 100),
    "auto_accept_confidence": (0.5, 0.99),
    "worker_max_attempts": (1, 8),
}


def validate(values: dict) -> dict:
    unknown = set(values) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown setting: {sorted(unknown)[0]}")
    merged = DEFAULTS | values
    if merged["default_profile"] not in {"faithful", "paper", "mono"}:
        raise ValueError("default_profile must be faithful, paper, or mono")
    for key, (low, high) in LIMITS.items():
        value = merged[key]
        if isinstance(DEFAULTS[key], int) and isinstance(value, bool):
            raise ValueError(f"{key} must be a number")
        if not isinstance(value, (int, float)) or not low <= value <= high:
            raise ValueError(f"{key} must be between {low} and {high}")
    return merged

from __future__ import annotations

DEFAULTS = {
    "retention_days": 0,  # 0: scans stay until someone deletes them
    "export_ttl_minutes": 1440,
    "max_files_per_project": 500,
    "max_upload_bytes": 50 * 1024 * 1024,
    "max_pixels": 60_000_000,
    "jpeg_quality": 92,
    "default_profile": "faithful",
    "worker_max_attempts": 3,
}

LIMITS = {
    "retention_days": (0, 3650),
    "export_ttl_minutes": (5, 10080),
    "max_files_per_project": (1, 1000),
    "max_upload_bytes": (1_048_576, 104_857_600),
    "max_pixels": (1_000_000, 100_000_000),
    "jpeg_quality": (70, 100),
    "worker_max_attempts": (1, 8),
}


def known(values: dict) -> dict:
    """What is stored, without settings of older releases, over the defaults."""
    return DEFAULTS | {key: value for key, value in values.items() if key in DEFAULTS}


def validate(values: dict) -> dict:
    unknown = set(values) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown setting: {sorted(unknown)[0]}")
    merged = DEFAULTS | values
    if merged["default_profile"] not in {"faithful", "paper", "mono"}:
        raise ValueError("default_profile must be faithful, paper, or mono")
    for key, (low, high) in LIMITS.items():
        value = merged[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"{key} must be a whole number between {low} and {high}")
    return merged

import os
import json
from typing import Dict, Any, Optional, List
from datetime import datetime
import psycopg
from psycopg.rows import dict_row

class DatabaseConfig:
    host: str = os.getenv("POSTGRES_HOST", "pdf-db")
    port: int = int(os.getenv("POSTGRES_PORT", "5432"))
    user: str = os.getenv("POSTGRES_USER", "postgres")
    password: str = os.getenv("POSTGRES_PASSWORD", "postgres")
    dbname: str = os.getenv("POSTGRES_DB", "pipeline")

    @classmethod
    def get_connection_string(cls) -> str:
        return f"postgresql://{cls.user}:{cls.password}@{cls.host}:{cls.port}/{cls.dbname}"

class PipelineDB:
    def __init__(self, conn_str: str = None):
        self.conn_str = conn_str or DatabaseConfig.get_connection_string()

    def get_connection(self):
        return psycopg.connect(self.conn_str, row_factory=dict_row)

    def check_deduplication(self, sha256: str) -> Optional[Dict[str, Any]]:
        """
        Instant O(1) deduplication check.
        Returns document record if already processed, else None.
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT sha256, original_filename, status, target_system, lab2_dispatch_id "
                    "FROM documents WHERE sha256 = %s",
                    (sha256,)
                )
                return cur.fetchone()

    def register_or_update(
        self,
        sha256: str,
        filename: str,
        file_size_bytes: int,
        status: str = "PENDING"
    ) -> Dict[str, Any]:
        """
        Insert new document record or update existing status.
        """
        query = """
        INSERT INTO documents (sha256, original_filename, file_size_bytes, status, updated_at)
        VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (sha256) DO UPDATE SET
            original_filename = EXCLUDED.original_filename,
            status = EXCLUDED.status,
            updated_at = CURRENT_TIMESTAMP
        RETURNING *;
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (sha256, filename, file_size_bytes, status))
                row = cur.fetchone()
                conn.commit()
                return dict(row)

    def record_ocr_metrics(
        self,
        sha256: str,
        is_native_vector: bool,
        ocr_applied: bool,
        detected_languages: str,
        dict_ratio: float,
        noise_ratio: float,
        needs_vision_review: bool
    ):
        query = """
        UPDATE documents SET
            is_native_vector = %s,
            ocr_applied = %s,
            detected_languages = %s,
            dictionary_validity_ratio = %s,
            noise_ratio = %s,
            needs_vision_review = %s,
            updated_at = CURRENT_TIMESTAMP
        WHERE sha256 = %s;
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    query,
                    (is_native_vector, ocr_applied, detected_languages, dict_ratio, noise_ratio, needs_vision_review, sha256)
                )
                conn.commit()

    def record_ai_metadata(
        self,
        sha256: str,
        verified_title: str,
        verified_author: str,
        domain: str,
        subdomain: str,
        tags: List[str],
        target_system: str,
        metadata_payload: Dict[str, Any]
    ):
        query = """
        UPDATE documents SET
            verified_title = %s,
            verified_author = %s,
            domain = %s,
            subdomain = %s,
            tags = %s::jsonb,
            target_system = %s,
            metadata_payload = %s::jsonb,
            status = 'ENRICHED',
            updated_at = CURRENT_TIMESTAMP
        WHERE sha256 = %s;
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    query,
                    (
                        verified_title,
                        verified_author,
                        domain,
                        subdomain,
                        json.dumps(tags),
                        target_system,
                        json.dumps(metadata_payload),
                        sha256
                    )
                )
                conn.commit()

    def get_night_vision_queue(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Sub-millisecond query leveraging partial index to retrieve pending night vision jobs.
        """
        query = """
        SELECT sha256, original_filename, domain, subdomain, dictionary_validity_ratio, noise_ratio
        FROM documents
        WHERE needs_vision_review = TRUE
        ORDER BY created_at ASC
        LIMIT %s;
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (limit,))
                return [dict(r) for r in cur.fetchall()]

    def mark_dispatched(self, sha256: str, target_system: str, dispatch_id: str):
        query = """
        UPDATE documents SET
            status = 'DISPATCHED',
            target_system = %s,
            lab2_dispatch_id = %s,
            updated_at = CURRENT_TIMESTAMP
        WHERE sha256 = %s;
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (target_system, dispatch_id, sha256))
                conn.commit()

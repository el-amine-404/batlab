import os
import logging
from typing import List, Dict, Any, Optional
import httpx
from .config import config

logger = logging.getLogger("pdf-orchestrator.notifier")

class AppriseNotifier:
    def __init__(self):
        self.apprise_url = config.apprise_url.rstrip("/")
        self.alert_tag = config.apprise_alert_tag
        self.import_tag = config.apprise_import_tag

    async def _send(self, title: str, body: str, tag: str = "alerts", kind: str = "info"):
        if not self.apprise_url:
            return

        payload = {
            "title": title[:250],
            "body": body[:3800],
            "type": kind,
            "format": "markdown",
            "tag": tag
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(self.apprise_url, json=payload)
                if resp.status_code in [200, 204]:
                    logger.info(f"Notification delivered via Apprise [{tag}/{kind}]: {title}")
                else:
                    logger.warning(f"Apprise returned status {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.warning(f"Could not reach Apprise at {self.apprise_url} ({e})")

    async def notify_quarantine(self, filename: str, threats: List[str], sha256: str):
        threat_list = "\n".join(f"• {t}" for t in threats) if threats else "Unknown threat"
        body = (
            f"**File**  {filename}\n"
            f"**Verdict**  🚨 QUARANTINED\n"
            f"**Threats Detected**\n{threat_list}\n"
            f"**SHA-256**  `{sha256[:16]}...`\n"
            f"**Action**  File isolated in restricted quarantine vault. Ingestion blocked."
        )
        await self._send(
            title=f"Security Alert: Exploit / Malware Quarantined",
            body=body,
            tag=self.alert_tag,
            kind="failure"
        )

    async def notify_ingest(
        self,
        filename: str,
        title: str,
        domain: str,
        subdomain: str,
        tags: List[str],
        dispatch_id: str
    ):
        tags_str = ", ".join(f"#{t}" for t in tags) if tags else "None"
        deep_link = ""
        if "http" in dispatch_id:
            for part in dispatch_id.split(":"):
                if part.startswith("http"):
                    deep_link = part + ":" + dispatch_id.split(part + ":")[1]
                    break

        body = (
            f"**Original File**  {filename}\n"
            f"**Verified Title**  {title}\n"
            f"**Classification**  {domain} / {subdomain}\n"
            f"**Tags**  {tags_str}\n"
        )
        if deep_link:
            body += f"**Paperless Link**  [View Document in Paperless]({deep_link})"
        else:
            body += f"**Dispatch**  {dispatch_id}"

        await self._send(
            title=f"PDF Ingested: {title}",
            body=body,
            tag=self.import_tag,
            kind="success"
        )

    async def notify_night_vision(self, count: int, results: List[Dict[str, Any]]):
        if count == 0:
            return
        doc_lines = []
        for r in results[:5]:
            doc_lines.append(f"• **{r.get('filename')}** ➔ {r.get('upgraded_title')}")
        if len(results) > 5:
            doc_lines.append(f"• ... and {len(results) - 5} more")

        body = (
            f"**Processed**  {count} degraded/handwritten document(s) upgraded via Quadro T1000 Vision.\n"
            f"**Upgraded Documents**\n" + "\n".join(doc_lines)
        )
        await self._send(
            title=f"Night Vision Batch Complete: {count} document(s)",
            body=body,
            tag=self.import_tag,
            kind="info"
        )

    async def notify_error(self, filename: str, error: str):
        body = (
            f"**File**  {filename}\n"
            f"**Error**  {error}\n"
            f"**Notice**  Check container logs on Lab2."
        )
        await self._send(
            title=f"PDF Pipeline Anomaly: {filename}",
            body=body,
            tag=self.alert_tag,
            kind="warning"
        )

notifier = AppriseNotifier()

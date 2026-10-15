"""Events: the log, the monthly reports and the message for what needs a look.

A part of Guard (guard.py): it uses self.config, self.state and the
other parts' methods."""

import json
import os
from datetime import datetime


from batlab_common import notify

from settings import FINDINGS, FINDINGS_LISTED, LIBRARY_CHANGES
from common import log, now_iso


class ReportingMixin:
    def event(self, kind, path, reason="", detail="", **extra):
        record = {"time": now_iso(), "event": kind, "path": path, "reason": reason, "detail": detail}
        record.update(extra)
        self.events.append(record)
        if kind in LIBRARY_CHANGES:
            self.library_changed = True

        log(f"{kind} {reason} {path} {detail}".rstrip())
        if self.config.dry_run:
            return
        reports = os.path.join(self.config.state_dir, "reports")
        os.makedirs(reports, exist_ok=True)
        with open(os.path.join(reports, datetime.now().strftime("%Y-%m") + ".jsonl"), "a",
                  encoding="utf-8") as out:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")

    def notify(self):
        """One message for what a pass found that needs a look, by kind."""
        worth = [e for e in self.events if e["event"] in FINDINGS]
        self.events = []
        if not worth or not self.config.notify_url or self.config.dry_run:
            return
        lines = []
        for kind, heading in FINDINGS.items():
            found = [e for e in worth if e["event"] == kind]
            if not found:
                continue
            lines.append(f"**{heading}**")
            for e in found[:FINDINGS_LISTED]:
                lines.append(f"- `{e['reason']}` {os.path.basename(e['path'])}"
                             + (f"\n  {e['detail'][:400]}" if e["detail"] else ""))
            if len(found) > FINDINGS_LISTED:
                lines.append(f"- … and {len(found) - FINDINGS_LISTED} more (/state/reports)")
        count = f"{len(worth)} file{'s' if len(worth) > 1 else ''}"
        try:
            notify.send(self.config.notify_url, f"musicguard: {count} to look at", "\n".join(lines),
                        tag=self.config.notify_issues, kind="warning")
        except notify.NotSent as error:  # a notification never stops the guard
            log(f"notification failed: {error}")

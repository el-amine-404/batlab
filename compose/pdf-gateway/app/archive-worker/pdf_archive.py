#!/usr/bin/env python3
"""pdf-archive: operate a portable archive without the gateway database.

  pdf-archive --root ROOT verify
  pdf-archive --root ROOT reindex --catalog catalog.sqlite
  pdf-archive --root ROOT repair [--apply]
  pdf-archive --root ROOT export --bundle out.tar [--document doc-...]
  pdf-archive --root ROOT import --bundle in.tar
  pdf-archive --root ROOT publish --pdf F --sha256 H --sidecar S.json [--text T]
  pdf-archive --root ROOT reindex-db      (PDF_GATEWAY_DATABASE_* from the environment)
  pdf-archive --root ROOT migrate-layout [--apply]   (first layout -> Section/Correspondent; then reindex-db)

Prints one JSON result. Exit 0 = ok, 1 = archive problems found, 2 = refused.
"""

import argparse
import json
import sys
from pathlib import Path

from archive import Archive, ArchiveError


def main() -> int:
    parser = argparse.ArgumentParser(prog="pdf-archive")
    parser.add_argument("--root", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("verify")
    reindex = commands.add_parser("reindex")
    reindex.add_argument("--catalog", type=Path, required=True)
    repair = commands.add_parser("repair")
    repair.add_argument("--apply", action="store_true")
    repair.add_argument("--grace-seconds", type=int, default=3600)
    export = commands.add_parser("export")
    export.add_argument("--bundle", type=Path, required=True)
    export.add_argument("--document", action="append")
    imported = commands.add_parser("import")
    imported.add_argument("--bundle", type=Path, required=True)
    commands.add_parser("reindex-db")
    migrate = commands.add_parser("migrate-layout")
    migrate.add_argument("--apply", action="store_true")
    publish = commands.add_parser("publish")
    publish.add_argument("--pdf", type=Path, required=True)
    publish.add_argument("--sha256", required=True)
    publish.add_argument("--sidecar", type=Path, required=True)
    publish.add_argument("--text", type=Path)
    args = parser.parse_args()

    archive = Archive(args.root)
    try:
        if args.command == "verify":
            result = archive.verify()
        elif args.command == "reindex":
            result = archive.reindex(args.catalog)
            result["ok"] = result["rejected"] == 0
        elif args.command == "repair":
            result = archive.repair(apply=args.apply, grace_seconds=args.grace_seconds)
            result["ok"] = not result["reportedOnly"]
        elif args.command == "export":
            result = {**archive.export(args.bundle, args.document), "ok": True}
        elif args.command == "reindex-db":
            import os
            import psycopg2
            from reindex_db import reindex_db
            connection = psycopg2.connect(host=os.getenv("PDF_GATEWAY_DATABASE_HOST", "postgres"),
                                          dbname=os.getenv("PDF_GATEWAY_DATABASE_NAME", "pdf_gateway"),
                                          user=os.getenv("PDF_GATEWAY_DATABASE_USER", "pdf_gateway"),
                                          password=os.environ["PDF_GATEWAY_DATABASE_PASSWORD"])
            try:
                result = reindex_db(archive, connection)
            finally:
                connection.close()
        elif args.command == "migrate-layout":
            result = {**archive.migrate_layout(apply=args.apply), "ok": True}
        elif args.command == "import":
            result = {**archive.import_bundle(args.bundle), "ok": True}
        else:
            sidecar = json.loads(args.sidecar.read_text(encoding="utf-8"))
            text = args.text.read_text(encoding="utf-8") if args.text else None
            result = {"published": str(archive.publish(args.pdf, args.sha256, sidecar, text).pdf), "ok": True}
    except ArchiveError as error:
        print(json.dumps({"ok": False, "code": error.code, "detail": error.detail}))
        return 2
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

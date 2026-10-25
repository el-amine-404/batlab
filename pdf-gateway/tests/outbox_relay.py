#!/usr/bin/env python3
"""Test-only stand-in for the API OutboxPublisher in stage tests.

Publishes due, unpublished outbox events of the given types to their queues and
marks them published. The end-to-end test uses the real API publisher instead.
"""

import json
import os
import sys

import pika
import psycopg2

QUEUES = {"CDR_QUEUED": "pdf.gateway.cdr.v1", "VALIDATION_QUEUED": "pdf.gateway.validation.v1",
          "METADATA_EDIT_QUEUED": "pdf.gateway.metadata-edit.v1",
          "ARCHIVE_QUEUED": "pdf.gateway.archive.v1"}

types = sys.argv[1:] or list(QUEUES)
db = psycopg2.connect(host="postgres", dbname="pdf_gateway", user="pdf_gateway", password=os.environ["PDF_GATEWAY_DATABASE_PASSWORD"])
broker = pika.BlockingConnection(pika.ConnectionParameters("rabbitmq", credentials=pika.PlainCredentials(
    "pdf_gateway", os.environ["PDF_GATEWAY_RABBITMQ_PASSWORD"])))
channel = broker.channel()
with db, db.cursor() as cursor:
    cursor.execute("SELECT id,event_type,payload FROM outbox_events WHERE published_at IS NULL AND available_at<=now() AND event_type = ANY(%s) ORDER BY created_at FOR UPDATE",
                   (types,))
    for event_id, event_type, payload in cursor.fetchall():
        channel.queue_declare(QUEUES[event_type], durable=True)
        channel.basic_publish("", QUEUES[event_type], json.dumps(payload), pika.BasicProperties(delivery_mode=2))
        cursor.execute("UPDATE outbox_events SET published_at=now() WHERE id=%s", (event_id,))
broker.close()

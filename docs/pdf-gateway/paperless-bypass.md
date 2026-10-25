# Paperless ingestion paths that bypass the gateway

The gateway protects only documents that enter through it. Paperless-ngx keeps
its own ingestion paths, and anything arriving that way gets **none** of the
gateway's quarantine, scanning, CDR or final validation. These paths were found
in the homelab configuration (`compose/paperless/docker-compose.yml`, checked
2026-10-25). Nothing has been changed; closing them is a deployment decision.

| Path | How it bypasses the gateway | Exposure | Recommendation |
|---|---|---|---|
| Consume folder `${DATA_ROOT}/documents/consume` (polled every 60 s, recursive, subfolders become tags) | Any file written there is ingested directly | Every container that mounts `${DATA_ROOT}` read-write can write it: in `compose/arr` five services (download clients and *arr apps); host accounts too | Mount narrower paths in those services, or point the consume folder somewhere only the gateway's projection could write. Once the gateway is the intended entry point, disable folder consumption or empty it. |
| Paperless web upload and its mobile apps | Direct upload in the Paperless UI | Any Paperless user | Use the gateway's UI/API for untrusted documents; restrict Paperless upload permission to the adapter's dedicated user if practical. |
| Paperless REST API (`post_document`) | Any API token can upload | Tokens of existing integrations (e.g. Paperless-GPT has API access) | Give the gateway adapter its own dedicated Paperless user and token, and keep other tokens to the permissions they need. |
| Mail fetching | Paperless can fetch attachments from mailboxes configured in its UI (stored in its database, not in Compose) | Not visible from Compose; check *Mail* in Paperless settings | If configured, route mail attachments through the gateway instead. |

What the gateway does guarantee: every document *it* imports is the archived,
reconstructed and re-validated copy, and is marked in Paperless with the custom
field `gateway_document_id`. In Paperless, a document **without** that field
did not come through the gateway.

Paperless 3.x notes that shaped the adapter: duplicate uploads are accepted
(only flagged in `duplicate_documents`), and the API exposes no file checksum.
Exactly-once therefore relies on the marker field plus the adapter's recorded
upload/task state, not on Paperless refusing duplicates. The adapter writes
nothing to Paperless's database or media directories.

# PDF Security Gateway: operations

The gateway is the `pdf-gateway` stack (`compose/pdf-gateway/docker-compose.yml`,
code in `compose/pdf-gateway/app`). It runs on lab2 with the host CDR provider.

## Install on a host

1. Secrets in `compose/.env` (see `.env.example`, the `pdf-gateway` lines): three
   `openssl rand -hex 32` values, the Paperless token (step 4) and the Homepage
   token (step 6).
2. Folders and their owners (each stage runs as its own uid):
   `make setup-pdf-gateway` (also part of `make setup`).
3. The CDR provider, as `potato` (rootless Podman + gVisor; `hosts/lab2/install-pdf-cdr-runtime.sh`
   installs the runtime once):

   ```sh
   hosts/lab2/pdf-cdr-provider/install.sh
   sudo loginctl enable-linger potato
   systemctl --user enable --now pdf-cdr-provider
   ```

   Its socket is `/mnt/docker-volumes/pdf-gateway/cdr-socket/cdr.sock`. Without
   it, uploads stop at `SANITIZING` and nothing is released. `PDF_CDR_OCR_LANGUAGE`
   (in `~/.config/pdf-cdr-provider/provider.env`, default `fra`) is the one language
   Dangerzone uses for the clean copy's invisible text layer; empty for none.
4. A dedicated Paperless user for the adapter (it may add documents, tags and
   custom fields, and read tasks; it is not an administrator):

   ```sh
   docker exec paperless python3 manage.py shell -c '
   from django.contrib.auth.models import User, Permission
   from rest_framework.authtoken.models import Token
   u, _ = User.objects.get_or_create(username="pdf-gateway")
   u.set_unusable_password(); u.is_staff = u.is_superuser = False; u.save()
   codes = ["add_document", "view_document", "change_document", "view_tag", "add_tag",
            "view_correspondent", "add_correspondent", "change_correspondent",
            "view_documenttype", "add_documenttype", "change_documenttype", "change_tag",
            "delete_document", "view_note", "add_note",
            "view_customfield", "add_customfield", "view_paperlesstask"]
   u.user_permissions.set(Permission.objects.filter(codename__in=codes))
   print(Token.objects.get_or_create(user=u)[0].key)'
   ```

   Put the printed token in `PDF_GATEWAY_PAPERLESS_TOKEN`.
5. Start: `make up STACK=pdf-gateway`, then `make up STACK=caddy` (port 443 and
   `pdf.caddy`).
6. Homepage's card: a READ_ONLY token, stored only as its SHA-256:

   ```sh
   token="$(openssl rand -hex 32)"
   docker exec pdf-gateway-db psql -U pdf_gateway -d pdf_gateway -c "
     INSERT INTO users(id,username,role) VALUES (gen_random_uuid(),'homepage','READ_ONLY');
     INSERT INTO api_tokens(id,user_id,token_hash,label)
       SELECT gen_random_uuid(),id,'$(printf %s "$token" | sha256sum | cut -d' ' -f1)','homepage'
       FROM users WHERE username='homepage';"
   ```

   Put `$token` in `PDF_GATEWAY_HOMEPAGE_TOKEN`, then `make up STACK=homepage`.

`make up` builds the gateway's images on the host (`pull_policy: build`, like the
other local apps), on the host network because lab2's bridge has no DNS for
builders. The first build (Maven, npm, Python wheels) is minutes of CPU on lab2;
later ones are cached. Loading images built elsewhere does not avoid it (the
build cache is not carried over). `make up STACK=pdf-gateway` builds them one at a
time, waiting while the CPU is above 75 °C (`make build-pdf-gateway` alone builds
without starting), then starts the stack. After a `git pull`, the same command
deploys the new version.

## Phones and PCs: trust the local CA once

The site is `https://pdf.homelab.lan`, with a certificate from Caddy's own CA
(no public certificate; the session cookie is `Secure`, so it needs HTTPS).

- **iPhone**: open `http://pdf.homelab.lan/ca.crt` in Safari, allow the profile
  download. Then Settings → General → VPN & Device Management → install it, and
  Settings → General → About → Certificate Trust Settings → turn on full trust
  for "Caddy Local Authority".
- **Fedora/Linux**: `curl -o caddy-local.crt http://pdf.homelab.lan/ca.crt`,
  `sudo cp caddy-local.crt /etc/pki/ca-trust/source/anchors/ && sudo update-ca-trust`.
  Firefox uses its own store: Settings → Certificates → Import.

The CA lives in `/mnt/docker-volumes/caddy/data/caddy/pki` (backed up). If it is
lost, Caddy makes a new one and every device must trust the new one.

## The AI files documents (laptop, optional)

When no filing rule recognises a document, the T1000 laptop's agent
(`compose/pdf-gateway/app/enrichment-agent`) files it with the LM Studio model
`qwen/qwen3-30b-a3b-2507` (about a minute a document; LM Studio splits it between
the GPU and RAM). The archive **waits up to 10 minutes** for it, so the saved PDF,
its folder and the Paperless copy are right from the start. If the laptop is off,
the document is saved at once as Unsorted and filed when the laptop is back.

The answer is applied by itself, field by field, and only what the document
supports: the type and section from fixed lists, a correspondent the text names,
a date printed in it, a number and an amount that appear verbatim, tags from the
archive's vocabulary (`manifests/taxonomy.json`, which you may extend). The page
says "Filled in by the AI"; fix anything with Edit. A document your rules filed
keeps its filing; the AI's answer is then only a suggestion.

1. On lab2: `PDF_GATEWAY_ENRICHMENT_ENABLED=true` in `compose/.env`, then recreate
   `api` and `archive-worker`.
2. An `ENRICHMENT` token (it can only lease jobs and read what it is given): make
   it on the laptop, store its SHA-256 on lab2 as in step 6 above, with the role
   `ENRICHMENT` and the user `t1000-agent`.
3. On the laptop, in `compose/pdf-gateway/app/enrichment-agent`:
   `agent.env` (from `agent.env.example`, mode 600, the token), and the CA:
   `curl -o caddy-local-ca.crt http://pdf.homelab.lan/ca.crt` (compare it with
   `/mnt/docker-volumes/caddy/data/caddy/pki/authorities/local/root.crt` on lab2).
   Then `docker compose up -d --build`. It restarts with Docker.
4. LM Studio's server must run on 127.0.0.1:1234; the agent loads the model when it
   has work, and LM Studio keeps it loaded (`lms unload --all` frees the RAM and GPU).
   The lighter `qwen/qwen3-4b-2507` (`PDF_GATEWAY_LLM_MODEL`, with
   `PDF_GATEWAY_LLM_CONTEXT=4096`) fits the GPU alone and takes ~17 s.

## The archive folder

`/mnt/storage/data/documents/pdf-archive/archive` is meant to be browsed:

```text
Finance/Acme Mobile/2026-06-10 Facture Acme Mobile - forfait 12 mois.pdf
Identity/Prefecture/2021-03-04 Titre de sejour.pdf
Unsorted/2026-10-26 Scanned document.pdf
```

- A section, then the correspondent (left out when there is none), then the
  document's date and title. A document nothing filed goes to `Unsorted`, dated the
  day it arrived. Names are plain ASCII; the same name twice gets ` (2)`.
- Beside each PDF, two hidden files: `.<name>.pdf.json` (its metadata, hash and
  history; the archive's source of truth) and `.<name>.pdf.txt` (its text). Keep
  them with the PDF when copying by hand.
- Editing the section, correspondent, date or title in the app moves the PDF.
- Don't add, rename or move files here by hand: `verify` reports a PDF without its
  sidecar. Upload instead.
- An archive from before 2026-10-26 (a folder per document, named `... [doc-...]`)
  is moved to this layout when the archive worker starts. By hand:
  `docker exec pdf-gateway-archive python /opt/pdf-gateway/pdf_archive.py --root /var/lib/pdf-archive migrate-layout`
  lists the moves; `--apply` makes them; then `reindex-db`.

## What the PDF carries

The clean copy is each page redrawn as a lossless 150-DPI image with an invisible
OCR text layer (searchable, copyable). Inside the PDF: Title, Author (the
correspondent), Subject (type · correspondent · date · number), Keywords (tags),
Producer "PDF Gateway"; no Creator, dates or versions; saved linearized (fast web
view). Paperless keeps such a file exactly as it is (it counts as born-digital, so
Paperless makes no re-encoded copy of its own) and gets the title, correspondent,
document type, date and tags.

## Using the app

- **Add files** (also the logo): drop PDFs here, or on any page. Each file shows a bar with
  five stages (checks, clean copy, AI filing, saved, Paperless), the time in the current
  stage, and an estimate. The estimate is the median of this gateway's last 50 runs of each
  step, scaled by page count, plus the files ahead. Uploads carry on while you browse.
- **Documents**: a list or grid with first-page pictures. The sidebar (or **Browse** on a
  phone) shows sections and correspondents with counts. Filter by type, year or tag. The
  search box matches titles, names and the documents' own text, ignoring case, accents and
  spacing ("m fitness" finds "MFITNESS").
- **Needs attention**: held, blocked or failed files, and documents nothing could file. The
  count is in the navigation.
- On a document's page, click a value to change it. **Undo** puts the previous value back
  (an edit like any other, so it also makes a new version).
- The pictures are made by the archive worker from the clean copy, never the original, into
  `pdf-archive/previews/` (beside `archive/`, not in the folders you browse). Missing ones
  are made again when the worker starts.

## Changing, re-running and deleting

- **Edit** (a document's page) changes any field: title, correspondent, type, date,
  number, amount, section, tags. The document is filed again (folder and file name
  follow), Paperless is updated, and a **new version of the PDF** with those values is
  written onto the validated clean copy, checked again by final validation, and only
  then replaces the current one (Paperless receives it as a new version). If that
  check fails, the current version stays.
- **Re-run the AI** asks the laptop again; its checked answer is applied the same way.
- **Delete everywhere** removes the archived copy, the original in quarantine, every
  stage's files and records, and moves the Paperless copy to Paperless's trash. Your
  own file on your computer is never touched. Each stage deletes its own files
  (`pdf.gateway.deleted.<stage>.v1`), so no part of the gateway gets more rights.
- The **same file uploaded again** is not a second document: the upload says "Already
  in your archive" and links to it.
- File and folder names are plain ASCII (accents dropped); titles keep accents with
  plain punctuation. Nothing in the PDF names the software (no Producer or Creator).

## Discord

`DISCORD_WEBHOOK_DOCUMENTS` (compose/.env) is the `documents` channel in Apprise
(`make up STACK=apprise` after changing it). The `notifier` service sends one card
when a file arrives and one when it is done (ready with its folder and Paperless
result, held, blocked or failed), each linking to the file's page.

## In Paperless

The gateway sets title, correspondent, document type, date, tags and the searchable
content (the original's own text, or the laptop's OCR for scans), and leaves one note
saying how the file was checked. It does not use Paperless's **storage path** (where
Paperless keeps its own copy on disk; the gateway's archive is already organised) or
**ASN** (archive serial number, for numbering paper documents in a binder).

## Signing in

People sign in with a name and a password. Until an admin can, the page shows a
setup form that needs the code the API writes to its log:

```bash
docker logs pdf-gateway-api 2>&1 | grep 'setup code'
```

Keep the name `admin` there: it is the account the bootstrap token made, and its
documents stay its own. Admins then manage everything under **System**: people
(name, password, role: admin, user or read-only; turn off; reset a password) and
apps (Homepage, the laptop's agent, scripts), which get API keys shown once and sent
as `Authorization: Bearer …`. `PDF_GATEWAY_BOOTSTRAP_ADMIN_TOKEN` stays the admin's
API key (listed as "bootstrap"); an API key no longer signs anyone in on the page.

## Backups and restores

restic (`restic/`) keeps:

- `/mnt/storage/data/documents/pdf-archive`: the archive, PDFs with their hidden
  sidecar and text beside them. This is the data that matters; it is readable
  without the gateway.
- `/mnt/docker-volumes/database-dumps/pdf-gateway.dump`: a `pg_dump` made before
  each snapshot (`restic/scripts/dump-databases.sh`), and the raw database folder.
- quarantine, reports and the broker's state under `/mnt/docker-volumes/pdf-gateway`.

ClamAV signatures, half-sent uploads and the provider socket are left out
(`restic/conf/excludes.txt`). The monthly restore test (`batlab-restic-restore-test.timer`, the 1st at 05:00) restores the dump and one
random archived PDF, and checks the PDF against its recorded SHA-256.

Restore the archive only:

```sh
sudo restic restore latest --target / --include /mnt/storage/data/documents/pdf-archive
docker exec pdf-gateway-archive python /opt/pdf-gateway/pdf_archive.py --root /var/lib/pdf-archive verify
```

Restore the database from the dump (stop the workers first):

```sh
make down STACK=pdf-gateway
docker compose ... up -d postgres     # or make up STACK=pdf-gateway SERVICE=postgres
docker exec -i pdf-gateway-db pg_restore --clean --if-exists -U pdf_gateway -d pdf_gateway \
  </mnt/docker-volumes/database-dumps/pdf-gateway.dump
make up STACK=pdf-gateway
```

Lost the database and every dump? The archive rebuilds the index:
`docker exec pdf-gateway-archive python /opt/pdf-gateway/pdf_archive.py --root /var/lib/pdf-archive reindex-db` (tested in M4).

## Health

- Homepage card: archived, checking, to review, held.
- `https://pdf.homelab.lan` → Status: database, broker, archive, OCR/AI.
- `docker exec pdf-gateway-archive python /opt/pdf-gateway/pdf_archive.py --root /var/lib/pdf-archive verify`: every PDF against its sidecar.

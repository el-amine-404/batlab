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
   it, uploads stop at `SANITIZING` and nothing is released.
4. A dedicated Paperless user for the adapter (it may add documents, tags and
   custom fields, and read tasks; it is not an administrator):

   ```sh
   docker exec paperless python3 manage.py shell -c '
   from django.contrib.auth.models import User, Permission
   from rest_framework.authtoken.models import Token
   u, _ = User.objects.get_or_create(username="pdf-gateway")
   u.set_unusable_password(); u.is_staff = u.is_superuser = False; u.save()
   codes = ["add_document", "view_document", "change_document", "view_tag", "add_tag",
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
build cache is not carried over). To keep lab2 cool on the first one, build one
image at a time: `make build-pdf-gateway`.

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

## Signing in

Sign in with `PDF_GATEWAY_BOOTSTRAP_ADMIN_TOKEN` (the first ADMIN's API token).
There is no user-management page yet: other users and tokens are rows in `users`
and `api_tokens` (SHA-256 only), as in step 6.

## Backups and restores

restic (`restic/`) keeps:

- `/mnt/storage/data/documents/pdf-archive`: the archive, PDFs with
  `metadata.json` and `content.txt`. This is the data that matters; it is readable
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

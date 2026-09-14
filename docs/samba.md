# Samba

Samba runs on the host, not in Docker. `templates/smb.conf` is the whole
configuration: LAN only, SMB3 with encryption, no guests.

| Share | Path | Who |
| --- | --- | --- |
| `media` | `/mnt/storage/data/media` | read: `potato` and `@media-readers`; write: `potato` |
| `photos` | `/mnt/storage/data/photos` (`library`, `inbox`, `private`) | `potato` only |
| `files` | `/mnt/storage/data/files` | `potato` only |

Install or update it:

```bash
testparm -s templates/smb.conf >/dev/null && \
  sudo install -m 644 templates/smb.conf /etc/samba/smb.conf && \
  sudo systemctl reload smbd
```

A user needs a Samba password once: `sudo smbpasswd -a potato`. To give
someone read access to `media` only, create a system user without a login shell,
add it to `media-readers`, and set its Samba password the same way.

Connect from Windows with `\\192.168.1.3\photos` (or `media`, `files`) in
Explorer, and from Linux with `smb://192.168.1.3/photos` in the file manager.

## Filing photos from another computer

Move inside the `photos` share only: dragging from `inbox` to `library` there is
a rename on the server and takes a moment, while a move between two shares, or
through a local folder, copies every byte through the network.

- When Explorer reports that a file already exists, choose **Skip** and compare
  the two afterwards; never **Replace**. The names are capture times, so a
  clash is a duplicate or a different shot from the same second.
- Move a photo together with its `.xmp` sidecar and, for Live Photos, the
  `.mov` of the same name. Moving whole folders keeps them together.
- To split a place into one folder per visit, run
  `photos/scripts/group-by-date.py` on it (see `photos/README.md`). The fastest
  and safest way from another computer is over SSH, on the server's own disk:
  `ssh my-homelab 'batlab/photos/scripts/group-by-date.py "/mnt/storage/data/photos/library/<place>"'`.
  It also runs directly on the share (a mapped drive or mounted folder) with
  Python 3.7 or later installed.
- Once Immich indexes `library`, avoid renaming or moving what is already
  filed there: Immich sees a new asset and loses its faces and albums.

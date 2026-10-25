# Lab2 capacity plan

Measurements on 2026-10-25 before gateway deployment:

| Resource | Observed |
|---|---:|
| CPU | Intel i5-8350U, 8 logical CPUs |
| RAM | 31 GiB total, 17 GiB available |
| Swap | 12 GiB total, effectively idle |
| Root filesystem | 221 GiB, 84% used, 36 GiB available |
| Data filesystem | 3.6 TiB, 45% used, 1.9 TiB available |
| Cgroups | v2 |
| Docker | 29.9.2 |

Root usage is already high, so document bytes, databases, queues, scanner data,
and scratch belong on the data disk or `/mnt/docker-volumes`, never Docker's
default writable layer. Uploads pause when either the target filesystem has less
than the greater of 10% or 10 GiB free, or inode headroom is below 10%.

Initial limits are one active scan, CDR, OCR, and AI task each. Heavy workers use
low CPU/IO priority, bounded memory/PIDs/tmpfs/logs, and deadlines. ClamAV alone
requires roughly 3–4 GiB and can temporarily double engine memory during database
reload according to its official Docker guidance; its measured peak on Lab2 must
precede final limits.

Acceptance benchmarks will record idle and stage-specific CPU, RSS, PSI, disk IO,
queue delay, and latency for DNS, Paperless, and Jellyfin. These are measurements,
not inferred guarantees. See [ClamAV Docker guidance](https://docs.clamav.net/manual/Installing/Docker.html)
and [RabbitMQ queue durability](https://www.rabbitmq.com/docs/queues).


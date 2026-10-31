"""stanzarr-worker: take AI-sync jobs from a Stanzarr server, one at a time.

  stanzarr-worker --server http://stanzarr.example --key <worker key>

Checks in every 20 s, so the server knows a GPU is available. Without a GPU it
still checks in (and says so) but takes no jobs, unless --cpu: that works,
but takes many minutes per song.
"""

import argparse
import json
import logging
import shutil
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request

from . import __version__, gpu, pipeline

log = logging.getLogger("stanzarr-worker")


def _json_serial(obj):
    if hasattr(obj, "item"):
        return obj.item()
    return str(obj)


class Server:
    def __init__(self, url: str, key: str, name: str):
        self.url, self.key, self.name = url.rstrip("/"), key, name

    def call(self, method: str, path: str, body=None, raw: bytes | None = None, timeout: int = 60):
        headers = {"X-Stanzarr-Key": self.key, "X-Stanzarr-Worker": self.name, "User-Agent": f"stanzarr-worker/{__version__}"}
        data = raw
        if body is not None:
            data = json.dumps(body, default=_json_serial).encode()
            headers["Content-Type"] = "application/json"
        elif raw is not None:
            headers["Content-Type"] = "application/octet-stream"
        request = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status == 204:
                return None
            payload = response.read()
            return json.loads(payload) if payload else None

    def download(self, path: str, target: str) -> None:
        request = urllib.request.Request(self.url + path, headers={"X-Stanzarr-Key": self.key, "X-Stanzarr-Worker": self.name})
        with urllib.request.urlopen(request, timeout=600) as response, open(target, "wb") as out:
            shutil.copyfileobj(response, out)


def host_address() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        hostname = socket.gethostname()
        clean_host = hostname.replace("-", ".")
        if clean_host == ip or hostname.lower() in ("localhost", "localhost.localdomain", ip):
            return ip
        return f"{ip} ({hostname})"
    except Exception:
        return socket.gethostname()


MODEL_NAME = "Demucs htdemucs + faster-whisper large-v3-turbo + MMS"


def heartbeat(server: Server, name: str, vram: int, host: str, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            server.call("POST", "/api/v1/worker/heartbeat", {
                "gpu": name,
                "vram": vram,
                "version": __version__,
                "host": host,
                "model": MODEL_NAME,
                "capabilities": "sync,generate",
            })
        except Exception as error:
            log.warning("server unreachable: %s", error)
        stop.wait(20)


def work(server: Server, job: dict, device: str) -> None:
    job_id = job["job"]
    song = job["song"]
    kind = job.get("kind", "ai_sync")
    lines_input = job.get("lines") or []
    log.info("job %s [%s]: %s – %s (%d lines)", job_id, kind, song["artist"], song["title"], len(lines_input))
    stage = lambda text: _quiet(lambda: server.call("POST", f"/api/v1/worker/jobs/{job_id}/stage", {"stage": text}))
    try:
        import gc
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        gc.collect()
    except Exception:
        pass
    with tempfile.TemporaryDirectory(prefix="stanzarr-worker-") as workdir:
        try:
            stage("downloading the song")
            ext = job.get("audio_name", "song.flac").rsplit(".", 1)[-1][:5]
            audio = f"{workdir}/song.{ext}"
            server.download(f"/api/v1/worker/jobs/{job_id}/audio", audio)
            if kind == "ai_generate" or not lines_input:
                lines, voice = pipeline.run_generate(audio, workdir, stage, device, song, lines_input)
                method = "HTDemucs + Whisper large-v3-turbo transcribe & align"
            else:
                lines, voice = pipeline.run(audio, lines_input, workdir, stage, device, song.get("language", ""))
                method = pipeline.NAME
            stage("uploading voice track")
            with open(voice, "rb") as handle:
                server.call("PUT", f"/api/v1/worker/jobs/{job_id}/voice", raw=handle.read(), timeout=600)
            result = server.call("POST", f"/api/v1/worker/jobs/{job_id}/result", {"lines": lines, "method": method})
            log.info("job %s done: %s", job_id, (result or {}).get("result"))
        except Exception as error:
            log.exception("job %s failed", job_id)
            _quiet(lambda: server.call("POST", f"/api/v1/worker/jobs/{job_id}/fail", {"error": str(error)[:500]}))
        finally:
            try:
                import gc
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                gc.collect()
            except Exception:
                pass


def _quiet(action) -> None:
    try:
        action()
    except Exception as error:
        log.warning("could not tell the server: %s", error)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--server", required=True, help="the Stanzarr address, e.g. http://stanzarr.homelab.lan")
    parser.add_argument("--key", required=True, help="the workers' key (stanzarr, Settings, Workers)")
    parser.add_argument("--name", default=socket.gethostname(), help="how this worker shows in Stanzarr")
    parser.add_argument("--cpu", action="store_true", help="take jobs without a GPU (slow)")
    parser.add_argument("--once", action="store_true", help="do one job, if there is one, then stop")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    name, vram = gpu.detect()
    if name:
        log.info("GPU: %s (%d MB)", name, vram)
    else:
        log.warning("no NVIDIA GPU found%s", ": taking jobs on the CPU (slow)" if args.cpu else ": checking in, taking no jobs")
    host = host_address()
    server = Server(args.server, args.key, args.name)
    try:
        server.call("POST", "/api/v1/worker/heartbeat", {
            "gpu": name,
            "vram": vram,
            "version": __version__,
            "host": host,
            "model": MODEL_NAME,
            "capabilities": "sync,generate",
        })
    except urllib.error.HTTPError as error:
        raise SystemExit(f"the server refused this worker ({error.code}): check the key") from error
    except urllib.error.URLError as error:
        raise SystemExit(f"cannot reach {args.server}: {error.reason}") from error
    stop = threading.Event()
    threading.Thread(target=heartbeat, args=(server, name, vram, host, stop), daemon=True).start()
    device = "cuda" if name else "cpu"
    try:
        while True:
            job = None
            if name or args.cpu:
                try:
                    job = server.call("POST", "/api/v1/worker/next")
                except Exception as error:
                    log.warning("could not ask for work: %s", error)
            if job:
                work(server, job, device)
            elif args.once:
                break
            if args.once and job:
                break
            if not job:
                time.sleep(10)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()


if __name__ == "__main__":
    main()

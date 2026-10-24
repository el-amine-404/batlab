# Incident Postmortem: PDF-OCR Memory Exhaustion & Holding Queue Retry Storm

**Date:** 2026-10-24  
**Severity:** High (OOM Kills, Watchdog Alerts, Lab2 Thermal Spike to 95.1°C, Healthchecks.io Failure)  
**Affected Systems:** `pdf-ocr`, `pdf-orchestrator`, Lab2 Host System Health (`watchdog`, `netdata`, `healthchecks.io`)  
**Status:** Resolved & Mitigated  

---

## 1. Summary

Between 22:36 UTC and 23:05 UTC, Lab2 experienced repeated Out-Of-Memory (OOM) kernel kills on the `pdf-ocr` container, triggering automated alerts in Discord (`#alerts`) from **Watchdog on Lab2**, **Healthchecks.io**, **Netdata**, and **PDF Gateway**.

The server's CPU temperature spiked to **95.1°C** with `pdf-ocr` consuming 60%+ CPU concurrently with background camera inference (Frigate).

The incident was halted manually by terminating `pdf-ocr` and `pdf-orchestrator`. Subsequent investigation identified two distinct compounding bugs:
1. An unconstrained rasterization decompression bomb caused by a 1720 DPI asset in a PDF invoice.
2. A self-inflicted retry storm in `holding_watcher.py` that repeatedly retried failed documents every 10 seconds without a dead-letter quarantine.

---

## 2. Root Cause Analysis (RCA)

### Bug 1: The 1720 DPI Rasterization Bomb
When `2026-06-10_facture_beinsport.pdf` was processed, `pdf-ocr` was configured with `--redo-ocr` to attempt OCR on embedded images.
- An embedded logo/barcode in the PDF metadata declared an internal resolution of **1720.15 DPI**.
- Ghostscript, running inside OCRmyPDF without a maximum DPI or megapixel cap, attempted to render page 1 at native declared resolution (`-r1720.153538x1720.153538`).
- This produced an uncompressed bitmap containing **285,900,396 pixels** (~286 Megapixels; Pillow logged a `DecompressionBombWarning`).
- Tesseract and Ghostscript required >3 GB of RAM to allocate the bitmap buffers, immediately exceeding `pdf-ocr`'s cgroup memory limit (`mem_limit: 1gb`, then `2gb`).
- The Linux kernel OOM-killer terminated the processes (`PID 1092018`, `PID 1130113`, `PID 1133769`).

### Bug 2: Self-Inflicted Retry Storm (Holding Watcher Loop)
In `compose/pdf-gateway/services/pdf-orchestrator/src/holding_watcher.py`:
```python
except Exception as e:
    logger.error(f"Holding watcher error on {filename}: {e}")
    await notifier.notify_error(filename, str(e))
    # Move back to holding
    if os.path.exists(proc_path):
        shutil.move(proc_path, src_path)
```
When a document failed (via OOM kill or HTTP timeout), the error handler moved the problematic file **back to `/data/holding`**.
Because the watcher poll interval was 10 seconds, the orchestrator immediately picked up the same file, re-triggered OCR, re-triggered the OOM crash, re-alerted, and looped indefinitely. This continuous CPU/disk thrashing drove Lab2's CPU temperature to 95.1°C.

---

## 3. Policy Decision: Handling Born-Digital Vector PDFs

### The Core Principle
**A born-digital vector PDF must NEVER be re-rasterized.**

Re-rasterizing a vector PDF (`--force-ocr` or `--redo-ocr` on digital pages) is an anti-pattern:
1. **Fidelity Degradation:** Re-rasterizing replaces clean scalable vector glyphs with lossy raster images and imperfect OCR guesses.
2. **Resource Waste:** Processing a 20-page presentation via full rasterization consumes minutes of CPU time and gigabytes of RAM, compared to **0.05 seconds** for native extraction.
3. **Decompression Vulnerability:** Born-digital PDFs frequently contain tiny high-DPI assets (logos, stamps, barcodes) that trigger decompression bombs.

### The Hybrid Solution for Embedded Diagrams
If a digital document contains embedded raster images (e.g., a diagram in school presentation slides):
- **Primary Text Layer:** Keep the native vector text layer intact without running full-page OCR.
- **Visual Intelligence:** Use the lightweight Vision model (`Qwen-2.5-VL`) or on-demand inspection to analyze diagrams and append the visual explanation to the document's **Markdown summary notes and tags**.
- **User Experience:** The user gets full, instantaneous searchability of the vector text without risking server crashes or high temperatures.

---

## 4. Implemented Safeguards

### 1. Circuit Breaker & Dead-Letter Quarantine
Modified `holding_watcher.py`:
- On any processing failure or exception, the document is moved to **`/data/holding/.quarantine/`** (or deleted if already safely backed up).
- It is **NEVER** moved back to `/data/holding/`.
- A single Apprise alert is dispatched detailing the quarantine reason.
- The pipeline immediately halts retries on that document, preventing any retry storm or Denial-of-Service condition.

### 2. Born-Digital Vector Bypass
In `pdf-ocr/src/engine.py`:
- If `pdffonts` detects embedded fonts and `pdftotext` extracts clean, dictionary-valid text, `is_native_vector` is set to `True`.
- The document is copied directly without invoking OCRmyPDF or Ghostscript rasterization.

### 3. Decompression Bomb & DPI Guards in OCR Engine
For documents that truly require OCR (scanned PDFs, image-only documents):
- `--max-image-mpixels 10`: Enforces a strict 10-megapixel ceiling, immediately blocking any decompression bomb.
- `--tesseract-downsample-large-images`: Forces Tesseract to downsample images larger than standard threshold.
- `--tesseract-downsample-above 2500`: Downsamples any asset above 2500 pixels in either dimension.
- `--image-dpi 300`: Normalizes rasterization to standard 300 DPI archiving quality.

### 4. Hard Host CPU & Resource Bounds
In `compose/pdf-gateway/docker-compose.yml`:
- `cpus: 1.0`: Hard cap of 1 core on `pdf-ocr` so OCR processing can never starve the host or trigger CPU thermal alerts.
- `mem_limit: 2gb` with proper downsampling guarantees that peak memory usage remains well below 500 MB.
- Single job concurrency (`OCR_JOBS=1`) in production.

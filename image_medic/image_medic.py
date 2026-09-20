#!/usr/bin/env python3
import os
import sys
import time
import signal
import argparse
import shutil
import hashlib
import sqlite3
import subprocess
from datetime import datetime
import math
from PIL import Image, ImageChops, ImageStat

interrupted = False

def signal_handler(signum, frame):
    global interrupted
    if not interrupted:
        sys.stdout.write("\n[SYSTEM] Interruption signal received. Halting new tasks and syncing state safely...\n")
        sys.stdout.flush()
        interrupted = True

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def format_time_eta(seconds_remaining):
    if seconds_remaining < 0:
        return "Calculating..."
    end_time = time.time() + seconds_remaining
    end_dt = datetime.fromtimestamp(end_time).astimezone()
    return end_dt.strftime("%Y-%m-%d %H:%M:%S %Z")

def print_progress(current, total, start_time, message=""):
    if total == 0: return
    percent = (current / total) * 100
    elapsed = time.time() - start_time
    
    if current > 0:
        eta_seconds = (elapsed / current) * (total - current)
        eta_str = format_time_eta(eta_seconds)
    else:
        eta_str = "Calculating..."

    bar_length = 30
    filled = int((current / total) * bar_length)
    bar = "=" * filled + "-" * (bar_length - filled)
    
    sys.stdout.write(f"\r[{bar}] {percent:5.1f}% | ETA: {eta_str} | {message:<40}")
    sys.stdout.flush()

class StateManager:
    def __init__(self, local_db_path, mount_db_path):
        self.local_db_path = local_db_path
        self.mount_db_path = mount_db_path
        self.conn = None
        self._sync_from_mount()
        self._init_db()

    def _sync_from_mount(self):
        if self.mount_db_path and os.path.exists(self.mount_db_path):
            sys.stdout.write(f"[INFO] Pulling state from mount...\n")
            os.makedirs(os.path.dirname(self.local_db_path), exist_ok=True)
            shutil.copy2(self.mount_db_path, self.local_db_path)

    def _init_db(self):
        os.makedirs(os.path.dirname(self.local_db_path), exist_ok=True)
        self.conn = sqlite3.connect(self.local_db_path, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS files (
                filepath TEXT PRIMARY KEY,
                mtime REAL,
                hash TEXT,
                status TEXT
            )
        ''')

    def get_file_state(self, filepath):
        cursor = self.conn.execute("SELECT mtime, hash, status FROM files WHERE filepath = ?", (filepath,))
        return cursor.fetchone()

    def update_file_state(self, filepath, mtime, file_hash, status):
        self.conn.execute(
            "INSERT OR REPLACE INTO files (filepath, mtime, hash, status) VALUES (?, ?, ?, ?)",
            (filepath, mtime, file_hash, status)
        )

    def close_and_sync(self):
        if self.conn:
            self.conn.close()
        if self.mount_db_path:
            sys.stdout.write(f"\n[INFO] Pushing state to mount...\n")
            try:
                os.makedirs(os.path.dirname(self.mount_db_path), exist_ok=True)
                shutil.copy2(self.local_db_path, self.mount_db_path)
            except OSError as e:
                if e.errno == 30:
                    sys.stdout.write("[INFO] Mount is Read-Only. State was safely saved to your local laptop instead.\n")
                else:
                    sys.stdout.write(f"[ERROR] Failed to push state to mount: {e}\n")
            except Exception as e:
                sys.stdout.write(f"[ERROR] Failed to push state to mount: {e}\n")

def compute_hash(filepath):
    hasher = hashlib.blake2b()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            hasher.update(chunk)
    return hasher.hexdigest()

def calculate_visual_difference(img1_path, img2_path):
    try:
        with Image.open(img1_path) as i1, Image.open(img2_path) as i2:
            i2_resized = i2.resize(i1.size, Image.Resampling.LANCZOS).convert('RGB')
            i1_rgb = i1.convert('RGB')
            diff = ImageChops.difference(i1_rgb, i2_resized)
            stat = ImageStat.Stat(diff)
            rms = math.sqrt(sum([s**2 for s in stat.mean]) / 3.0)
            return rms
    except Exception:
        return 999.0

def deep_validate_image(filepath):
    try:
        with open(filepath, 'rb') as f:
            if f.read(2) != b'\xff\xd8':
                return "FAKE_NON_JPEG"
    except Exception: return "IO_ERROR"

    try:
        with Image.open(filepath) as img:
            img.verify()
    except Exception: return "CORRUPTED_STRUCTURAL"

    try:
        result = subprocess.run(['identify', '-verbose', filepath], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=30)
        if "Corrupt JPEG data" in result.stderr or "Premature end" in result.stderr:
            return "CORRUPTED_VISUAL"
    except Exception: pass
    return "HEALTHY"

def repair_image(filepath, library_path, review_path, tmp_repair_path):
    filename = os.path.basename(filepath)
    name, ext = os.path.splitext(filename)
    success_methods = []
    
    temp_thumb = os.path.join(tmp_repair_path, f"{name}_METHOD_THUMBNAIL{ext}")
    has_thumb = False
    try:
        subprocess.run(f"exiftool -b -ThumbnailImage '{filepath}' > '{temp_thumb}'", shell=True, stderr=subprocess.DEVNULL)
        if os.path.exists(temp_thumb) and os.path.getsize(temp_thumb) > 1000:
            subprocess.run(['exiftool', '-TagsFromFile', filepath, '-all:all', '-ThumbnailImage=', '-Orientation=', '-overwrite_original', temp_thumb], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            has_thumb = True
    except Exception: pass

    temp_im = os.path.join(tmp_repair_path, f"{name}_METHOD_IMAGEMAGICK{ext}")
    has_im = False
    try:
        subprocess.run(['convert', filepath, '-auto-orient', temp_im], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=60)
        if os.path.exists(temp_im) and os.path.getsize(temp_im) > 1000:
            subprocess.run(['exiftool', '-TagsFromFile', filepath, '-all:all', '-Orientation=', '-overwrite_original', temp_im], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if deep_validate_image(temp_im) == "HEALTHY":
                has_im = True
            else:
                os.remove(temp_im)
    except Exception: pass

    # AI-Free Auto-Selection via Ground Truth Comparison
    if has_thumb and has_im:
        rms = calculate_visual_difference(temp_thumb, temp_im)
        if rms > 25.0:
            # IM has massive glitch artifacts (chaos), discard it
            os.remove(temp_im)
            has_im = False
        else:
            # IM is healthy high-res and matches thumbnail perfectly
            os.remove(temp_thumb)
            has_thumb = False

    rel_path = os.path.relpath(filepath, library_path)
    
    if has_thumb:
        dest = os.path.join(review_path, os.path.dirname(rel_path), f"{name}_METHOD_THUMBNAIL{ext}")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(temp_thumb, dest)
        success_methods.append(("Thumbnail Extraction (Visually Perfect)", dest))
        
    if has_im:
        dest = os.path.join(review_path, os.path.dirname(rel_path), f"{name}_METHOD_IMAGEMAGICK{ext}")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(temp_im, dest)
        success_methods.append(("ImageMagick Salvage (Full Res)", dest))

    return success_methods

def apply_changes(library_path, workspace):
    review_path = os.path.join(workspace, "review")
    backup_path = os.path.join(workspace, "backup")
    if not os.path.exists(review_path):
        sys.stdout.write("[INFO] No files to apply in review folder.\n")
        return
        
    for root, _, files in os.walk(review_path):
        for f in files:
            # Reconstruct original filename by stripping method tags
            orig_filename = f.replace("_METHOD_THUMBNAIL", "").replace("_METHOD_IMAGEMAGICK", "")
            
            rev_file = os.path.join(root, f)
            rel_dir = os.path.relpath(root, review_path)
            rel_path = orig_filename if rel_dir == "." else os.path.join(rel_dir, orig_filename)
                
            lib_file = os.path.join(library_path, rel_path)
            bak_file = os.path.join(backup_path, rel_path)
            
            os.makedirs(os.path.dirname(bak_file), exist_ok=True)
            sys.stdout.write(f"Applying: {rel_path} (Using {f})\n")
            
            if os.path.exists(lib_file): shutil.move(lib_file, bak_file)
            shutil.move(rev_file, lib_file)
            
    sys.stdout.write("[INFO] Apply complete. Test your library, then run 'make docker-commit' or 'make docker-rollback'.\n")

def rollback_changes(library_path, workspace):
    backup_path = os.path.join(workspace, "backup")
    review_path = os.path.join(workspace, "review")
    if not os.path.exists(backup_path):
        sys.stdout.write("[INFO] No backups found to rollback.\n")
        return
        
    for root, _, files in os.walk(backup_path):
        for f in files:
            bak_file = os.path.join(root, f)
            rel_path = os.path.relpath(bak_file, backup_path)
            lib_file = os.path.join(library_path, rel_path)
            rev_file = os.path.join(review_path, rel_path) # We don't restore the method tag, just standard name
            
            os.makedirs(os.path.dirname(rev_file), exist_ok=True)
            sys.stdout.write(f"Rolling back: {rel_path}\n")
            
            if os.path.exists(lib_file): shutil.move(lib_file, rev_file)
            shutil.move(bak_file, lib_file)
            
    sys.stdout.write("[INFO] Rollback complete. Original files restored.\n")

def commit_changes(workspace):
    backup_path = os.path.join(workspace, "backup")
    if os.path.exists(backup_path):
        shutil.rmtree(backup_path)
        sys.stdout.write("[INFO] Commit complete. Old backups securely deleted.\n")
    else:
        sys.stdout.write("[INFO] No backups found to commit.\n")

def main():
    global interrupted
    parser = argparse.ArgumentParser(description="Image Medic - Validate and Repair")
    parser.add_argument("--mode", choices=["scan", "apply", "rollback", "commit"], default="scan")
    parser.add_argument("--library", required=True, help="Path to the library folder to scan")
    parser.add_argument("--workspace", default=os.path.expanduser("~/.local/share/image_medic"), help="Local workspace")
    parser.add_argument("--host-library", default="", help="Host path for UX reporting")
    parser.add_argument("--host-workspace", default="", help="Host workspace for UX reporting")
    args = parser.parse_args()

    library_path = os.path.abspath(args.library)
    
    if args.mode == "apply": return apply_changes(library_path, args.workspace)
    if args.mode == "rollback": return rollback_changes(library_path, args.workspace)
    if args.mode == "commit": return commit_changes(args.workspace)

    # Mode: SCAN
    local_db = os.path.join(args.workspace, "library_state.db")
    mount_db = os.path.join(library_path, ".image_medic_backup", "library_state.db")
    review_path = os.path.join(args.workspace, "review")
    tmp_repair_path = os.path.join(args.workspace, ".tmp_repair")

    if not os.path.exists(library_path): sys.exit(1)
    os.makedirs(review_path, exist_ok=True)
    os.makedirs(tmp_repair_path, exist_ok=True)

    sys.stdout.write(f"[INFO] Initializing Scan Mode\n")
    state = StateManager(local_db, mount_db)

    all_files = []
    for root, _, files in os.walk(library_path):
        if ".image_medic_backup" in root: continue
        for f in files:
            if f.lower().endswith(('.jpg', '.jpeg')):
                all_files.append(os.path.join(root, f))

    total_files = len(all_files)
    sys.stdout.write(f"[INFO] Found {total_files} JPEG files.\n")

    start_time = time.time()
    stats = {"HEALTHY": 0, "CORRUPTED_FOUND": 0, "FAKE_NON_JPEG": 0, "SKIPPED": 0, "REPAIRED_TO_REVIEW": 0, "REPAIR_FAILED": 0}
    repaired_files = []

    for i, filepath in enumerate(all_files):
        if interrupted: break
        filename = os.path.basename(filepath)
        print_progress(i, total_files, start_time, message=f"Processing {filename[:15]}...")
        
        try:
            mtime = os.path.getmtime(filepath)
            record = state.get_file_state(filepath)
            if record:
                rec_mtime, _, _ = record
                if mtime == rec_mtime:
                    stats["SKIPPED"] += 1
                    continue

            file_hash = compute_hash(filepath)
            status = deep_validate_image(filepath)
            
            if status.startswith("CORRUPTED_"):
                stats["CORRUPTED_FOUND"] += 1
                print_progress(i, total_files, start_time, message=f"Repairing {filename[:15]}...")
                methods_used = repair_image(filepath, library_path, review_path, tmp_repair_path)
                if methods_used:
                    stats["REPAIRED_TO_REVIEW"] += 1
                    status = "PENDING_REVIEW" 
                    for method_name, rev_file in methods_used:
                        repaired_files.append((filepath, rev_file, method_name))
                else:
                    stats["REPAIR_FAILED"] += 1
            elif status == "HEALTHY":
                stats["HEALTHY"] += 1
            elif status == "FAKE_NON_JPEG":
                stats["FAKE_NON_JPEG"] += 1

            state.update_file_state(filepath, mtime, file_hash, status)

        except Exception as e:
            sys.stdout.write(f"\n[ERROR] Failed to process {filepath}: {e}\n")

    print_progress(total_files, total_files, start_time, message="Complete.")
    sys.stdout.write("\n\n[SUMMARY]\n")
    for k, v in stats.items(): sys.stdout.write(f"  {k}: {v}\n")

    if repaired_files:
        host_lib = args.host_library if args.host_library else library_path
        host_work = args.host_workspace if args.host_workspace else args.workspace
        sys.stdout.write("\n" + "="*80 + "\n")
        sys.stdout.write("[ACTION REQUIRED] MANUAL REVIEW READY\n")
        sys.stdout.write("Review the options in your workspace. Keep the best version of each file and delete the rest.\n")
        sys.stdout.write("-" * 80 + "\n")
        
        current_orig = ""
        for orig, rev, method in repaired_files:
            if orig != current_orig:
                sys.stdout.write(f"\nORIGINAL: {os.path.join(host_lib, os.path.relpath(orig, library_path))}\n")
                current_orig = orig
            
            rev_rel = os.path.relpath(rev, review_path)
            sys.stdout.write(f"  -> {method}\n")
            sys.stdout.write(f"     File: {os.path.join(host_work, 'review', rev_rel)}\n")
            
        sys.stdout.write("\n" + "-" * 80 + "\n")
        sys.stdout.write(f"If you approve, run: make docker-apply LIB={host_lib}\n")
        sys.stdout.write("=" * 80 + "\n")

    state.close_and_sync()
    sys.stdout.write("[INFO] Shutdown complete.\n")

if __name__ == "__main__":
    main()

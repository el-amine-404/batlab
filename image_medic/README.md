# Image Medic

A robust, cross-platform, interrupt-resilient tool to scan, validate, and repair corrupted JPEG images.

## Features
- **Cross-Platform:** Works on any Linux distribution. Uses standard Python libraries.
- **Resilient & Atomic:** Handles unexpected interruptions (Ctrl+C, power loss, network drops). Uses atomic file operations to guarantee zero data loss. Mid-treatment files are automatically reverted.
- **JSON State Management:** Progress is cached in a `.json` file. If interrupted, the script resumes exactly where it left off.
- **Deep Validation:** Detects visually corrupted JPEGs (e.g., bit-shifted MCU blocks resulting in green bands) even when the file structure is technically valid.
- **Metadata Preservation:** Extracts and re-injects all original EXIF metadata (including Orientation) into the repaired files.
- **Clean UX:** Simple, professional, monochrome text output with precise time estimates (including timezones).

## Requirements
- Python 3.6+
- `Pillow` (`pip install Pillow`)
- `exiftool` (Must be installed on the system, e.g., `sudo apt install libimage-exiftool-perl` or `sudo dnf install perl-Image-ExifTool`)
- `ImageMagick` (Optional, for fallback GPU/OpenCL salvage attempts)

## Usage

Run the script by pointing it to your target library folder. The tool uses relative or absolute paths, avoiding any hardcoded environment specifics.

```bash
python3 image_medic.py --library /path/to/your/photos
```

### Options
- `--library`: (Required) The path to the folder containing your images.
- `--state-file`: (Optional) Path to the JSON state file. Defaults to `library_state.json` in the current directory.
- `--quarantine`: (Optional) Path to move corrupted original files. Defaults to `./quarantine`.

## Architecture & Safety
The tool operates in a strictly safe pipeline:
1. **Validation:** Images are verified.
2. **Sandbox Repair:** If corruption is found, the repair occurs in a `.tmp_repair/` directory.
3. **Metadata Injection:** EXIF data is injected into the temporary repaired file.
4. **Atomic Swap:** Only when validation of the new file passes 100%, the original file is moved to the quarantine folder, and the new file replaces the original. If interrupted during this process, the original remains untouched.

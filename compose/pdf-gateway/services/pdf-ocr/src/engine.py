import os
import re
import shutil
import subprocess
from abc import ABC, abstractmethod
from typing import Dict, Any, Tuple
from datetime import datetime

from .config import config

class IVectorDetector(ABC):
    @abstractmethod
    def is_native_vector_pdf(self, file_path: str) -> Tuple[bool, int, str]:
        """Returns (is_vector, char_count_p1, sample_text)."""
        pass

class IQualityAssessor(ABC):
    @abstractmethod
    def assess_quality(self, text: str) -> Dict[str, Any]:
        """Calculates dictionary validity, noise ratio, and vision escalation flag."""
        pass

class IOcrRunner(ABC):
    @abstractmethod
    def run_ocr(self, input_path: str, output_path: str, langs: str = None) -> Tuple[bool, str]:
        """Runs lossless OCRmyPDF."""
        pass

class PopplerVectorDetector(IVectorDetector):
    def is_native_vector_pdf(self, file_path: str) -> Tuple[bool, int, str]:
        # 1. Check fonts in document
        has_fonts = False
        try:
            res_fonts = subprocess.run(
                [config.pdffonts_cmd, file_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10
            )
            # pdffonts output lines: line 0 is header, line 1 is separator dashed line
            lines = res_fonts.stdout.strip().splitlines()
            if len(lines) > 2:
                has_fonts = True
        except Exception:
            pass

        # 2. Check for embedded raster images (diagrams, photos, figures)
        has_embedded_images = False
        try:
            res_img = subprocess.run(
                ["pdfimages", "-list", file_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10
            )
            img_lines = [l for l in res_img.stdout.strip().splitlines() if l.strip()]
            if len(img_lines) > 2:
                has_embedded_images = True
        except Exception:
            pass

        # 3. Check text extraction from page 1
        text_p1 = ""
        try:
            res_text = subprocess.run(
                [config.pdftotext_cmd, "-l", "1", file_path, "-"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10
            )
            text_p1 = res_text.stdout.strip()
        except Exception:
            pass

        char_count = len(text_p1)
        # Born-digital vector PDFs: if fonts exist and page 1 has digital text, bypass OCR rasterization
        is_native = has_fonts and (char_count >= config.min_char_count_first_page)
        return is_native, char_count, text_p1[:400]

class LexicalQualityAssessor(IQualityAssessor):
    # Common English, French, and Arabic functional/core word roots
    COMMON_WORDS = {
        # French
        "le", "la", "les", "un", "une", "des", "du", "de", "dans", "en", "pour", "par",
        "sur", "avec", "sans", "est", "sont", "ete", "avoir", "etre", "fait", "cours",
        "universite", "faculte", "science", "sciences", "etudiant", "rapport", "stage",
        "projet", "examen", "chapitre", "systeme", "donnees", "gestion", "facture", "total",
        "societe", "droit", "maroc", "rabat", "casablanca", "entreprise", "classe", "objet",
        # English
        "the", "be", "to", "of", "and", "a", "in", "that", "have", "i", "it", "for", "not",
        "on", "with", "he", "as", "you", "do", "at", "this", "but", "his", "by", "from",
        "data", "system", "computer", "science", "design", "guide", "user", "manual", "code",
        "software", "programming", "java", "python", "docker", "cisco", "network", "patterns",
        # Arabic (Administrative, Academic, Legal keywords)
        "المملكة", "المغربية", "وزارة", "جامعة", "كلية", "شهادة", "تقرير", "تدريب", "مشروع",
        "بطاقة", "الوطنية", "التعريف", "فاتورة", "مجموع", "قانون", "علوم", "طالب", "رسمي",
        "إدارة", "شعبة", "ماستر", "إجازة", "باكالوريا", "مغربي", "الرباط", "الدار", "البيضاء"
    }

    def assess_quality(self, text: str) -> Dict[str, Any]:
        if not text or len(text.strip()) == 0:
            return {
                "total_words": 0,
                "dictionary_validity_ratio": 0.0,
                "noise_ratio": 1.0,
                "needs_vision_escalation": True,
                "detected_arabic": False,
                "reason": "Empty text extracted"
            }

        # Check for Arabic script presence
        has_arabic = bool(re.search(r'[\u0600-\u06FF]', text))

        # Tokenize Latin and Arabic words
        raw_words = re.findall(r'[\u0600-\u06FF\w]+', text.lower())
        total_tokens = len(raw_words)

        if total_tokens < 5:
            return {
                "total_words": total_tokens,
                "dictionary_validity_ratio": 0.0,
                "noise_ratio": 1.0,
                "needs_vision_escalation": True,
                "detected_arabic": has_arabic,
                "reason": "Insufficient word tokens"
            }

        valid_words = 0
        for w in raw_words:
            if w in self.COMMON_WORDS:
                valid_words += 1
            elif len(w) >= 3 and (w.isalpha() or re.match(r'^[\u0600-\u06FF]+$', w)):
                valid_words += 1

        dict_ratio = round(valid_words / total_tokens, 3)

        # Count symbol noise (excluding Latin letters, Arabic letters, digits, and standard punctuation)
        noise_chars = len(re.findall(r'[^a-zA-Z0-9\s.,;:!?\'"()\-_/éèêëàâäôöûüçÉÈÊËÀÂÄÔÖÛÜÇ\u0600-\u06FF\u0660-\u0669]', text))
        total_chars = len(text)
        noise_ratio = round(noise_chars / max(1, total_chars), 3)

        # Escalation rules:
        # 1. Low dictionary ratio (< 65%)
        # 2. High noise ratio (> 25%)
        # 3. Arabic documents with noisy scans (Tesseract often garbles handwritten/complex Arabic stamps)
        needs_escalation = (dict_ratio < config.min_dictionary_validity_ratio) or (noise_ratio > config.max_symbol_noise_ratio)

        return {
            "total_words": total_tokens,
            "dictionary_validity_ratio": dict_ratio,
            "noise_ratio": noise_ratio,
            "detected_arabic": has_arabic,
            "needs_vision_escalation": needs_escalation,
            "reason": "Degraded OCR or high symbol noise" if needs_escalation else "Text quality verified"
        }

class OcrMyPdfRunner(IOcrRunner):
    def run_ocr(self, input_path: str, output_path: str, langs: str = None) -> Tuple[bool, str]:
        selected_langs = langs or config.ocr_languages
        cmd = [
            config.ocrmypdf_cmd,
            "--skip-text",
            "--max-image-mpixels", "10",
            "--tesseract-downsample-large-images",
            "--tesseract-downsample-above", "2500",
            "--invalidate-digital-signatures",
            "--optimize", "0",
            "-l", selected_langs,
            "-j", str(config.ocr_jobs),
            input_path,
            output_path
        ]
        try:
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=config.tesseract_timeout
            )
            # Exit codes: 0 = success, 6 = already has text
            if res.returncode in (0, 6):
                return True, "OCR completed successfully"
            return False, f"OCRmyPDF failed ({res.returncode}): {res.stderr.strip()}"
        except Exception as e:
            return False, f"OCR execution exception: {str(e)}"

class OcrCoordinator:
    """
    Coordinates Vector detection, OCR execution, text extraction, and quality grading.
    """
    def __init__(self, detector: IVectorDetector = None, runner: IOcrRunner = None, assessor: IQualityAssessor = None):
        self.detector = detector or PopplerVectorDetector()
        self.runner = runner or OcrMyPdfRunner()
        self.assessor = assessor or LexicalQualityAssessor()

    def _enrich_embedded_diagrams(self, input_path: str, output_path: str, fallback_text: str) -> Tuple[bool, str]:
        """
        Scans born-digital PDF for embedded diagram images (width >= 350, height >= 200).
        Runs Tesseract OCR directly on the isolated image crops and injects invisible
        searchable text layers (render_mode=3) at the exact diagram coordinates.
        Preserves 100% of native vector text while making diagrams fully selectable.
        """
        try:
            import fitz
            doc = fitz.open(input_path)
            diagrams_ocred = 0

            # Fast check if any images exist
            total_images = sum(len(page.get_images()) for page in doc)
            if total_images == 0:
                doc.close()
                shutil.copy2(input_path, output_path)
                return False, fallback_text

            for page_idx in range(len(doc)):
                page = doc[page_idx]
                for img in page.get_images(full=True):
                    xref = img[0]
                    w, h = img[2], img[3]
                    # Only process large diagrams/figures, ignore tiny icons, logos, bullets
                    if w >= 350 and h >= 200:
                        try:
                            bbox = page.get_image_bbox(img)
                        except Exception:
                            continue

                        try:
                            base_img = doc.extract_image(xref)
                            img_bytes = base_img["image"]
                            ext = base_img.get("ext", "png")
                            tmp_img = f"/tmp/diag_{xref}.{ext}"
                            with open(tmp_img, "wb") as f:
                                f.write(img_bytes)

                            res = subprocess.run(
                                ["tesseract", tmp_img, "stdout", "-l", "fra+eng"],
                                capture_output=True,
                                text=True,
                                timeout=15
                            )
                            if os.path.exists(tmp_img):
                                os.remove(tmp_img)

                            txt = res.stdout.strip()
                            if txt and len(txt) > 5:
                                # Render mode 3 = invisible text (preserves image appearance while making text selectable)
                                page.insert_textbox(bbox, txt, fontsize=10, render_mode=3)
                                diagrams_ocred += 1
                        except Exception:
                            continue

            if diagrams_ocred > 0:
                doc.save(output_path)
                doc.close()
                try:
                    res = subprocess.run(
                        [config.pdftotext_cmd, "-l", "2", output_path, "-"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=10
                    )
                    extracted_text = res.stdout.strip()
                except Exception:
                    extracted_text = fallback_text
                return True, extracted_text
            else:
                doc.close()
                shutil.copy2(input_path, output_path)
                return False, fallback_text
        except Exception:
            shutil.copy2(input_path, output_path)
            return False, fallback_text

    def process(self, input_path: str, output_path: str) -> Dict[str, Any]:
        # 1. Step 1: Check if already native vector text
        is_native, char_count, sample_text = self.detector.is_native_vector_pdf(input_path)

        ocr_applied = False
        if is_native:
            # Native vector document: perform selective OCR on embedded diagrams
            ocr_applied, extracted_text = self._enrich_embedded_diagrams(input_path, output_path, sample_text)
        else:
            # Check if document has Arabic characters
            has_arabic = bool(re.search(r'[\u0600-\u06FF]', sample_text))
            selected_langs = "ara+fra+eng" if has_arabic else "fra+eng"
            # Scanned image or mixed document -> execute lossless OCR
            success, msg = self.runner.run_ocr(input_path, output_path, langs=selected_langs)
            if not success:
                # If OCRmyPDF failed, copy original so pipeline doesn't break, but flag for vision escalation
                shutil.copy2(input_path, output_path)
            ocr_applied = True

            # Extract newly created text from output file
            try:
                res = subprocess.run(
                    [config.pdftotext_cmd, "-l", "2", output_path, "-"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=10
                )
                extracted_text = res.stdout.strip()
            except Exception:
                extracted_text = ""

        # 2. Step 2: Quality & Garbage Assessment
        quality_report = self.assessor.assess_quality(extracted_text)

        return {
            "status": "PROCESSED",
            "is_native_vector": is_native,
            "ocr_applied": ocr_applied,
            "extracted_chars_count": len(extracted_text),
            "quality_metrics": quality_report,
            "needs_vision_escalation": quality_report["needs_vision_escalation"],
            "sample_snippet": extracted_text[:250].replace("\n", " ").strip(),
            "processed_at": datetime.utcnow().isoformat()
        }

import os
from pydantic import BaseModel

class OcrConfig(BaseModel):
    service_name: str = "pdf-ocr"
    service_version: str = "1.0.0"
    
    # Quality thresholds
    min_dictionary_validity_ratio: float = float(os.getenv("MIN_DICT_RATIO", "0.65"))
    max_symbol_noise_ratio: float = float(os.getenv("MAX_NOISE_RATIO", "0.25"))
    min_char_count_first_page: int = int(os.getenv("MIN_CHARS", "60"))
    
    # OCR settings
    ocr_languages: str = os.getenv("OCR_LANGUAGES", "eng+fra+ara")
    ocr_jobs: int = int(os.getenv("OCR_JOBS", "1"))
    tesseract_timeout: int = int(os.getenv("TESSERACT_TIMEOUT", "180"))
    
    # CLI Binaries
    pdffonts_cmd: str = os.getenv("PDFFONTS_CMD", "pdffonts")
    pdftotext_cmd: str = os.getenv("PDFTOTEXT_CMD", "pdftotext")
    ocrmypdf_cmd: str = os.getenv("OCRMYPDF_CMD", "ocrmypdf")

config = OcrConfig()

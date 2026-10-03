.PHONY: ocr-image test

OCR_IMAGE = batlab/pgsocr:1

ocr-image:
	docker build -t $(OCR_IMAGE) ocr

test:
	python3 -m unittest discover -s scripts/tests
	python3 -m unittest discover -s ocr/tests

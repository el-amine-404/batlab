.PHONY: help build docker-build docker-scan docker-apply docker-rollback docker-commit clean

IMAGE_NAME = image-medic
WORKSPACE = ~/.local/share/image_medic
LIB ?= /tmp/test_library

help:
	@echo "Image Medic Makefile commands:"
	@echo "  make docker-build    - Build the Docker image"
	@echo "  make docker-scan     - Scan the library and generate repairs in local review folder"
	@echo "  make docker-apply    - Replace originals with approved repairs, backing up originals"
	@echo "  make docker-rollback - Restore backed up originals if you aren't happy with the repairs"
	@echo "  make docker-commit   - Permanently delete the backups once you verify the share is healthy"
	@echo "  make clean           - Remove local workspace, test data, and python caches"

build:
	pip3 install -r requirements.txt

docker-build:
	docker build -t $(IMAGE_NAME) .

docker-scan:
	@mkdir -p $(WORKSPACE)
	@if [ "$(LIB)" = "/tmp/test_library" ]; then echo "Provide library: make docker-scan LIB=/mnt/photos"; exit 1; fi
	docker run --rm --user $$(id -u):$$(id -g) -v $(LIB):/library -v $(WORKSPACE):/workspace $(IMAGE_NAME) --mode scan --library /library --workspace /workspace --host-library $(LIB) --host-workspace $(WORKSPACE)

docker-apply:
	@mkdir -p $(WORKSPACE)
	@if [ "$(LIB)" = "/tmp/test_library" ]; then echo "Provide library: make docker-apply LIB=/mnt/photos"; exit 1; fi
	docker run --rm --user $$(id -u):$$(id -g) -v $(LIB):/library -v $(WORKSPACE):/workspace $(IMAGE_NAME) --mode apply --library /library --workspace /workspace --host-library $(LIB) --host-workspace $(WORKSPACE)

docker-rollback:
	@mkdir -p $(WORKSPACE)
	@if [ "$(LIB)" = "/tmp/test_library" ]; then echo "Provide library: make docker-rollback LIB=/mnt/photos"; exit 1; fi
	docker run --rm --user $$(id -u):$$(id -g) -v $(LIB):/library -v $(WORKSPACE):/workspace $(IMAGE_NAME) --mode rollback --library /library --workspace /workspace --host-library $(LIB) --host-workspace $(WORKSPACE)

docker-commit:
	@mkdir -p $(WORKSPACE)
	@if [ "$(LIB)" = "/tmp/test_library" ]; then echo "Provide library: make docker-commit LIB=/mnt/photos"; exit 1; fi
	docker run --rm --user $$(id -u):$$(id -g) -v $(LIB):/library -v $(WORKSPACE):/workspace $(IMAGE_NAME) --mode commit --library /library --workspace /workspace --host-library $(LIB) --host-workspace $(WORKSPACE)

clean:
	rm -rf $(WORKSPACE)
	rm -rf /tmp/medic_workspace
	rm -rf /tmp/test_library
	rm -rf __pycache__

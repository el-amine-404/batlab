FROM python:3.12-alpine

# Use Alpine Edge repositories to guarantee the absolute latest versions 
# of ImageMagick (7.1.2+) and ExifTool (13.x+)
RUN apk add --no-cache \
    --repository=http://dl-cdn.alpinelinux.org/alpine/edge/main \
    --repository=http://dl-cdn.alpinelinux.org/alpine/edge/community \
    imagemagick exiftool build-base jpeg-dev zlib-dev

WORKDIR /app

# Install Python requirements (Pillow)
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy and set up the script
COPY image_medic.py .
RUN chmod +x image_medic.py

ENTRYPOINT ["python3", "/app/image_medic.py"]

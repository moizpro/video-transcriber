FROM python:3.12-slim

# faster-whisper necesita ffmpeg para leer el audio de los vídeos;
# la imagen "slim" no lo incluye.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY transcriber_core.py .
COPY web/ web/

ENV UPLOAD_DIR=/data/jobs
VOLUME ["/data/jobs"]

EXPOSE 8000
CMD ["uvicorn", "web.server:app", "--host", "0.0.0.0", "--port", "8000"]

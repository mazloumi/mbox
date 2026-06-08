FROM python:3.12-slim

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends antiword catdoc \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MBOX_PATH=/data/mail.mbox \
    INDEX_PATH=/index/index.db \
    ARCHIVE_DIR=/archive \
    PORT=9000

EXPOSE 9000
CMD ["python", "-m", "mboxviewer.main"]

FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
ENV PYTHONPATH=/app/src \
    MBOX_PATH=/data/mail.mbox \
    INDEX_PATH=/index/index.db \
    PORT=8000

EXPOSE 8000
CMD ["python", "-m", "mboxviewer.main"]

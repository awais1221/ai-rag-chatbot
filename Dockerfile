# Works on Render (free plan), Railway, Fly.io, Hugging Face Spaces or any VPS.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FASTEMBED_CACHE_PATH=/app/.cache/fastembed \
    DATA_DIR=/app/data \
    TRUST_PROXY=true \
    PORT=7860

# Run as a non-root user (uid 1000)
RUN useradd -m -u 1000 app
WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY --chown=app:app . .
RUN mkdir -p /app/data /app/.cache && chown -R app:app /app/data /app/.cache
USER app

# Bake the embedding model into the image so cold starts don't download it
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/app/.cache/fastembed')"

EXPOSE 7860
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers"]

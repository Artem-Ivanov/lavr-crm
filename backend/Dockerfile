FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --uid 1000 --create-home lavr \
    && mkdir -p /data/media && chown -R lavr:lavr /data
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini entrypoint.sh seed.json ./
COPY seed-media ./seed-media
USER 1000:1000
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
CMD ["sh", "entrypoint.sh"]

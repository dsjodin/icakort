FROM python:3.11-slim

# Icke-root från början. /data ägs av användaren, så en namngiven volym
# ärver rätt ägare när Docker skapar den.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data \
    && chown app:app /data

WORKDIR /app

# Beroendena först, så att ett kodbyte inte river pip-lagret.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV ICAKORT_DATA_DIR=/data \
    ICAKORT_HOST=0.0.0.0 \
    ICAKORT_PORT=8000 \
    PYTHONUNBUFFERED=1

USER app
VOLUME ["/data"]
EXPOSE 8000

# Hälsokollen är undantagen från lösenordsskyddet. urllib i stället för curl,
# så vi slipper installera något extra i imagen.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz').read()"]

CMD ["icakort", "serve"]

ARG PYTHON_BASE_IMAGE=python:3.12.10-slim-bookworm
FROM ${PYTHON_BASE_IMAGE} AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY pyproject.toml README.md requirements.lock ./
COPY src ./src
RUN python -m pip install --upgrade pip && python -m pip wheel --wheel-dir /wheels -r requirements.lock && python -m pip wheel --wheel-dir /wheels --no-deps .

FROM ${PYTHON_BASE_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH
RUN addgroup --system app \
    && adduser --system --ingroup app --home /app app \
    && install -d -o app -g app -m 700 /run/secrets
WORKDIR /app
RUN python -m venv /opt/venv
COPY --from=builder /wheels /wheels
COPY requirements.lock ./
RUN pip install --no-index --find-links=/wheels -r requirements.lock && pip install --no-index --find-links=/wheels --no-deps solana-gem-filter-bot && rm -rf /wheels
COPY alembic.ini ./
COPY migrations ./migrations
USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/live', timeout=3)"
CMD ["gem-filter-api"]

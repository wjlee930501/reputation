FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y \
    gcc libpq-dev \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b \
    libffi-dev libjpeg-dev libopenjp2-7 \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml .
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
RUN pip install --no-cache-dir .
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh
RUN useradd -m appuser \
    && mkdir -p /tmp/private_asset_uploads /tmp/sites /tmp/reports \
    && chown -R appuser:appuser /app /tmp/private_asset_uploads /tmp/sites /tmp/reports
USER appuser
ENV PYTHONPATH=/app
ENTRYPOINT ["./docker-entrypoint.sh"]

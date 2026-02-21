FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y \
    gcc libpq-dev \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b \
    libffi-dev libjpeg-dev libopenjp2-7 \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"
COPY . .
ENV PYTHONPATH=/app

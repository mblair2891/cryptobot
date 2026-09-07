FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MODE=demo \
    DATA_DIR=/app/data

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./

RUN pip install --no-cache-dir .

RUN mkdir -p /app/data
EXPOSE 8000
CMD ["aethergrid", "serve"]

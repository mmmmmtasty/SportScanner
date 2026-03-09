FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src

RUN python -m pip install --upgrade pip && \
    python -m pip install .

EXPOSE 32699

CMD ["sh", "-c", "alembic upgrade head && uvicorn sportscanner.main:create_app --factory --host 0.0.0.0 --port 32699"]


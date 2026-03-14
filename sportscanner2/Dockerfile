FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

LABEL net.unraid.docker.webui="http://[IP]:[PORT:32699]/admin/" \
      net.unraid.docker.managed="dockerman"

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --upgrade pip && \
    python -m pip install .

EXPOSE 32699

CMD ["uvicorn", "sportscanner.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "32699"]

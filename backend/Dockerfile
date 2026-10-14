FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FIELDNOTE_SECRETS_DIR=/run/secrets
WORKDIR /app
RUN groupadd --system app && useradd --system --gid app --create-home app
RUN mkdir -p /run/secrets && chown app:app /run/secrets
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=app:app . .
USER app
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent ./agent
COPY ai ./ai
COPY api ./api
COPY auth ./auth
COPY festivals ./festivals
COPY db ./db
COPY models ./models
COPY scheduler ./scheduler
COPY services ./services
COPY tools ./tools
COPY backend ./backend
COPY config.py main.py alembic.ini ./

RUN mkdir -p storage tmp input output data \
    && useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

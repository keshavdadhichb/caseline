FROM python:3.11-slim

WORKDIR /app

# System deps for scikit-learn/pandas wheels build cleanly on slim.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
# Only the runtime dataset, not the 486MB raw source or the 25MB eval splits.
COPY data/sample ./data/sample
COPY data/method_metrics.json ./data/method_metrics.json

# Populated on first query; a named volume in compose keeps it warm across
# restarts so the offline-fallback guarantee holds after a redeploy too.
RUN mkdir -p .cache/plans .cache/gemini

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "backend"]

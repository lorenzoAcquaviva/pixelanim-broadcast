
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# Render pool
ENV RENDER_WORKERS=2 \
    RENDER_MP_METHOD=fork

# Cache render
ENV RENDER_CACHE_DISABLED=0 \
    RENDER_RESULT_MAX=8 \
    RENDER_RESULT_MAX_FRAMES=24000

# Cache palette
ENV PALETTE_CACHE_TTL=30

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Backend suddiviso in moduli
COPY main.py api.py core.py database.py ./

# Frontend
COPY static ./static

EXPOSE 9910

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9910"]

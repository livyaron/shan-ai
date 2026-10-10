# מערכת לקחים UI (lessons_ui/) — built here, copied into static/lessons below.
FROM node:22-slim AS ui
WORKDIR /ui
COPY lessons_ui/package.json lessons_ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY lessons_ui/ ./
RUN npm run build

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    ffmpeg \
    fonts-noto-unhinted \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libpango-1.0-0 \
    libharfbuzz0b \
    libfontconfig1 \
    fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .
COPY --from=ui /ui/dist /app/static/lessons

# Expose FastAPI port (Railway injects $PORT at runtime)
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

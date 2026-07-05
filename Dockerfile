# QuantDeck — single-image deploy.
# Stage 1 builds the React frontend; stage 2 runs the FastAPI backend and serves
# the built frontend from the same origin (the frontend uses relative /api + ws
# URLs, so one origin means zero CORS/config).

# ---- Stage 1: build the frontend ----
FROM node:20-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build   # -> /app/frontend/dist

# ---- Stage 2: backend runtime ----
FROM python:3.12-slim AS runtime
WORKDIR /app

# System deps kept minimal; slim image already has what we need.
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install Python deps first (better layer caching).
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install -r backend/requirements.txt

# Backend source.
COPY backend/ ./backend/

# Built frontend copied where the app looks for it (see app._frontend_dir()).
COPY --from=frontend /app/frontend/dist ./backend/frontend_dist

WORKDIR /app/backend

# Writable dir for the SQLite DB (ephemeral on most hosts unless a volume is
# attached — paper portfolios reset on redeploy, which is fine for a demo).
RUN mkdir -p data

# The host injects $PORT; default to 8000 for local `docker run`.
ENV QUANTDECK_HOST=0.0.0.0
EXPOSE 8000

# Bind to $PORT if provided (Render/Fly/Railway all set it), else 8000.
CMD ["sh", "-c", "python -m uvicorn quantdeck.api.app:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}"]

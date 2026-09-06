# Single-service deploy -- MASTER_PLAN.md step 4.1. Multi-stage: compile the
# frontend, then serve the built bundle from the same FastAPI process that
# serves the API, so the whole app lives behind one origin and one HTTPS
# certificate (app/main.py's own docstring; docs/architecture.md's CORS note
# on why that single choice matters for both CORS and the mic-permission
# HTTPS requirement alike).
#
# The directory layout below is not arbitrary: app/main.py resolves the
# frontend build as `Path(__file__).resolve().parents[2] / "frontend" / "dist"`
# -- two levels above app/main.py itself, i.e. one level above backend/. This
# image reproduces that same repo-relative shape (/app/backend, /app/frontend)
# rather than flattening everything into one directory, specifically so that
# path resolves the same way here as it does locally.

FROM node:20-slim AS frontend-builder
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
WORKDIR /app/backend

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
COPY --from=frontend-builder /build/frontend/dist /app/frontend/dist

# Most PaaS hosts (Render, Railway, Fly.io) inject their own $PORT at
# runtime; 8000 is the fallback, matching this project's own local-dev
# convention (README's "Running locally").
ENV PORT=8000
EXPOSE 8000

# Shell form so ${PORT} actually expands -- exec form would pass the
# literal, unexpanded string to uvicorn's --port instead of a number.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}

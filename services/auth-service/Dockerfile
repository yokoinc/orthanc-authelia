# Multi-stage build: stage 1 compiles the Vue frontend with Vite, stage 2
# assembles the final Python image with the frontend's dist embedded.
#
# Build context = services/auth-service/ (holds sources/ and frontend/).
# The former build path (services/auth-service/sources/) is deprecated.
#
# Local rebuild:
#   docker build -t registry.yokoinc.ovh/orthanc-auth-service:1.0.16 \
#     services/auth-service/

# ============================================================================
# Stage 1: build the Vue frontend
# ============================================================================
FROM node:20-alpine AS frontend

WORKDIR /build

# Caching: package.json first, then npm ci, then the sources
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund

COPY frontend/ ./
RUN npm run build
# Output: /build/dist/ holds index.html and assets/

# ============================================================================
# Stage 2: final Python image
# ============================================================================
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

COPY sources/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

# Backend Python
COPY sources/entrypoint.sh /app/
COPY sources/auth_service.py /app/
COPY sources/admin_module.py /app/
COPY sources/static/ /app/static/
COPY sources/templates/ /app/templates/
COPY sources/translations/ /app/translations/

# Compiled Vue frontend (imported from stage 1)
COPY --from=frontend /build/dist/ /app/frontend/

RUN chmod +x /app/entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "auth_service:app", "--host", "0.0.0.0", "--port", "8000"]

# Multi-stage build : stage 1 compile le frontend Vue via Vite,
# stage 2 assemble l'image Python finale avec le dist du frontend embarque.
#
# Build context = services/auth-service/ (contient sources/ et frontend/).
# Ancien chemin de build (services/auth-service/sources/) est deprecie.
#
# Rebuild local :
#   docker build -t registry.yokoinc.ovh/orthanc-auth-service:1.0.16 \
#     services/auth-service/

# ============================================================================
# Stage 1 : build frontend Vue
# ============================================================================
FROM node:20-alpine AS frontend

WORKDIR /build

# Cache : package.json d'abord, puis npm ci, puis les sources
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund

COPY frontend/ ./
RUN npm run build
# Sortie : /build/dist/ contient index.html + assets/

# ============================================================================
# Stage 2 : image Python finale
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

# Frontend Vue compile (importe depuis stage 1)
COPY --from=frontend /build/dist/ /app/frontend/

RUN chmod +x /app/entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "auth_service:app", "--host", "0.0.0.0", "--port", "8000"]

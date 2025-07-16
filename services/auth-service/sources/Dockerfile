FROM python:3.11-slim

WORKDIR /app

# Install required dependencies (add netcat for health checks)
RUN apt-get update && apt-get install -y netcat-openbsd && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir fastapi uvicorn redis

# Copy entrypoint and application files
COPY entrypoint.sh /app/
COPY auth_service.py /app/
COPY static/ /app/static/
COPY templates/ /app/templates/
COPY translations/ /app/translations/

# Make entrypoint executable
RUN chmod +x /app/entrypoint.sh

# Expose the service port
EXPOSE 8000

# Use entrypoint for configuration and documentation
ENTRYPOINT ["/app/entrypoint.sh"]

# Start FastAPI service with uvicorn in production mode
CMD ["uvicorn", "auth_service:app", "--host", "0.0.0.0", "--port", "8000"]
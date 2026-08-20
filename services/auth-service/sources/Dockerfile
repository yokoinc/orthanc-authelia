FROM python:3.11-slim

WORKDIR /app

# Install OS-level dependencies (netcat for health checks)
RUN apt-get update && apt-get install -y netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies from requirements.txt
# (source unique de verite pour les versions ; requirements-dev.txt reste hors image)
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy entrypoint and application files
COPY entrypoint.sh /app/
COPY auth_service.py /app/
COPY admin_module.py /app/
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
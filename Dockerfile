################################################################################
################################################################################
###                                                                          ###
###                          Dockerfile                                     ###
###                   Docker Container Definition                           ###
###                                                                          ###
################################################################################
################################################################################

# Multi-stage build for Ephemeral REST
FROM python:3.11-slim as builder

# Set working directory
WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# Final stage
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy Python dependencies from builder
COPY --from=builder /root/.local /root/.local

# Make sure scripts in .local are usable
ENV PATH=/root/.local/bin:$PATH

# Copy application code
COPY . .

# Create directories for ephemeris data and database
RUN mkdir -p sweph data logs && \
    chmod -R 755 sweph data logs

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:5000/health')" || exit 1

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    FLASK_HOST=0.0.0.0 \
    FLASK_PORT=5000 \
    DATABASE_PATH=/app/data/ephemeral.db \
    LOG_FILE=/app/logs/google_api_usage.log \
    USAGE_COUNT_FILE=/app/data/api_usage_count.json

# Run with gunicorn
CMD ["gunicorn", "-c", "gunicorn_config.py", "app:create_app()"]
FROM python:3.9-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies and package
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy Guardian source and configs (tracked in git)
COPY guardian/ ./guardian/
COPY config.example.yaml ./config.example.yaml
RUN pip install --no-cache-dir -e .

# Expose Guardian API port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:8080/health || exit 1

# Run the Guardian daemon
CMD ["python", "-m", "guardian.main"]

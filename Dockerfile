# Pinned Python 3.12 slim base image with sha256 digest
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

# Create non-root system user and group
RUN groupadd -r -g 10001 guardian && \
    useradd -r -u 10001 -g guardian -d /app -s /sbin/nologin guardian

WORKDIR /app

# Install system dependencies with minimal attack surface
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy Guardian source and configs (tracked in git; no secrets baked into image)
COPY guardian/ ./guardian/
COPY config.example.yaml ./config.example.yaml
COPY config.render.yaml ./config.render.yaml
RUN pip install --no-cache-dir -e .

# Create writable data directory for SQLite persistence with non-root ownership
RUN mkdir -p /app/data && chown -R guardian:guardian /app

# Switch to unprivileged non-root user
USER guardian

# Expose Guardian API port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:8080/health || exit 1

# Run Guardian daemon (default binds to 127.0.0.1 in production, 0.0.0.0 in container when mapped)
CMD ["python", "-m", "guardian.main", "--host", "0.0.0.0"]

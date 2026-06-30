# CERES OS Python Brain Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml .
COPY ceres_brain/ ceres_brain/

# Install Python dependencies
RUN pip install --no-cache-dir -e .

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV CERES_LOG_LEVEL=INFO

# Default command runs the scout simulator
CMD ["python", "-m", "ceres_brain.scout", "--simulate"]

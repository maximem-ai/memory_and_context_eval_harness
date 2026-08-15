# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy the current directory contents into the container at /app
COPY . /app

# Install any needed packages specified in pyproject.toml
RUN pip install --no-cache-dir .[dev]

# Define environment variable
ENV PYTHONUNBUFFERED=1

# The dashboard and API server.
EXPOSE 8766

# Serve by default. To run the test suite in this image instead:
#   docker run --rm eval-harness:latest pytest tests
CMD ["python", "-m", "runner.server"]

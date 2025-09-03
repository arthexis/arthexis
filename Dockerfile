# Use Ubuntu 22.04 base image for ARM64 to emulate Raspberry Pi 4B environment
FROM --platform=linux/arm64 ubuntu:22.04

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y \
    python3 python3-pip python3-venv \
    build-essential libffi-dev libssl-dev libpq-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Expose default Django port
EXPOSE 8000

# Run the Django development server
CMD ["python3", "manage.py", "runserver", "0.0.0.0:8000"]

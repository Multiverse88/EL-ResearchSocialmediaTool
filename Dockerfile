FROM python:3.12-slim

# Prevent Python from writing .pyc files and buffer output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright's headless Chromium for the free TikTokApi scraping fallback
# (--with-deps pulls the required system libraries via apt)
RUN playwright install --with-deps chromium

# Copy application source code and web assets
COPY src/ /app/src/
COPY static/ /app/static/
COPY openwebui_tool.py /app/openwebui_tool.py

# Create persistent data directory
RUN mkdir -p /app/data
ENV DATABASE_PATH=/app/data/social_media.db

EXPOSE 8000

# Default entrypoint starts the FastAPI server
CMD ["uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8000"]

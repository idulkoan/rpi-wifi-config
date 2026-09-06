FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    python3-dbus \
    libdbus-1-dev \
    libglib2.0-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create non-root user matching host pi uid
RUN useradd -u 1000 -m appuser
USER appuser

COPY --chown=appuser:appuser . .

CMD ["python", "app.py"]

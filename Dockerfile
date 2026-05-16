FROM python:3.11-slim

WORKDIR /app

# ---- System dependencies ----
RUN apt-get update && apt-get install -y \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# ---- Python dependencies ----
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- Copy source (excludes data/ via .dockerignore) ----
COPY . .

# ---- Environment ----
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# ---- Entry point ----
CMD ["python", "main.py", "--help"]

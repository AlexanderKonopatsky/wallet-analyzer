FROM python:3.10-slim

# Install Node.js
RUN apt-get update && apt-get install -y curl && \
    curl -fsSL https://deb.nodesource.com/setup_18.x | bash - && \
    apt-get install -y nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Build frontend
COPY frontend/ frontend/
RUN cd frontend && npm install && npm run build

# Copy backend
COPY backend/ backend/

# Copy other needed files
COPY .env.example .

# Create data directories
RUN mkdir -p data/reports

EXPOSE 8000

CMD cd backend && uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}

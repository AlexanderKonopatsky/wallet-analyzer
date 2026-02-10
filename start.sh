#!/bin/bash
set -e

echo "🚀 Starting deployment..."

# Install Playwright browser
echo "📦 Installing Playwright Chromium..."
playwright install chromium --with-deps

# Build frontend
echo "🏗️  Building frontend..."
cd frontend
npm install
npm run build
cd ..

# Start backend
echo "🔥 Starting FastAPI server..."
cd backend
uvicorn server:app --host 0.0.0.0 --port $PORT

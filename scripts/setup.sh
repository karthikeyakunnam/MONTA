#!/bin/bash
# MONTA — Development Setup Script
set -e

echo "🎬 Setting up MONTA development environment..."

# Copy env file
cp -n .env.example .env 2>/dev/null || echo ".env already exists"

# Install frontend dependencies
echo "📦 Installing frontend dependencies..."
cd frontend/web && npm install && cd ../..

# Install backend dependencies
echo "🐍 Installing backend dependencies..."
cd backend && pip install -r requirements.txt && cd ..

# Install worker dependencies
echo "⚙️ Installing worker dependencies..."
cd workers && pip install -r requirements.txt && cd ..

# Install orchestration dependencies
echo "🧠 Installing orchestration dependencies..."
cd orchestration && pip install -r requirements.txt && cd ..

# Start infrastructure
echo "🐳 Starting Docker services (PostgreSQL, Redis, Qdrant)..."
docker-compose up -d postgres redis qdrant

echo ""
echo "✅ MONTA setup complete!"
echo ""
echo "Start development:"
echo "  make backend    # Start FastAPI"
echo "  make frontend   # Start Next.js"
echo "  make worker     # Start Celery worker"
echo "  make dev        # Start everything"

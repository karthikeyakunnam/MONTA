#!/bin/bash
# MONTA — Run Development Servers
set -e

echo "🎬 Starting MONTA development servers..."

# Start infrastructure
docker-compose up -d postgres redis qdrant

# Start backend
cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

# Start worker
cd ../workers && celery -A celery_app worker --loglevel=info &
WORKER_PID=$!

# Start frontend
cd ../frontend/web && npm run dev &
FRONTEND_PID=$!

echo "✅ All services running"
echo "  Frontend: http://localhost:3000"
echo "  Backend:  http://localhost:8000"
echo "  API Docs: http://localhost:8000/docs"

# Wait for any process to exit
wait -n
kill $BACKEND_PID $WORKER_PID $FRONTEND_PID 2>/dev/null

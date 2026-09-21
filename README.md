# 🎬 MONTA — AI-Powered Video Editing Platform

> "Tell MONTA what you want. It edits for you."

MONTA is a 14-layer AI video editing engine that transforms natural language prompts into fully edited, cinematic videos. From understanding footage to composing stories, grading colors, syncing audio, and rendering final exports — MONTA handles it all.

## Architecture

```
Layer 1  → Experience Layer (Next.js / Electron)
Layer 2  → Media Gateway (Upload, Validate, Store)
Layer 3  → Prompt Intelligence Engine (Intent Parsing)
Layer 4  → Context Composer (User + Platform + History)
Layer 5  → Director Agent (Task Planning — the CEO)
Layer 6  → Video Intelligence Team (Scene, Action, Quality, Emotion)
Layer 7  → Story Architect (Narrative Structure)
Layer 8  → Style Engine (Visual Style Mapping)
Layer 9  → Timeline Generator (Edit Plan)
Layer 10 → Audio Studio (MONTA Audio — Music, Remix, FX)
Layer 11 → Edit Executor (FFmpeg, OpenCV, Whisper)
Layer 12 → Critic System (Evaluate + Retry Loop)
Layer 13 → Render Farm (Multi-Res Export)
Layer 14 → Learning Layer (User Preferences + Feedback)
```

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Frontend | Next.js, Electron (later) |
| Backend | FastAPI |
| Orchestration | LangGraph |
| Queue | Redis + Celery |
| Database | PostgreSQL |
| Vector Memory | Qdrant |
| Vision | Qwen-VL, Gemini Vision, OpenCV |
| Speech | Whisper |
| Video | FFmpeg, MoviePy |
| Local Models | Qwen 3, Llama 3 |
| Deployment | Docker, Kubernetes (later) |

## Quick Start

```bash
# 1. Clone & setup
cp .env.example .env

# 2. Start all services
docker-compose up -d

# 3. Start frontend
cd frontend/web && npm install && npm run dev

# 4. Start backend
cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload

# 5. Start workers
cd workers && celery -A celery_app worker --loglevel=info
```

## Project Structure

```
MONTA/
├── frontend/          # Layer 1 — Experience Layer
├── backend/           # FastAPI API Server
├── orchestration/     # LangGraph — Layers 5, 6, 7, 12
├── workers/           # Celery Workers
├── services/          # Layers 2-4, 8-11, 13
├── vision/            # Qwen-VL, Gemini Vision, OpenCV
├── speech/            # Whisper
├── models/            # Qwen 3, Llama 3
├── memory/            # Layer 14 — Qdrant + Learning
├── shared/            # Common utilities
├── infra/             # Docker, K8s, Redis, Nginx
├── scripts/           # Dev & ops scripts
└── docs/              # Documentation
```

## License

Proprietary — All Rights Reserved.

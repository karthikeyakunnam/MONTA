# MONTA Project Architecture

## What is MONTA? What problem does it solve?

### Takeaway
MONTA is an AI-powered video editing platform that transforms natural language prompts into fully edited, cinematic videos — automating the entire editing pipeline from footage understanding to final render.

### Cited Findings
- MONTA is described as a "14-layer AI video editing engine that transforms natural language prompts into fully edited, cinematic videos" — [README.md](file:///Users/karthikeyaunnam/Documents/MONTA/README.md)
- Tagline: "Tell MONTA what you want. It edits for you." — [README.md](file:///Users/karthikeyaunnam/Documents/MONTA/README.md)
- The platform handles: understanding footage, composing stories, grading colors, syncing audio, and rendering final exports — [README.md](file:///Users/karthikeyaunnam/Documents/MONTA/README.md)
- The 14 layers are: Experience (UI), Media Gateway, Prompt Intelligence, Context Composer, Director Agent, Video Intelligence, Story Architect, Style Engine, Timeline Generator, Audio Studio, Edit Executor, Critic System, Render Farm, Learning Layer — [README.md](file:///Users/karthikeyaunnam/Documents/MONTA/README.md)

### Inferences
- MONTA solves the problem of complex, time-consuming video editing by using AI to interpret user intent and automate the full editing workflow
- The layered architecture separates concerns: intelligence layers (3-7) handle creative decision-making while execution layers (8-13) handle technical rendering

### Gaps
- No information about target market, pricing, or competitive positioning found in codebase
- No user adoption metrics or traction data available

## What is the tech stack?

### Takeaway
Python-based backend (FastAPI), Next.js frontend, LangGraph orchestration, Celery/Redis for async tasks, PostgreSQL for persistence, Qdrant for vector memory, and FFmpeg/OpenCV/Whisper for media processing.

### Cited Findings
- **Frontend**: Next.js (Electron planned for later) — [README.md](file:///Users/karthikeyaunnam/Documents/MONTA/README.md)
- **Backend**: FastAPI with uvicorn — [README.md](file:///Users/karthikeyaunnam/Documents/MONTA/README.md); [backend/requirements.txt](file:///Users/karthikeyaunnam/Documents/MONTA/backend/requirements.txt)
- **Orchestration**: LangGraph + LangChain — [orchestration/requirements.txt](file:///Users/karthikeyaunnam/Documents/MONTA/orchestration/requirements.txt)
- **Task Queue**: Celery 5.4+ with Redis broker — [workers/celery_app.py](file:///Users/karthikeyaunnam/Documents/MONTA/workers/celery_app.py)
- **Database**: PostgreSQL 16 with SQLAlchemy async + Alembic migrations — [docker-compose.yml](file:///Users/karthikeyaunnam/Documents/MONTA/docker-compose.yml); [backend/requirements.txt](file:///Users/karthikeyaunnam/Documents/MONTA/backend/requirements.txt)
- **Vector Memory**: Qdrant — [docker-compose.yml](file:///Users/karthikeyaunnam/Documents/MONTA/docker-compose.yml)
- **Media Processing**: FFmpeg, OpenCV, MoviePy, Whisper — [README.md](file:///Users/karthikeyaunnam/Documents/MONTA/README.md)
- **AI Models**: Qwen-VL, Gemini Vision, Qwen 3, Llama 3 — [README.md](file:///Users/karthikeyaunnam/Documents/MONTA/README.md)
- **Infrastructure**: Docker, Kubernetes (planned) — [README.md](file:///Users/karthikeyaunnam/Documents/MONTA/README.md)
- **Python Libraries**: pydantic v2, httpx, numpy, boto3, python-jose, passlib/bcrypt — [backend/requirements.txt](file:///Users/karthikeyaunnam/Documents/MONTA/backend/requirements.txt)

### Inferences
- The stack is modern and cloud-native with async Python throughout
- Heavy reliance on external AI model providers (Gemini, Qwen via DashScope, OpenAI-compatible endpoints)
- Docker Compose is the primary development/deployment orchestration; K8s is planned but not yet implemented

### Gaps
- No CI/CD configuration files found (no .github/workflows, no Jenkinsfile, etc.)
- No Terraform or infrastructure-as-code configs found beyond Docker
- Frontend package.json not examined in detail

## What are the main components/modules?

### Takeaway
The codebase is organized into 8+ top-level directories: backend (FastAPI API), orchestration (LangGraph agents), services (business logic layers), workers (Celery tasks), vision/speech/models (AI capabilities), memory (narrative learning), shared (contracts/utilities), and frontend (Next.js UI).

### Cited Findings
- **backend/**: FastAPI API server with Alembic migrations, app code, and tests — [backend/](file:///Users/karthikeyaunnam/Documents/MONTA/backend)
- **orchestration/**: LangGraph-based agents (director, intelligence, story) and pipeline composition root — [orchestration/](file:///Users/karthikeyaunnam/Documents/MONTA/orchestration); [orchestration/pipeline.py](file:///Users/karthikeyaunnam/Documents/MONTA/orchestration/pipeline.py)
- **services/**: 8 service modules — audio_studio, context_composer, edit_executor, media_gateway, prompt_engine, render_farm, style_engine, timeline_generator — [services/](file:///Users/karthikeyaunnam/Documents/MONTA/services)
- **workers/**: Celery task definitions for pipeline, render, and export tasks — [workers/](file:///Users/karthikeyaunnam/Documents/MONTA/workers)
- **vision/**: Qwen-VL, Gemini Vision, OpenCV integrations — [README.md](file:///Users/karthikeyaunnam/Documents/MONTA/README.md)
- **speech/**: Whisper integration — [README.md](file:///Users/karthikeyaunnam/Documents/MONTA/README.md)
- **models/**: Qwen 3, Llama 3 local model support — [README.md](file:///Users/karthikeyaunnam/Documents/MONTA/README.md)
- **memory/**: Layer 14 — Qdrant + narrative learning — [README.md](file:///Users/karthikeyaunnam/Documents/MONTA/README.md)
- **shared/**: Common utilities, contracts, providers, observability — [docs/layers-3-7.md](file:///Users/karthikeyaunnam/Documents/MONTA/docs/layers-3-7.md)
- **evaluation/**: Story judge, golden dataset, benchmarks — [docs/layers-3-7.md](file:///Users/karthikeyaunnam/Documents/MONTA/docs/layers-3-7.md)
- **frontend/**: Next.js web application — [README.md](file:///Users/karthikeyaunnam/Documents/MONTA/README.md)
- **infra/**: Docker, K8s, Redis, Nginx configs — [README.md](file:///Users/karthikeyaunnam/Documents/MONTA/README.md)

### Inferences
- The architecture follows a clean separation: orchestration coordinates, services execute, workers handle async tasks
- The `shared/` directory is critical — it contains all cross-layer contracts (frozen pydantic models), model provider abstractions, and observability
- The evaluation/ directory suggests a mature testing/validation culture with golden datasets and benchmark tooling

### Gaps
- No detailed examination of frontend/ directory structure
- No examination of bank_project/, datasets/, reports/, scripts/, storage/, benchmarks/ directories
- No API endpoint documentation examined (docs/api.md not read)

## How do the components interact?

### Takeaway
The pipeline flows: User prompt → IntentEngine (L3) → ContextComposer (L4) → DirectorAgent (L5) plans DAG → VideoIntelligenceTeam (L6) analyzes clips in parallel → StoryArchitect (L7) designs narrative → validation → Layers 8-13 execute render. Celery workers handle async task execution; the backend API orchestrates the overall flow.

### Cited Findings
- Pipeline composition root is `orchestration/pipeline.py` — `MontaPipeline` class wires all dependencies — [orchestration/pipeline.py](file:///Users/karthikeyaunnam/Documents/MONTA/orchestration/pipeline.py)
- Flow: `prompt → IntentEngine (L3) → probe clips → ContextComposer (L4) → ContextPack → DirectorAgent (L5) plans & executes → analyze_clip ×N (L6, parallel) → reconcile_intent → design_story (L7) → validate_story` — [docs/layers-3-7.md](file:///Users/karthikeyaunnam/Documents/MONTA/docs/layers-3-7.md)
- DirectorAgent uses a DAG-based execution model with bounded concurrency, retry policies, and critical task abort — [docs/layers-3-7.md](file:///Users/karthikeyaunnam/Documents/MONTA/docs/layers-3-7.md)
- Celery workers process pipeline_tasks, render_tasks, and export_tasks asynchronously via Redis broker — [workers/celery_app.py](file:///Users/karthikeyaunnam/Documents/MONTA/workers/celery_app.py)
- Docker Compose defines 5 services: postgres, redis, qdrant, backend, worker, frontend — [docker-compose.yml](file:///Users/karthikeyaunnam/Documents/MONTA/docker-compose.yml)
- Backend depends on postgres, redis, qdrant; worker depends on redis, postgres — [docker-compose.yml](file:///Users/karthikeyaunnam/Documents/MONTA/docker-compose.yml)
- Model provider abstraction supports Gemini (REST), OpenAI-compatible (Qwen/Qwen-VL on DashScope, vLLM/SGLang/TGI), with circuit breaker and fallback — [docs/layers-3-7.md](file:///Users/karthikeyaunnam/Documents/MONTA/docs/layers-3-7.md)
- Narrative memory uses PostgreSQL (production) or in-memory (dev), with hierarchical Bayesian smoothing — [docs/layers-3-7.md](file:///Users/karthikeyaunnam/Documents/MONTA/docs/layers-3-7.md)
- The Critic system (L12) evaluates output and can trigger retry loops with pattern exclusion — [orchestration/pipeline.py](file:///Users/karthikeyaunnam/Documents/MONTA/orchestration/pipeline.py)

### Inferences
- The system is designed for resilience: model failures degrade gracefully (signal-only analysis without vision), circuit breakers prevent cascade failures, and the Director isolates bad clips
- The architecture supports both local (Qwen/Llama) and cloud (Gemini/DashScope) model providers with a unified abstraction
- WebSocket support (python-jose, websockets in requirements) suggests real-time progress updates to the frontend

### Gaps
- No examination of actual API route definitions in backend/app/
- No examination of frontend-to-backend communication patterns
- No examination of the master_graph.py LangGraph definition
- docs/api.md not reviewed

## What are the key dependencies and external services?

### Takeaway
Key external dependencies include AI model providers (Google Gemini, DashScope/Qwen, OpenAI-compatible endpoints), FFmpeg for media processing, PostgreSQL/Qdrant/Redis for data, and AWS S3 (boto3) for storage. Internal dependencies center on LangGraph, FastAPI, Celery, and pydantic v2.

### Cited Findings
- **AI Providers**: Google Gemini (REST generateContent), DashScope (Qwen/Qwen-VL), OpenAI-compatible (vLLM/SGLang/TGI for self-hosted models) — [docs/layers-3-7.md](file:///Users/karthikeyaunnam/Documents/MONTA/docs/layers-3-7.md)
- **Media Tools**: FFmpeg, ffprobe (for video decode, probing, rendering) — [orchestration/pipeline.py](file:///Users/karthikeyaunnam/Documents/MONTA/orchestration/pipeline.py)
- **Data Stores**: PostgreSQL 16 (primary DB), Redis 7 (Celery broker + cache), Qdrant (vector memory) — [docker-compose.yml](file:///Users/karthikeyaunnam/Documents/MONTA/docker-compose.yml)
- **Cloud Storage**: AWS S3 via boto3 — [backend/requirements.txt](file:///Users/karthikeyaunnam/Documents/MONTA/backend/requirements.txt)
- **Auth**: python-jose (JWT), passlib/bcrypt (password hashing) — [backend/requirements.txt](file:///Users/karthikeyaunnam/Documents/MONTA/backend/requirements.txt)
- **Core Frameworks**: FastAPI, LangGraph, LangChain, Celery, Pydantic v2, SQLAlchemy async — [backend/requirements.txt](file:///Users/karthikeyaunnam/Documents/MONTA/backend/requirements.txt); [orchestration/requirements.txt](file:///Users/karthikeyaunnam/Documents/MONTA/orchestration/requirements.txt)
- **Observability**: Custom metrics catalog with Prometheus-compatible `/api/v1/metrics` endpoint — [docs/layers-3-7.md](file:///Users/karthikeyaunnam/Documents/MONTA/docs/layers-3-7.md)
- **Evaluation**: Golden dataset (14 projects, 7 categories), story judge, calibration system — [docs/layers-3-7.md](file:///Users/karthikeyaunnam/Documents/MONTA/docs/layers-3-7.md)

### Inferences
- The system has a sophisticated model provider abstraction with resilience patterns (circuit breaker, fallback, bounded concurrency)
- The evaluation stack (golden dataset, story judge, calibration, memory experiment) suggests a focus on measurable quality and continuous improvement
- S3 integration implies cloud-native storage for media assets

### Gaps
- No examination of .env.example for full list of environment variables
- No analysis of actual API endpoints or authentication flow
- No examination of frontend dependencies (package.json)
- No analysis of the bank_project/ directory (purpose unclear)

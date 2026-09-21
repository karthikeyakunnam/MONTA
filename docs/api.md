# MONTA — API Documentation

Base URL: `http://localhost:8000/api/v1`

## Endpoints

### Upload
- `POST /upload/video` — Upload a single video
- `POST /upload/batch` — Upload multiple videos
- `GET /upload/status/{clip_id}` — Check upload status

### Projects
- `POST /projects/` — Create project
- `GET /projects/{id}` — Get project
- `GET /projects/` — List projects
- `PUT /projects/{id}` — Update project
- `DELETE /projects/{id}` — Delete project

### Prompts
- `POST /prompts/analyze` — Analyze editing prompt

### Timeline
- `POST /timeline/generate` — Generate timeline
- `GET /timeline/{project_id}` — Get timeline
- `PUT /timeline/{project_id}` — Update timeline

### Audio
- `POST /audio/generate` — Generate music
- `POST /audio/remix` — DJ remix
- `POST /audio/mashup` — Mashup tracks
- `POST /audio/tempo-match` — Tempo match
- `POST /audio/voiceover` — Generate voiceover

### Render
- `POST /render/start` — Start render
- `GET /render/status/{render_id}` — Render progress
- `POST /render/cancel/{render_id}` — Cancel render

### Export
- `POST /export/` — Export for platform
- `GET /export/{id}/download` — Download export

### WebSocket
- `WS /ws/{project_id}` — Real-time progress

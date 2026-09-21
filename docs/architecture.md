# MONTA — Architecture Documentation

See [implementation_plan.md] for the complete 14-layer architecture diagram and folder mapping.

## Layer Overview

| Layer | Name | Purpose |
|-------|------|---------|
| 1 | Experience Layer | User interface (Next.js, Electron) |
| 2 | Media Gateway | Upload, validate, store videos |
| 3 | Prompt Intelligence | Parse natural language → structured intent |
| 4 | Context Composer | Combine all context sources |
| 5 | Director Agent | Strategic planning (the CEO) |
| 6 | Video Intelligence | Scene, action, quality, emotion analysis |
| 7 | Story Architect | Narrative structure design |
| 8 | Style Engine | Visual style mapping |
| 9 | Timeline Generator | Edit sequence planning |
| 10 | Audio Studio | MONTA Audio (music, remix, FX) |
| 11 | Edit Executor | FFmpeg, OpenCV, Whisper execution |
| 12 | Critic System | Quality evaluation + retry loop |
| 13 | Render Farm | Multi-resolution rendering |
| 14 | Learning Layer | User preferences + feedback |

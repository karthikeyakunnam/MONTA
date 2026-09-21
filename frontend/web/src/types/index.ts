/** MONTA — TypeScript Types */

export interface ClipMetadata {
  fps: number;
  resolution: string;
  duration: number;
  clip_id: string;
  format: string;
  file_size_bytes: number;
}

export interface EditingIntent {
  genre: string;
  pacing: string;
  color: string;
  emotion: string;
  target: string;
}

export interface TimelineEntry {
  clip_id: string;
  start: number;
  end: number;
  transition: string;
}

export interface StoryAct {
  act_number: number;
  theme: string;
  clips: string[];
  mood: string;
}

export interface Project {
  id: string;
  title: string;
  status: string;
  raw_prompt?: string;
  parsed_intent?: EditingIntent;
  clips: ClipMetadata[];
  timeline?: TimelineEntry[];
}

export interface RenderProgress {
  render_id: string;
  status: string;
  progress_percent: number;
  current_step: string;
}

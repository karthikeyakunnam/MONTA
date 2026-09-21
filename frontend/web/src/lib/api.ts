/** MONTA — API Client Library */

const API_BASE = '/api/v1';

export async function apiRequest(endpoint: string, options?: RequestInit) {
  const res = await fetch(`${API_BASE}${endpoint}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// Upload
export const uploadVideo = (file: File) => {
  const formData = new FormData();
  formData.append('file', file);
  return fetch(`${API_BASE}/upload/video`, { method: 'POST', body: formData });
};

// Projects
export const createProject = (data: object) => apiRequest('/projects/', { method: 'POST', body: JSON.stringify(data) });
export const getProject = (id: string) => apiRequest(`/projects/${id}`);

// Prompts
export const analyzePrompt = (data: object) => apiRequest('/prompts/analyze', { method: 'POST', body: JSON.stringify(data) });

// Timeline
export const generateTimeline = (projectId: string) => apiRequest('/timeline/generate', { method: 'POST', body: JSON.stringify({ project_id: projectId }) });

// Render
export const startRender = (data: object) => apiRequest('/render/start', { method: 'POST', body: JSON.stringify(data) });
export const getRenderStatus = (id: string) => apiRequest(`/render/status/${id}`);

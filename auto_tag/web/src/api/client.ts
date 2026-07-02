const API_BASE = '/api'

async function fetchJSON<T>(url: string, init?: RequestInit, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${API_BASE}${url}`, {
    headers: { 'Content-Type': 'application/json' },
    signal,
    ...init,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(typeof err.detail === 'string' ? err.detail : err.detail?.error?.message || res.statusText)
  }
  return res.json()
}

async function fetchBlob(url: string, params?: Record<string, any>, signal?: AbortSignal): Promise<Blob> {
  const qs = params ? '?' + new URLSearchParams(
    Object.entries(params).filter(([_, v]) => v != null).map(([k, v]) => [k, String(v)])
  ).toString() : ''
  const res = await fetch(`${API_BASE}${url}${qs}`, { signal })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(typeof err.detail === 'string' ? err.detail : res.statusText)
  }
  return res.blob()
}

async function downloadJSON(url: string, params?: Record<string, any>, filename?: string): Promise<void> {
  const blob = await fetchBlob(url, params)
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = filename || 'export.json'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(a.href)
}

// --- Types ---

export interface HealthResponse {
  status: string
  chroma_path: string
  embedding_parent_exists: boolean
  chroma_parent_exists: boolean
  collection: string
}

export interface JobCreatePayload {
  input_dirs: string[]
  image_ls_files?: string[]
  work_dir?: string
  rotate_angle?: string | null
  b_yuv_image?: boolean
  mixed_yuv?: boolean
  yuv_type?: string
  image_height?: number
  image_width?: number
  skip_if_in_db?: boolean
}

export interface JobStatusResponse {
  job_id: string
  status: string
  processed: number
  total: number
  error?: string
  failed_count: number
  failed_so_far: number
  skip_in_db: number
  vlm_calls: number
  stage1_skips: number
  stage2_joins: number
}

export interface RecordsResponse {
  total: number | null
  offset: number
  limit: number
  items: Record<string, any>[]
  chroma_path: string
}

export interface ByPathResponse {
  found: boolean
  source?: string
  matched_path?: string
  cluster_id?: string
  is_cluster_center?: boolean
  cluster_center_path?: string
  cluster_center_labels?: Record<string, any>
  own_labels?: Record<string, any>
  effective_labels?: Record<string, any>
  duplicate_links?: any[]
  anchor_embedding_records?: any[]
  chroma_path?: string
  image_path?: string
  note?: string
}

export interface UpdateLabelsPayload {
  work_dir: string
  image_path: string
  labels: Record<string, any>
  mode: 'image_only' | 'with_cluster'
  image_width?: number
  image_height?: number
  yuv_type?: string
  b_yuv_image?: boolean
  mixed_yuv?: boolean
  rotate_angle?: string | null
}

export interface JobSummary {
  job_id: string
  status: string
  processed: number
  total: number
  error?: string | null
  failed_count: number | null
  failed_so_far: number
  skip_in_db: number
  vlm_calls: number
  stage1_skips: number
  stage2_joins: number
  work_dir: string
  log_dir: string
  created_at: number
}

export interface ListJobsResponse {
  jobs: JobSummary[]
  server_started_at: number
}

export interface StatsResponse {
  work_dir: string
  chroma_path: string
  log_dir: string
  embedding_record_count: number
  chroma_document_count: number
  cluster_count: number
  labeled_document_count: number
  duplicate_link_rows: number
  snapshot: Record<string, any> | null
  current_params: Record<string, any>
  has_snapshot: boolean
  has_config_diff: boolean
  has_relation_diff: boolean
  has_questions_diff: boolean
  enable_recompute_relations: boolean
  enable_rebuild_relations: boolean
  enable_reannotate: boolean
  param_diff_table: any[]
  config_path_effective?: string
}

export interface DuplicatesResponse {
  work_dir: string | null
  log_dir: string
  file: string
  total: number
  offset: number
  limit: number
  items: Record<string, any>[]
}

// --- API Client ---

export const api = {
  // Health
  health: () => fetchJSON<HealthResponse>('/health'),

  // Jobs
  createJob: (payload: JobCreatePayload) =>
    fetchJSON<{ job_id: string }>('/jobs', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  getJob: (jobId: string) => fetchJSON<JobStatusResponse>(`/jobs/${jobId}`),
  getJobLogs: (jobId: string, tail: number = 200) =>
    fetchJSON<{ job_id: string; lines: string[] }>(`/jobs/${jobId}/logs?tail=${tail}`),
  listJobs: () => fetchJSON<ListJobsResponse>('/jobs'),

  // Utils
  checkDirs: (dirs: string[]) =>
    fetchJSON<{ exist: string[]; not_exist: string[] }>('/utils/check_dirs', {
      method: 'POST',
      body: JSON.stringify({ dirs }),
    }),

  // Records
  listRecords: (params: { offset?: number; limit?: number; cluster_id?: string; work_dir?: string }) => {
    const qs = new URLSearchParams()
    if (params.offset != null) qs.set('offset', String(params.offset))
    if (params.limit != null) qs.set('limit', String(params.limit))
    if (params.cluster_id) qs.set('cluster_id', params.cluster_id)
    if (params.work_dir) qs.set('work_dir', params.work_dir)
    return fetchJSON<RecordsResponse>(`/records?${qs}`)
  },
  recordByPath: (params: { image_path: string; work_dir?: string }) => {
    const qs = new URLSearchParams({ image_path: params.image_path })
    if (params.work_dir) qs.set('work_dir', params.work_dir)
    return fetchJSON<ByPathResponse>(`/records/by_path?${qs}`)
  },
  previewImage: (params: {
    image_path: string; work_dir?: string; image_width?: number; image_height?: number;
    yuv_type?: string; b_yuv_image?: boolean; mixed_yuv?: boolean; rotate_angle?: string | null
  }) => fetchBlob('/records/preview', params),
  updateRecordLabels: (payload: UpdateLabelsPayload) =>
    fetchJSON<any>('/records/update_labels', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  // Duplicates
  listDuplicates: (params: { work_dir?: string; log_dir?: string; offset?: number; limit?: number }) => {
    const qs = new URLSearchParams()
    if (params.work_dir) qs.set('work_dir', params.work_dir)
    if (params.log_dir) qs.set('log_dir', params.log_dir)
    if (params.offset != null) qs.set('offset', String(params.offset))
    if (params.limit != null) qs.set('limit', String(params.limit))
    return fetchJSON<DuplicatesResponse>(`/duplicates?${qs}`)
  },

  // Database
  databaseStats: (params: { work_dir?: string; config_path?: string }) => {
    const qs = new URLSearchParams()
    if (params.work_dir) qs.set('work_dir', params.work_dir)
    if (params.config_path) qs.set('config_path', params.config_path)
    return fetchJSON<StatsResponse>(`/database/stats?${qs}`)
  },
  exportEmbeddings: (params: Record<string, any>) =>
    downloadJSON('/database/export_embeddings', params),
  exportDuplicates: (params: Record<string, any>) =>
    downloadJSON('/database/export_duplicates', params),
  exportCompactShared: (params?: { work_dir?: string }) =>
    downloadJSON('/database/export_compact_shared', params, 'auto_tag_compact_labels_shared.json'),
  exportCompactSlice: (params: { work_dir?: string; offset?: number; limit?: number }) =>
    downloadJSON('/database/export_compact_slice', params),
  exportCompactChunk: (params: { work_dir?: string; chunk_index?: number; chunk_size?: number }) =>
    downloadJSON('/database/export_compact_chunk', params),
  recomputeRelations: (body: { work_dir?: string }) =>
    fetchJSON<any>('/database/recompute_relations', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  rebuildRelations: (body: { work_dir?: string }) =>
    fetchJSON<any>('/database/rebuild_relations', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  reannotate: (body: {
    work_dir?: string; full_refresh?: boolean; incremental?: boolean; centers_only?: boolean
  }) => fetchJSON<any>('/database/reannotate', {
    method: 'POST',
    body: JSON.stringify(body),
  }),

  // Models
  getModels: () => fetchJSON<any>('/models'),
  resetCircuitBreaker: () => fetchJSON<any>('/models/reset', { method: 'POST' }),
  testModel: (modelName: string) =>
    fetchJSON<any>(`/models/test/${encodeURIComponent(modelName)}`, { method: 'POST' }),
  updateCircuitBreaker: (body: {
    time_window_seconds: number; failure_rate_threshold: number; cooldown_seconds: number
  }) => fetchJSON<any>('/models/circuit-breaker', {
    method: 'PUT',
    body: JSON.stringify(body),
  }),
  resetCircuitBreaker: () => fetchJSON<any>('/models/reset', { method: 'POST' }),
  resetModelCircuitBreaker: (modelName: string) =>
    fetchJSON<any>(`/models/reset/${encodeURIComponent(modelName)}`, { method: 'POST' }),
  testModel: (modelName: string) =>
    fetchJSON<any>(`/models/test/${encodeURIComponent(modelName)}`, { method: 'POST' }),
}
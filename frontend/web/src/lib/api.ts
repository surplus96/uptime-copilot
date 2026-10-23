export interface Health { status: string; llm_provider: string; llm_model: string; offline: boolean }
export interface EventRow { machine_id: number; severity: string; diagnosis: string; detected_at: string; evidence_at: string | null }
export interface Events { total: number; events: EventRow[]; offset?: number; limit?: number; summary?: { total: number; urgent: number; warning: number } }
export interface Simulator {
  running: boolean; dataset_now: string | null; sim_now: string | null; timezone: string;
  hours_per_tick: number; tick_seconds: number; last_error: string | null;
  consecutive_failures: number; has_stale_events: boolean;
  degrading: { machine_id: number; state: string; signal: string; drift_sigma: number; progress: number }[];
}
export interface Delivery { status?: string; display_id?: string; work_order_id?: number; error?: string }
export type AgentResult =
  | { status: 'pending_approval'; message: string; work_order?: string; work_order_data?: WorkOrderData | null; perspectives?: string[] }
  | { status: 'done'; result: string; work_order?: string; work_order_data?: WorkOrderData | null; cmms_delivery?: Delivery }
  | { status: 'running'; next?: string[] }
  | { status: 'failed'; error_type?: string }
  | { status: 'not_found' }
export interface InventoryRow {
  component: string; part_name: string; on_hand: number; lead_time_days: number;
  demand_30d: number; demand_90d: number; eol_status: string; eol_date: string | null;
}
export interface RagResult { answer: string; context?: string; verified: boolean; verified_reason?: string }
export interface AuditRow { ts: string; thread_id: string; event_type: string; target: string; summary: string; result: string; provider: string }

export class ApiError extends Error {
  constructor(message: string, public status?: number) { super(message); this.name = 'ApiError' }
}
export async function request<T>(path: string, body?: unknown, timeout = 15000): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)
  try {
    const response = await fetch(`/api${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal,
    })
    const data = await response.json().catch(() => null)
    if (!response.ok) {
      const detail = data?.reason ?? data?.detail
      const message = typeof detail === 'string' ? detail : Array.isArray(detail)
        ? detail.map((item: { msg?: string }) => item.msg ?? '입력을 확인하세요').join('; ')
        : `서버 요청 오류 (HTTP ${response.status})`
      throw new ApiError(message, response.status)
    }
    if (data === null) throw new ApiError('서버 응답 형식을 확인할 수 없습니다')
    return data as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (controller.signal.aborted) throw new ApiError('응답 시간이 초과됐습니다. 서버 처리 결과를 확인하세요.')
    throw new ApiError('서버에 연결하지 못했습니다. 연결 상태를 확인하세요.')
  } finally { clearTimeout(timer) }
}
export const api = {
  health: () => request<Health>('/health'),
  simulator: () => request<Simulator>('/simulator/status'),
  events: (params = new URLSearchParams({ limit: '100' })) => request<Events>(`/events?${params}`),
  status: (thread: string) => request<AgentResult>(`/agent/status/${encodeURIComponent(thread)}`),
  diagnose: (thread: string, message: string) => request<AgentResult>('/agent/query', { thread_id: thread, message }, 60000),
  resume: (thread: string, approved: boolean) => request<AgentResult>('/agent/resume', { thread_id: thread, approved }, 60000),
  retryDelivery: (thread: string) => request<{ cmms_delivery: Delivery }>('/agent/cmms/retry', { thread_id: thread }, 60000),
  inventory: () => request<{ rows: InventoryRow[] }>('/parts/inventory_risk', undefined, 60000),
  rag: (question: string) => request<RagResult>('/rag/query', { question }, 60000),
  audit: (params: URLSearchParams) => request<{ events: AuditRow[] }>(`/audit_log?${params}`),
}
export const deliveryLabels: Record<string, string> = {
  sent: '전송 완료', partial_tasks: '작업지시서 생성됨 · 점검 항목 일부 미등록',
  created: '작업지시서 생성됨', creation_unknown: '생성 결과 미확인 · Atlas에서 확인 필요',
  skipped_unconfigured: '미설정 · 전송 안 함', blocked_offline: '오프라인 정책으로 차단',
  blocked_insecure_url: '평문 HTTP 주소로 전송 차단', blocked_missing_tasks: '점검 절차가 없어 전송 차단',
  failed: '전송 실패', 성공: '성공', 실패: '실패', 차단: '차단',
}
export const eventLabels: Record<string, string> = {
  llm_call: 'LLM 호출', tool_call: '도구 호출', approval: '승인', rejection: '반려',
  external_push: '외부 전송', blocked_by_offline: '오프라인 차단',
}
export function displayTime(value?: string | null) { return value ? value.replace('T', ' ').replace(/\.\d+$/, '') : '확인되지 않음' }
export function errorMessage(error: unknown) { return error instanceof Error ? error.message : '요청을 처리하지 못했습니다' }

export interface Freshness { status: 'missing' | 'lagging' | 'aligned'; dataset_lag_hours: number | null; wall_clock_lag_hours: number | null }
export interface MachineRow { machine_id: number; model: string; age: number; latest_at: string | null; freshness: Freshness; sensors: Record<string, number | null> }
export interface MachineList { machines: MachineRow[]; total: number; models: string[]; dataset_now: string | null; queried_at: string; timezone: string }
export interface TelemetryPoint { datetime: string; sample_count: number; volt: number | null; rotate: number | null; pressure: number | null; vibration: number | null; [key: string]: string | number | null }
export interface Telemetry { machine_id: number; model: string; age: number; dataset_now: string | null; latest_at: string | null; freshness: Freshness; queried_at: string; timezone: string; hours: number; bucket_hours: number; aggregation: string; unique_samples: number; expected_samples: number; duplicate_samples: number; window_start: string | null; window_end: string | null; points: TelemetryPoint[] }
export interface ApprovalRow { thread_id: string; machine_id: number | null; priority: string | null; updated_at: string | null }
export interface Approvals { total: number; approvals: ApprovalRow[]; offset: number; limit: number }
export interface WorkOrderData {
  version: 1; machine_id: number | null; severity: string | null; priority: string | null; recommend_shutdown: boolean | null; priority_reasons: string[];
  components: { component: string; evidence: string; manual: string; action: string; steps: string[];
    procurement: { part_no: string | null; on_hand: number | null; min_stock: number | null; lead_time_days: number | null; needs_procurement: boolean | null; eol_status: string | null; eol_date: string | null } | null }[];
}
export const operationsApi = {
  machines: (params: URLSearchParams) => request<MachineList>(`/machines?${params}`),
  telemetry: (id: number, hours: number, bucket: number) => request<Telemetry>(`/machines/${id}/telemetry?hours=${hours}&bucket_hours=${bucket}`),
  approvals: (offset = 0, limit = 20) => request<Approvals>(`/agent/approvals?offset=${offset}&limit=${limit}`),
}

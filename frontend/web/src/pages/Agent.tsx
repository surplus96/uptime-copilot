import { useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, ClipboardCheck, RefreshCw, Send, ShieldCheck, Sparkles } from 'lucide-react'
import { api, ApiError, deliveryLabels, type AgentResult, type WorkOrderData } from '../lib/api'
import { DiagnosisContext } from '../components/DiagnosisContext'
import { useHealth } from '../lib/queries'
import { Badge, Button, Card, Confirm, Empty, ErrorNotice, Notice, PageTitle } from '../components/ui'

type Action = { kind: 'query'; thread: string; message: string } | { kind: 'approve' | 'reject' | 'retry'; thread: string }
// Store only the action type keyed by ID. Never persist diagnosis content or work orders.
const storageKey = (thread: string) => `uptime:unconfirmed:${thread}`
function marker(thread: string) { try { return sessionStorage.getItem(storageKey(thread)) } catch { return null } }
function mark(thread: string, kind?: string) { try { if (kind) sessionStorage.setItem(storageKey(thread), kind); else sessionStorage.removeItem(storageKey(thread)) } catch { /* URL still permits server recovery when storage is unavailable. */ } }
function deliveryDescription(result: Extract<AgentResult, { status: 'done' }>) {
  const delivery = result.cmms_delivery
  if (delivery?.status) return deliveryLabels[delivery.status] ?? delivery.status
  if (result.result.startsWith('[긴급 반려됨]')) return '반려됨 · 전송하지 않음'
  if (result.result.startsWith('[사전 경보 - 예방 조치 권장]')) return '주의 진단 · 자동 전송 대상 아님'
  if (result.result.startsWith('[긴급 승인됨]') || delivery?.display_id || delivery?.work_order_id) return '응답에 전송 결과 없음 · 감사 로그에서 확인'
  if (!result.work_order?.trim() && !result.work_order_data?.components.length) return '전송 대상 없음 · 작업지시서가 생성되지 않은 진단입니다.'
  return '응답에 전송 결과 없음 · 감사 로그에서 확인'
}
export function WorkOrder({ text, data }: { text: string; data?: WorkOrderData | null }) {
  if (data?.version === 1 && data.components.length) return <div className="structured-order"><div className="section-label"><ClipboardCheck size={17} />작업지시서 · 설비 #{data.machine_id}</div><div className="actions"><Badge tone="warning">{data.severity ?? '긴급도 미확인'}</Badge><Badge>{data.priority ?? '우선순위 미지정'}</Badge><Badge>정지 권고: {data.recommend_shutdown === null ? '미확인' : data.recommend_shutdown ? '예' : '아니오'}</Badge></div>{data.priority_reasons.length > 0 && <ul className="order-reasons">{data.priority_reasons.map((reason, i) => <li key={i}>{reason}</li>)}</ul>}{data.components.map(component => <div className="work-order" key={component.component}><h3>{component.component}</h3><dl><dt>관측 근거</dt><dd className="preserve-text">{component.evidence || '반환된 근거 없음'}</dd><dt>매뉴얼 근거</dt><dd className="preserve-text">{component.manual || '반환된 근거 없음'}</dd><dt>조치사항</dt><dd>{component.steps.length ? <ol className="order-steps">{component.steps.map((step, i) => <li key={i}>{step}</li>)}</ol> : <p className="preserve-text">{component.action || '반환된 절차 없음'}</p>}</dd><dt>부품 조달 (합성 데이터)</dt><dd>{component.procurement ? <><p>{component.procurement.part_no ?? component.component} · 재고 {component.procurement.on_hand ?? '미확인'} / 최소 {component.procurement.min_stock ?? '미확인'} · 리드타임 {component.procurement.lead_time_days ?? '미확인'}일</p><Badge tone={component.procurement.needs_procurement ? 'warning' : 'neutral'}>{component.procurement.needs_procurement === null ? '조달 필요 여부 미확인' : component.procurement.needs_procurement ? '조달 검토 필요' : '조달 필요 신호 없음'}</Badge>{component.procurement.eol_date && <p>단종 상태 {component.procurement.eol_status ?? '미확인'} · {component.procurement.eol_date}</p>}</> : '조달 정보 없음'}</dd></dl></div>)}<p className="caption">진단 우선순위와 조달 긴급도는 별개입니다. 대체품을 포함한 전체 조달 설명은 원문에서 확인하세요.</p><details><summary>작업지시서 원문</summary><p className="preserve-text">{text}</p></details></div>
  return <div className="work-order"><div className="section-label"><ClipboardCheck size={17} />작업지시서</div><div className="preserve-text">{text}</div><p className="caption">조달 상태는 합성 부품 데이터 기준입니다. 진단 긴급도와 조달 긴급도는 별개입니다.</p></div>
}
export default function AgentPage() {
  const [params, setParams] = useSearchParams()
  const thread = params.get('thread') ?? '', machine = params.get('machine') ?? '', previous = params.get('previous') ?? ''
  const [message, setMessage] = useState(machine ? `${machine}번 설비 상태를 진단해줘` : '')
  const [feedback, setFeedback] = useState('')
  const [unconfirmedThreads, setUnconfirmedThreads] = useState<Record<string, boolean>>({})
  function setUnconfirmed(id: string, value: boolean) { setUnconfirmedThreads(previous => ({ ...previous, [id]: value })) }
  const client = useQueryClient(), health = useHealth()
  const operation = useMutation({
    mutationFn: async (action: Action): Promise<AgentResult> => {
      if (action.kind === 'query') return api.diagnose(action.thread, action.message)
      if (action.kind === 'approve' || action.kind === 'reject') return api.resume(action.thread, action.kind === 'approve')
      const response = await api.retryDelivery(action.thread)
      const previous = client.getQueryData<AgentResult>(['agent', action.thread])
      if (previous?.status !== 'done') throw new Error('진단 상태를 다시 확인하세요')
      return { ...previous, cmms_delivery: response.cmms_delivery }
    },
    onMutate: action => { mark(action.thread, action.kind); setUnconfirmed(action.thread, true); setFeedback('') },
    onSuccess: (result, action) => {
      mark(action.thread); setUnconfirmed(action.thread, false); client.setQueryData(['agent', action.thread], result)
      if (action.kind === 'approve' && result.status === 'done' && result.result.startsWith('[긴급 반려됨]')) setFeedback('서버에는 이미 반려된 건으로 기록되어 있습니다. 아래는 서버에 기록된 결과입니다.')
      if (action.kind === 'reject' && result.status === 'done' && result.result.startsWith('[긴급 승인됨]')) setFeedback('서버에는 이미 승인된 건으로 기록되어 있습니다. 아래는 서버에 기록된 결과입니다.')
      void client.invalidateQueries({ queryKey: ['audit'] }); void client.invalidateQueries({ queryKey: ['approvals'] })
    },
    onError: (error, action) => {
      if (action.kind === 'query' && error instanceof ApiError && (error.status === 400 || error.status === 422)) {
        mark(action.thread); setUnconfirmed(action.thread, false); client.setQueryData(['agent', action.thread], { status: 'failed', error_type: '요청 거부' })
      }
    },
  })
  const busy = operation.isPending && operation.variables?.thread === thread
  const status = useQuery({ queryKey: ['agent', thread], queryFn: () => api.status(thread), enabled: !!thread && !busy,
    refetchInterval: query => query.state.data?.status === 'running' ? 5000 : false,
    retry: false, staleTime: 0,
  })
  const result = status.data, unconfirmed = unconfirmedThreads[thread] ?? !!marker(thread)
  const active = busy || result?.status === 'running' || result?.status === 'pending_approval' || (unconfirmed && result?.status !== 'done' && result?.status !== 'failed')
  async function checkStatus() {
    setFeedback('')
    const response = await status.refetch()
    if (response.data && !response.isError) {
      if (response.data.status === 'pending_approval') {
        mark(thread); setUnconfirmed(thread, false)
        setFeedback('서버에서 승인 대기 상태를 확인했습니다. 작업지시서를 다시 검토한 뒤 결정하세요.')
      } else if (response.data.status === 'done' || response.data.status === 'failed') { mark(thread); setUnconfirmed(thread, false) }
    }
  }
  function submit(event: FormEvent) {
    event.preventDefault()
    if (!message.trim() || active || status.isFetching || status.isError) return
    const newThread = crypto.randomUUID()
    client.setQueryData(['agent', newThread], { status: 'running' })
    setParams({ ...(machine ? { machine } : {}), ...(previous ? { previous } : {}), thread: newThread })
    operation.mutate({ kind: 'query', thread: newThread, message: message.trim() })
  }
  function prepareLatestDiagnosis(id: number) {
    if (busy || unconfirmed || status.isFetching || status.isError) return
    setMessage(`${id}번 설비의 최신 상태를 진단해줘`)
    setFeedback('')
    setParams({ machine: String(id), previous: thread })
  }
  const canDecide = !busy && !unconfirmed && !status.isFetching && !status.isError
  return <><PageTitle eyebrow="AI MAINTENANCE COPILOT" title="AI 설비 진단" actions={<Link className="button button-secondary" to="/events">이벤트 목록 <ArrowRight size={16} /></Link>}>설비 이력과 매뉴얼을 바탕으로 진단하고, 작업지시서를 검토하세요.</PageTitle>
    <div className="agent-grid"><Card className="conversation"><div className="card-heading"><h2><Sparkles size={19} />설비 에이전트</h2>{machine && <Badge>설비 #{machine}</Badge>}</div>
      <form onSubmit={submit} className="diagnosis-form"><label htmlFor="diagnosis-message">설비 번호와 증상</label><textarea id="diagnosis-message" value={message} onChange={e => setMessage(e.target.value)} rows={4} placeholder="예: 64번 설비에서 오류가 났는데 뭐가 문제야?" /><div className="form-bottom"><span className="caption">새 요청은 별도 진단 URL로 생성됩니다.</span><Button disabled={!message.trim() || active || status.isFetching || status.isError}><Send size={16} />진단 요청</Button></div></form>
      {previous && previous !== thread && <Notice><Link className="text-link" to={`/agent?thread=${encodeURIComponent(previous)}${machine ? `&machine=${encodeURIComponent(machine)}` : ''}`}>이전 진단 보기</Link>{!thread && <p>기존 진단은 보존됩니다. 입력된 질문을 확인한 뒤 ‘진단 요청’을 누르면 별도의 진단이 생성됩니다.</p>}</Notice>}
      {!!thread && result && (result.status === 'done' || result.status === 'pending_approval') && <DiagnosisContext result={result} machineHint={machine} canPrepare={!busy && !unconfirmed && !status.isFetching && !status.isError} onPrepare={prepareLatestDiagnosis} />}
      {!thread && <Empty><Sparkles size={30} /><h3>어떤 설비를 살펴볼까요?</h3><p>설비 번호와 증상을 입력하거나 예시로 시작하세요.</p><div className="example-questions">{['64번 설비에서 오류 났는데 뭐가 문제야?', '5번 설비 상태 확인해줘', '1번 설비 다음 점검은 언제야?'].map(q => <Button key={q} variant="secondary" onClick={() => setMessage(q)}>{q}<ArrowUpIcon /></Button>)}</div></Empty>}
      {thread && <><div className="thread-bar"><span>진단 ID <code>{thread}</code></span><Button variant="ghost" onClick={() => void checkStatus()} disabled={busy || status.isFetching}><RefreshCw size={15} />상태 확인</Button></div><p className="caption">현재 URL로 이 진단을 다시 열 수 있습니다. 화면 이동·닫기는 서버 요청을 취소하지 않습니다.</p></>}
      <ErrorNotice error={operation.variables?.thread === thread ? operation.error : null} /><ErrorNotice error={status.error} />{feedback && <Notice>{feedback}</Notice>}
      {busy && <Notice>서버가 요청을 처리 중입니다. 로컬 모델에서는 수 분이 걸릴 수 있습니다. 요청은 자동 재전송하지 않습니다.</Notice>}
      {!busy && unconfirmed && result?.status !== 'done' && result?.status !== 'failed' && <Notice tone="warning">이전 요청의 결과가 확인되지 않았습니다. ‘상태 확인’으로 서버 기록을 확인한 뒤 다음 작업을 진행하세요.</Notice>}
      {!busy && result?.status === 'running' && <Notice>서버 기록상 처리 중입니다. 5초마다 상태를 확인합니다. 서버 중단 시에도 이 상태가 남을 수 있습니다.</Notice>}
      {!busy && result?.status === 'not_found' && <Notice tone="warning">이 ID의 서버 기록을 찾지 못했습니다. 직전 요청이 전달되지 않았다고 단정할 수 없습니다. 잠시 뒤 상태를 다시 확인하세요.</Notice>}
      {!busy && result?.status === 'failed' && <Notice tone="error">진단이 중단됐습니다 ({result.error_type ?? '원인 미확인'}). 내용을 확인한 뒤 새 진단을 요청하세요.</Notice>}
      {result?.status === 'done' && <div className="answer"><div className="section-label"><ShieldCheck size={18} />서버 처리 결과</div><p className="preserve-text">{result.result}</p>{result.work_order && <WorkOrder text={result.work_order} data={result.work_order_data} />}
        <div className="delivery"><h3>외부 연동 결과</h3><p>CMMS: {deliveryDescription(result)}</p>{result.cmms_delivery?.display_id && <p>작업지시서 {result.cmms_delivery.display_id}</p>}
          {result.cmms_delivery?.status === 'partial_tasks' && <Confirm label="점검 항목 등록 재시도" title="미등록 점검 항목을 다시 전송할까요?" disabled={!canDecide} onConfirm={() => operation.mutate({ kind: 'retry', thread })}>기존 CMMS 작업지시서의 점검 항목 등록을 재시도합니다.</Confirm>}
          {unconfirmed && <Notice tone="warning">연동 요청 결과 미확인: 상태 확인 후 결과를 검토하세요.</Notice>}<Link className="text-link" to={`/audit?thread=${encodeURIComponent(thread)}`}>이 진단의 감사 로그 <ArrowRight size={15} /></Link>
        </div></div>}
    </Card><div className="stack"><Card className={result?.status === 'pending_approval' ? 'approval-card' : ''}><p className="eyebrow">HUMAN IN THE LOOP</p><h2>작업지시서 검토·승인</h2>{result?.status === 'pending_approval' ? <><Badge tone="warning">승인 대기</Badge>{result.work_order ? <WorkOrder text={result.work_order} data={result.work_order_data} /> : <p className="preserve-text">{result.message}</p>}
      <h3>안전·생산·정비 관점</h3><p className="caption">근거 확인 없이 생성된 AI 참고 의견입니다.</p>{result.perspectives?.length ? result.perspectives.map((p, i) => <div className="perspective preserve-text" key={i}>{p}</div>) : <Notice tone="warning">관점 의견이 없습니다. 원본 메시지를 검토하세요.</Notice>}
      <details><summary>원본 메시지</summary><p className="preserve-text">{result.message}</p></details>
      <p className="caption">{health.isError || !health.data ? '연결 상태를 확인하지 못해 외부 전송 가능 여부를 알 수 없습니다.' : health.data.offline ? '오프라인 모드: Slack은 차단됩니다. 허용된 내부 주소의 CMMS만 등록을 시도합니다.' : '승인하면 설정된 Slack 알림과 CMMS 등록을 시도합니다.'} 반려 시 전송하지 않습니다.</p>
      <div className="actions"><Confirm label="승인" title="이 작업지시서를 승인할까요?" disabled={!canDecide} onConfirm={() => operation.mutate({ kind: 'approve', thread })}>작업지시서를 승인하고 설정된 외부 시스템으로 전송을 시도합니다. 실제 전송 결과는 별도로 확인해야 합니다.</Confirm><Confirm label="반려" danger title="이 작업지시서를 반려할까요?" disabled={!canDecide} onConfirm={() => operation.mutate({ kind: 'reject', thread })}>작업지시서를 반려합니다. 외부 전송은 수행하지 않습니다.</Confirm></div>
    </> : <Empty>긴급 진단의 작업지시서가 생성되면<br />이곳에서 검토하고 결정합니다.</Empty>}</Card><Notice>모델의 위험 점수는 보정된 고장 확률이 아닙니다. 작업지시서와 실제 설비 상태를 함께 확인하세요.</Notice></div></div>
  </>
}
function ArrowUpIcon() { return <ArrowRight size={14} /> }

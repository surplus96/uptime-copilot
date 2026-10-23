import { lazy, Suspense, useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, BookOpen, Pause, Play, RefreshCw, Search, Zap } from 'lucide-react'
import { api, deliveryLabels, displayTime, eventLabels, request } from '../lib/api'
import { failureCountdown, simulationStateLabel } from '../lib/simulation'
import { useInventory, useSimulator } from '../lib/queries'
import { Badge, Button, Card, Confirm, Empty, ErrorNotice, Loading, Notice, PageTitle } from '../components/ui'

const InventoryChart = lazy(() => import('../components/Charts').then(module => ({ default: module.InventoryChart })))

export function InventoryPage() {
  const query = useInventory()
  return <><PageTitle eyebrow="PARTS & PROCUREMENT" title="부품·조달" actions={<Button variant="secondary" disabled={query.isFetching} onClick={() => void query.refetch()}><RefreshCw size={16} />새로고침</Button>}>예상 수요와 조달 리드타임을 비교해 부족 위험을 확인하세요.</PageTitle>
    <Notice tone="warning">재고·리드타임은 합성 데이터입니다. 수요는 과거 교체 이력 기반 추정치이며 발주 후 입고 대기 물량은 반영하지 않습니다.</Notice>{query.data && !query.isError && query.data.rows.length > 0 && <Card><h2>재고와 수요 비교</h2><p className="caption">수요는 올림한 추정 수량입니다. 30일·90일 수요는 각각의 누적 수요이며 합산하지 않습니다.</p><Suspense fallback={<Loading />}><InventoryChart rows={query.data.rows} /></Suspense></Card>}<ErrorNotice error={query.error} />{query.isPending ? <Loading /> : query.data?.rows.length === 0 ? <Empty>표시할 부품이 없습니다. 위험이 없다는 뜻은 아닙니다.</Empty> : <div className="inventory-grid">{query.data?.rows.map(row => {
      const d30 = Math.ceil(row.demand_30d), d90 = Math.ceil(row.demand_90d)
      const days = row.demand_90d > 0 ? Math.floor(row.on_hand / (row.demand_90d / 90)) : null
      const short30 = Math.max(0, d30 - row.on_hand), short90 = Math.max(0, d90 - row.on_hand)
      const late = days !== null && row.lead_time_days > days
      return <Card key={row.component}><div className="card-heading"><div><p className="eyebrow">{row.component}</p><h2>{row.part_name}</h2></div><Badge tone={short30 > 0 || (short90 > 0 && late) ? 'danger' : short90 > 0 ? 'warning' : 'success'}>{short30 > 0 ? '30일 부족 예상' : short90 > 0 ? '재주문 필요' : '추정 재고 여유'}</Badge></div>
        <div className="stock-metrics"><div><span>현재 재고</span><strong>{row.on_hand}<small>개</small></strong></div><div><span>30일 수요</span><strong>약 {d30}<small>개</small></strong></div><div><span>90일 수요</span><strong>약 {d90}<small>개</small></strong></div></div>
        <dl className="inline-dl"><dt>조달 리드타임</dt><dd>{row.lead_time_days}일</dd><dt>재고 소진 단순 추정</dt><dd>{days === null ? '수요 없음' : `약 ${days}일 후`}</dd></dl>
        {short30 > 0 ? <Notice tone="warning">30일 내 약 {short30}개 부족 예상.{row.lead_time_days > 30 ? ' 지금 발주해도 30일 내 입고가 어렵습니다.' : ' 발주·입고 일정을 확인하세요.'}</Notice> : short90 > 0 ? <Notice tone={late ? 'warning' : 'info'}>90일 내 약 {short90}개 부족 예상. {late ? '현재 리드타임으로는 추정 소진 전 입고가 어렵습니다.' : `약 ${days === null ? '—' : days - row.lead_time_days}일 내 발주가 필요합니다.`}</Notice> : null}
        {['경과', '임박'].includes(row.eol_status) && <Notice tone="warning">{row.eol_status === '경과' ? '단종됨' : '단종 임박'} · {row.eol_date}</Notice>}<p className="caption">수요를 올림한 수량으로 부족분을 표시합니다. 소진 시점은 90일 수요가 일정하다는 단순 추정입니다.</p></Card>
    })}</div>}
  </>
}
export function ManualPage() {
  const [question, setQuestion] = useState(''), [submitted, setSubmitted] = useState('')
  const mutation = useMutation({ mutationFn: api.rag, onMutate: value => setSubmitted(value) })
  function submit(e: FormEvent) { e.preventDefault(); if (question.trim() && !mutation.isPending) mutation.mutate(question.trim()) }
  return <><PageTitle eyebrow="MAINTENANCE KNOWLEDGE" title="매뉴얼 검색">정비 매뉴얼에서 답변과 근거를 함께 확인하세요.</PageTitle><Card className="manual-search"><BookOpen size={28} className="text-orange" /><h2>어떤 내용이 궁금하신가요?</h2><form onSubmit={submit} className="filters"><input aria-label="매뉴얼 질문" className="grow" value={question} onChange={e => setQuestion(e.target.value)} placeholder="예: comp3 부품은 어떤 증상과 관련 있어?" /><Button disabled={!question.trim() || mutation.isPending}><Search size={17} />검색</Button></form><p className="caption">설비 진단은 AI 진단 메뉴를 이용하세요. 적재된 문서 범위에서만 검색합니다.</p></Card>
    {mutation.isPending && <Loading />}<ErrorNotice error={mutation.error} />{mutation.data && !mutation.isPending && !mutation.isError && <div className="manual-results"><Card><p className="eyebrow">ANSWER</p><h2>{submitted}</h2><Badge tone={mutation.data.verified ? 'success' : 'warning'}>{mutation.data.verified ? '근거 검증 통과' : '검증 미완료'}</Badge>{!mutation.data.verified && <Notice tone="warning">{mutation.data.verified_reason ?? '답변 검증을 완료하지 못했습니다.'}</Notice>}<p className="preserve-text">{mutation.data.answer}</p></Card><Card><p className="eyebrow">SOURCE CONTEXT</p><h2>검색 근거</h2>{mutation.data.context ? <p className="preserve-text source-text">{mutation.data.context}</p> : <Empty>반환된 근거 문맥이 없습니다.</Empty>}</Card></div>}
  </>
}
export function AuditPage() {
  const [params, setParams] = useSearchParams()
  const [thread, setThread] = useState(params.get('thread') ?? ''), [type, setType] = useState(params.get('type') ?? '')
  const appliedThread = params.get('thread') ?? '', appliedType = params.get('type') ?? ''
  const query = useQuery({ queryKey: ['audit', appliedThread, appliedType], queryFn: () => {
    const apiParams = new URLSearchParams({ limit: '200' }); if (appliedThread) apiParams.set('thread_id', appliedThread); if (appliedType) apiParams.set('event_type', appliedType)
    return api.audit(apiParams)
  }, refetchInterval: 10000 })
  return <><PageTitle eyebrow="AUDIT & INTEGRATIONS" title="감사·연동" actions={<Button variant="secondary" disabled={query.isFetching} onClick={() => void query.refetch()}><RefreshCw size={16} />새로고침</Button>}>승인과 실제 외부 전송 결과를 각각 확인하세요.</PageTitle><Card><form className="filters" onSubmit={e => { e.preventDefault(); setParams({ ...(thread.trim() ? { thread: thread.trim() } : {}), ...(type ? { type } : {}) }) }}><label className="grow">진단 ID<input value={thread} onChange={e => setThread(e.target.value)} placeholder="진단 URL에 표시된 ID" /></label><label>기록 유형<select value={type} onChange={e => setType(e.target.value)}><option value="">전체</option>{Object.entries(eventLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><Button>필터 적용</Button></form><p className="caption">최근 최대 200건 · 시각은 백엔드 서버 로컬 시간(시간대 정보 없음) · 10초마다 갱신</p>
    <ErrorNotice error={query.error} />{query.isPending ? <Loading /> : query.data?.events.length ? <><div className="table-scroll"><table><thead><tr><th>시각</th><th>유형</th><th>대상 / 요약</th><th>결과</th><th>진단 ID</th></tr></thead><tbody>{query.data.events.map((row, i) => <tr key={i}><td className="numeric">{displayTime(row.ts)}</td><td>{eventLabels[row.event_type] ?? row.event_type}</td><td><strong>{row.target}</strong><p className="audit-summary">{row.summary}</p></td><td>{deliveryLabels[row.result] ?? row.result}</td><td>{row.thread_id ? <Link className="text-link thread-link" to={`/agent?thread=${encodeURIComponent(row.thread_id)}`}>{row.thread_id}<ArrowRight size={14} /></Link> : '—'}</td></tr>)}</tbody></table></div>{query.data.events.length >= 200 && <Notice>최근 200건만 표시합니다. 진단 ID나 유형으로 범위를 좁혀 보세요.</Notice>}</> : !query.isError && <Empty>조건에 맞는 기록이 없습니다.</Empty>}</Card><Notice>차단은 정책상 전송하지 않음, 실패는 전송 시도 중 오류, 미설정은 전송을 시도하지 않은 상태입니다. 승인 성공이 외부 전송 성공을 뜻하지는 않습니다.</Notice></>
}
export function SimulatorPage() {
  const query = useSimulator(), client = useQueryClient()
  const [machine, setMachine] = useState('64'), [signal, setSignal] = useState(''), [feedback, setFeedback] = useState('')
  const mutation = useMutation({ mutationFn: async (action: 'start' | 'stop' | 'reset' | 'inject') => {
    const result = await request<{ error?: string }>(`/simulator/${action}`, action === 'inject' ? { machine_id: Number(machine), signal: signal || null } : {}, 30000)
    if (result.error) throw new Error(result.error)
    return action
  }, onSuccess: action => setFeedback(({ start: '시뮬레이터를 시작했습니다.', stop: '시뮬레이터를 정지했습니다.', reset: '시뮬레이션과 이벤트를 초기화했습니다.', inject: '열화를 주입했습니다. 다음 틱에서 상태가 변합니다.' })[action]),
  onSettled: () => { void client.invalidateQueries({ queryKey: ['simulator'] }); void client.invalidateQueries({ queryKey: ['events'] }); void client.invalidateQueries({ queryKey: ['inventory'] }) } })
  const disabled = mutation.isPending || !query.data || query.isError
  const validMachine = /^\d+$/.test(machine) && Number(machine) >= 1 && Number(machine) <= 100
  return <><PageTitle eyebrow="SIMULATION LAB" title="시뮬레이터">시뮬레이션 데이터를 진행하고 열화를 주입해 진단 흐름을 살펴보세요.</PageTitle><ErrorNotice error={query.error || mutation.error} />{feedback && <Notice>{feedback}</Notice>}{query.isPending ? <Loading /> : <div className="simulator-grid"><Card><div className="card-heading"><h2>실행 상태</h2><Badge tone={query.isError ? 'danger' : query.data?.running ? 'success' : 'neutral'}>{query.isError ? '미확인' : query.data?.running ? '실행 중' : '정지'}</Badge></div><dl><dt>데이터 기준 시각</dt><dd>{displayTime(query.data?.dataset_now)}</dd><dt>시뮬레이션 최신 시각</dt><dd>{displayTime(query.data?.sim_now)}</dd><dt>진행 속도</dt><dd>{query.data ? `${query.data.tick_seconds}초마다 ${query.data.hours_per_tick}시간` : '미확인'}</dd></dl><div className="actions"><Button disabled={disabled || query.data?.running} onClick={() => mutation.mutate('start')}><Play size={16} />시작</Button><Button variant="secondary" disabled={disabled || !query.data?.running} onClick={() => mutation.mutate('stop')}><Pause size={16} />정지</Button></div>{query.data?.last_error && <Notice tone="error">틱 오류: {query.data.last_error} · 연속 실패 {query.data.consecutive_failures}회</Notice>}{query.data?.has_stale_events && <Notice tone="warning">현재 열화가 없는 이전 감지 이벤트가 남아 있습니다.</Notice>}</Card>
    <Card><h2>설비 열화 주입</h2><p className="muted">선택한 설비의 시뮬레이션 상태와 정비 이력에 영향을 줍니다.</p><div className="filters"><label>설비 번호 (1~100)<input type="number" min="1" max="100" step="1" value={machine} onChange={e => setMachine(e.target.value)} /></label><label>센서<select value={signal} onChange={e => setSignal(e.target.value)}><option value="">자동 선택</option><option value="volt">전압</option><option value="rotate">회전수</option><option value="pressure">압력</option><option value="vibration">진동</option></select></label></div><Confirm label={<><Zap size={16} />열화 주입</>} title="설비에 열화를 주입할까요?" disabled={disabled || !validMachine} onConfirm={() => mutation.mutate('inject')}>설비 #{machine}에 열화를 주입합니다. 실제 센서 수집이 아닌 시뮬레이션 동작입니다.</Confirm></Card>
    <Card className="full-span"><h2>시뮬레이션 단계</h2><Notice>‘전조 오류 발생’은 실제 고장 전 단계이며 감지 이벤트는 ‘주의’일 수 있습니다. 예정 고장 시각에 고장·자동 정비 기록이 생성되면 시뮬레이션은 열화 없는 상태로 복귀합니다. 정지 중에는 남은 시뮬레이션 시간이 줄어들지 않습니다.</Notice>{query.data?.degrading.length ? <div className="table-scroll"><table><thead><tr><th>설비</th><th>단계</th><th>센서</th><th>열화 편차</th><th>예정 고장까지 (시뮬레이션 시간)</th><th /></tr></thead><tbody>{query.data.degrading.map(row => <tr key={row.machine_id}><td>#{row.machine_id}</td><td>{simulationStateLabel(row.state)}</td><td>{({ volt: '전압', rotate: '회전수', pressure: '압력', vibration: '진동' } as Record<string, string>)[row.signal] ?? row.signal}</td><td>{row.drift_sigma}σ</td><td>{failureCountdown(row.hours_until_failure)}<p className="caption">{displayTime(row.failure_due_at)}</p></td><td><Link className="text-link" to={`/agent?machine=${row.machine_id}`}>진단 <ArrowRight size={14} /></Link></td></tr>)}</tbody></table></div> : <Empty>표시할 열화 상태가 없습니다.</Empty>}</Card>
    <Card className="full-span"><h2>실제 고장 발생 기록 (시뮬레이션)</h2><p className="caption">운영 연도 내 최근 최대 20건. 아래는 고장 기록이며 현재 시뮬레이션 단계와 다를 수 있습니다. 자동 정비 이력은 실제 현장의 수리 완료를 뜻하지 않습니다.</p>{query.data?.recent_failures?.length ? <div className="table-scroll"><table><thead><tr><th>설비</th><th>고장 발생 시각</th><th>부품</th><th /></tr></thead><tbody>{query.data.recent_failures.map((row, i) => <tr key={`${row.machine_id}-${row.datetime}-${i}`}><td>#{row.machine_id}</td><td className="numeric">{displayTime(row.datetime)}</td><td>{row.component}</td><td><Link className="text-link" to={`/agent?machine=${row.machine_id}`}>진단 <ArrowRight size={14} /></Link></td></tr>)}</tbody></table></div> : <Empty>{query.data?.recent_failures ? '운영 연도 내 시뮬레이션 고장 기록이 없습니다.' : '서버에 고장 기록 조회 정보가 없습니다.'}</Empty>}</Card>
    <Card className="full-span danger-zone"><div><h2>시뮬레이터 리셋</h2><p className="muted">시뮬레이션 데이터와 전체 감지·완료 이벤트를 삭제합니다. 수동 스캔 결과도 포함됩니다.</p></div><Confirm danger label="전체 초기화" title="시뮬레이션과 이벤트를 초기화할까요?" disabled={disabled} onConfirm={() => mutation.mutate('reset')}>이 작업은 되돌릴 수 없습니다. 시뮬레이션 데이터와 수동 스캔을 포함한 감지·완료 이벤트가 삭제됩니다.</Confirm></Card>
    </div>}</>
}

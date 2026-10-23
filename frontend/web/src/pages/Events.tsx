import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Activity, ArrowRight, ArrowUpRight, Boxes, RefreshCw, Search, ShieldAlert } from 'lucide-react'
import { Badge, Button, Card, Confirm, Empty, ErrorNotice, Loading, Notice, PageTitle } from '../components/ui'
import { displayTime, request, type EventRow } from '../lib/api'
import { useApprovals, useEvents, useInventory } from '../lib/queries'

function Severity({ value }: { value: string }) { return <Badge tone={value === '긴급' ? 'danger' : 'warning'}>{value}</Badge> }
function EventList({ rows }: { rows: EventRow[] }) {
  return <div className="event-list">{rows.map(row => <Link className="event-list-row" key={row.machine_id} to={`/events?machine=${row.machine_id}`}><div className={`event-icon ${row.severity === '긴급' ? 'urgent' : ''}`}><Activity size={19} /></div><div className="grow"><strong>설비 #{row.machine_id}</strong><p>{row.diagnosis}</p></div><Severity value={row.severity} /><ArrowUpRight size={18} /></Link>)}</div>
}
export function Overview() {
  const events = useEvents(), inventory = useInventory(), approvals = useApprovals(0, 5)
  const urgent = events.data?.summary?.urgent ?? events.data?.events.filter(e => e.severity === '긴급').length
  const warning = events.data?.summary?.warning ?? events.data?.events.filter(e => e.severity === '주의').length
  const shortage = inventory.data?.rows.filter(r => Math.ceil(r.demand_30d) > r.on_hand).length
  const metrics = [
    { label: '미완료 감지 이벤트', value: events.isError ? '—' : events.data?.total ?? '—', detail: '실제 정비 완료와 별도 관리', icon: Activity, tone: 'orange' },
    { label: events.data?.summary ? '전체 긴급 이벤트' : '조회 범위 내 긴급', value: events.isError ? '—' : urgent ?? '—', detail: events.data?.summary ? '서버 전체 집계 기준' : '현재 조회한 최대 100건 기준', icon: ShieldAlert, tone: 'red' },
    { label: events.data?.summary ? '전체 주의 이벤트' : '조회 범위 내 주의', value: events.isError ? '—' : warning ?? '—', detail: '고장 전 위험 신호 확인', icon: Activity, tone: 'amber' },
    { label: '30일 재고 부족 부품', value: inventory.isError ? '—' : shortage ?? '—', detail: '합성 재고 · 추정 수요 기준', icon: Boxes, tone: 'blue' },
  ]
  return <><PageTitle eyebrow="OPERATIONS OVERVIEW" title="오늘의 정비 운영" actions={<Link className="button button-secondary" to="/events">이벤트 확인 <ArrowRight size={16} /></Link>}>감지된 이상을 확인하고, 필요한 조치로 이어가세요.</PageTitle>
    <div className="metric-grid">{metrics.map(({ label, value, detail, icon: Icon, tone }) => <Card className="metric" key={label}><div className="metric-top"><span>{label}</span><Icon className={`text-${tone}`} size={20} /></div><strong className="metric-value">{value}<small>건</small></strong><p>{detail}</p></Card>)}</div>
    <div className="overview-grid"><Card><div className="card-heading"><div><p className="eyebrow">ATTENTION REQUIRED</p><h2>우선 확인할 이벤트</h2></div><Link className="text-link" to="/events">전체 보기 <ArrowRight size={15} /></Link></div>
      <ErrorNotice error={events.error} />{events.isPending ? <Loading /> : events.data && <>{events.data.total > events.data.events.length && <Notice>전체 {events.data.total}건 중 {events.data.events.length}건을 조회했습니다.</Notice>}{events.data.events.length ? <EventList rows={events.data.events.slice(0, 5)} /> : <Empty>감지 이벤트가 없습니다. 센서 상태가 정상이라는 뜻은 아닙니다.</Empty>}</>}
    </Card><Card className="workflow-card"><p className="eyebrow">MAINTENANCE WORKFLOW</p><h2>근거를 확인하고<br />조치를 결정하세요.</h2><ol className="workflow"><li><span>01</span><div><strong>이벤트 확인</strong><p>설비와 발생 근거를 살펴봅니다.</p></div></li><li><span>02</span><div><strong>AI 진단·작업지시서 검토</strong><p>매뉴얼 근거와 조달 상태를 확인합니다.</p></div></li><li><span>03</span><div><strong>승인 후 전송 결과 확인</strong><p>처리 결과를 감사 로그에서 추적합니다.</p></div></li></ol><Link className="button button-primary" to="/agent">AI 진단 시작 <ArrowRight size={16} /></Link></Card></div>
    <Card className="approval-overview"><div className="card-heading"><div><p className="eyebrow">APPROVAL INBOX</p><h2>승인 대기 <span className="count">{approvals.isError ? '—' : approvals.data?.total ?? '—'}</span></h2></div><Link className="text-link" to="/approvals">대기함 열기 <ArrowRight size={15} /></Link></div><ErrorNotice error={approvals.error} />{approvals.data?.approvals.length ? <div className="approval-shortlist">{approvals.data.approvals.map(row => <Link key={row.thread_id} to={`/agent?thread=${encodeURIComponent(row.thread_id)}`}><strong>설비 #{row.machine_id ?? '미확인'}</strong><Badge tone="warning">{row.priority ?? '승인 대기'}</Badge><span>검토하기 <ArrowRight size={14} /></span></Link>)}</div> : !approvals.isError && !approvals.isPending && <Empty>승인 대기 중인 진단이 없습니다.</Empty>}</Card><Notice>이벤트가 없는 설비를 정상으로 집계하지 않습니다. 설비 탐색에서 최신 관측과 데이터 지연을 확인하세요.</Notice><ErrorNotice error={inventory.error} />
  </>
}
export function EventsPage() {
  const client = useQueryClient()
  const [search, setSearch] = useState(''), [draft, setDraft] = useState(''), [severity, setSeverity] = useState('전체'), [offset, setOffset] = useState(0)
  const limit = 20
  const events = useEvents(search, severity === '전체' ? '' : severity, offset, limit)
  const [selected, setSelected] = useState<number[]>([]), [detailId, setDetailId] = useState<number | null>(() => {
    const value = new URLSearchParams(window.location.search).get('machine'); return value ? Number(value) : null
  })
  const [feedback, setFeedback] = useState('')
  const mutation = useMutation({ mutationFn: async (kind: 'scan' | 'complete' | 'delete') => {
    if (kind === 'scan') { const data = await request<{ detected_count: number }>('/scan', {}, 180000); return `스캔 완료: ${data.detected_count}건 감지` }
    const ids = selected.filter(id => events.data?.events.some(e => e.machine_id === id))
    const data = await request<{ completed_count?: number; deleted_count?: number }>(`/events/${kind}`, { machine_ids: ids })
    return `${data.completed_count ?? data.deleted_count ?? 0}건 ${kind === 'complete' ? '완료 처리' : '삭제'}했습니다.`
  }, onSuccess: text => { setFeedback(text); setSelected([]) }, onSettled: () => { void client.invalidateQueries({ queryKey: ['events'] }); void client.invalidateQueries({ queryKey: ['audit'] }) } })
  const all = events.data?.events ?? []
  const rows = all
  const detail = all.find(e => e.machine_id === detailId)
  const currentSelected = selected.filter(id => all.some(row => row.machine_id === id))
  return <><PageTitle eyebrow="MACHINE EVENTS" title="설비·이벤트" actions={<><Button variant="secondary" disabled={events.isFetching} onClick={() => void events.refetch()}><RefreshCw size={16} />새로고침</Button><Button disabled={mutation.isPending} onClick={() => mutation.mutate('scan')}><Search size={16} />전체 스캔</Button></>}>설비별 최신 감지 근거를 확인하고 진단을 시작하세요.</PageTitle>
    <ErrorNotice error={events.error || mutation.error} />{feedback && <Notice>{feedback}</Notice>}{mutation.isPending && <Notice>요청을 처리 중입니다. 화면을 닫아도 서버 처리는 취소되지 않습니다.</Notice>}
    <div className="events-grid"><Card><div className="card-heading"><h2>감지 이벤트 <span className="count">{events.data?.total ?? '—'}</span></h2><Badge>10초마다 갱신</Badge></div>
      <form className="filters" onSubmit={e => { e.preventDefault(); setSearch(draft.trim()); setOffset(0); setSelected([]) }}><label className="search-field"><Search size={17} /><input aria-label="설비 번호 또는 증상 검색" placeholder="설비 번호 또는 증상 검색" value={draft} onChange={e => setDraft(e.target.value)} /></label><select aria-label="긴급도" value={severity} onChange={e => { setSeverity(e.target.value); setOffset(0); setSelected([]) }}><option>전체</option><option>긴급</option><option>주의</option></select><Button variant="secondary">검색</Button></form>
      <p className="caption">검색 결과 {events.data?.total ?? '—'}건 · 서버 전체에 필터 적용 · 페이지당 {limit}건</p>
      {events.isPending ? <Loading /> : events.isError ? null : rows.length ? <div className="table-scroll"><table><thead><tr><th><input type="checkbox" aria-label="표시된 이벤트 전체 선택" checked={rows.length > 0 && rows.every(row => selected.includes(row.machine_id))} onChange={e => setSelected(e.target.checked ? rows.map(r => r.machine_id) : [])} /></th><th>설비 / 증상</th><th>긴급도</th><th>마지막 스캔</th></tr></thead><tbody>{rows.map(row => <tr className={row.machine_id === detailId ? 'selected-row' : ''} key={row.machine_id}><td><input type="checkbox" aria-label={`설비 ${row.machine_id} 선택`} checked={selected.includes(row.machine_id)} onChange={e => setSelected(ids => e.target.checked ? [...ids, row.machine_id] : ids.filter(id => id !== row.machine_id))} /></td><td><button className="row-button" onClick={() => setDetailId(row.machine_id)}><strong>설비 #{row.machine_id}</strong><span>{row.diagnosis}</span></button></td><td><Severity value={row.severity} /></td><td className="numeric">{displayTime(row.detected_at)}</td></tr>)}</tbody></table></div> : <Empty>조건에 맞는 감지 이벤트가 없습니다.</Empty>}
      <div className="pagination"><span>{events.data?.total ? offset + 1 : 0}–{offset + rows.length} / {events.data?.total ?? '—'}건</span><Button variant="secondary" disabled={!offset || events.isFetching} onClick={() => { setOffset(value => Math.max(0, value - limit)); setSelected([]) }}>이전</Button><Button variant="secondary" disabled={!events.data || offset + limit >= events.data.total || events.isFetching} onClick={() => { setOffset(value => value + limit); setSelected([]) }}>다음</Button></div><div className="table-actions"><span>{currentSelected.length}건 선택</span><Confirm label="완료 처리" title="선택 이벤트를 완료 처리할까요?" disabled={!currentSelected.length || mutation.isPending || events.isError} onConfirm={() => mutation.mutate('complete')}>목록에서 숨깁니다. 실제 정비 기록은 생성되지 않으며 새로운 근거가 생기면 다시 나타납니다.</Confirm><Confirm danger label="삭제" title="선택 이벤트를 삭제할까요?" disabled={!currentSelected.length || mutation.isPending || events.isError} onConfirm={() => mutation.mutate('delete')}>완료 기록 없이 삭제합니다. 같은 조건이면 다음 스캔에서 바로 다시 나타날 수 있습니다.</Confirm></div>
    </Card><Card className="detail-panel"><p className="eyebrow">EVENT DETAIL</p>{detail ? <><div className="card-heading"><h2>설비 #{detail.machine_id}</h2><Severity value={detail.severity} /></div><p className="detail-diagnosis">{detail.diagnosis}</p><dl><dt>근거 발생 시각</dt><dd>{displayTime(detail.evidence_at)}</dd><dt>마지막 스캔 시각</dt><dd>{displayTime(detail.detected_at)}</dd></dl><Link className="button button-secondary full-width sensor-link" to={`/machines/${detail.machine_id}`}>센서 추이 확인 <Activity size={16} /></Link><Link className="button button-primary full-width" to={`/agent?machine=${detail.machine_id}`}>이 설비 AI 진단 <ArrowRight size={17} /></Link></> : <Empty>목록의 설비를 선택하면<br />감지 근거를 확인할 수 있습니다.</Empty>}</Card></div>
  </>
}

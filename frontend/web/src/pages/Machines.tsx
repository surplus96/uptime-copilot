import { lazy, Suspense, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Activity, ArrowLeft, ArrowRight, RefreshCw, Search } from 'lucide-react'
import { operationsApi, displayTime, type Freshness } from '../lib/api'
import { Badge, Button, Card, Empty, ErrorNotice, Loading, Notice, PageTitle } from '../components/ui'
const SensorChart = lazy(() => import('../components/Charts').then(module => ({ default: module.SensorChart })))
const signals = [
  { key: 'volt', label: '전압', color: '#df681b' }, { key: 'rotate', label: '회전수', color: '#4d8aab' },
  { key: 'pressure', label: '압력', color: '#9b78b5' }, { key: 'vibration', label: '진동', color: '#3b987b' },
] as const
export function FreshnessBadge({ freshness }: { freshness: Freshness }) {
  return <Badge tone={freshness.status === 'missing' ? 'neutral' : freshness.status === 'lagging' ? 'warning' : 'success'}>{freshness.status === 'missing' ? '데이터 없음' : freshness.status === 'lagging' ? `데이터셋 대비 ${freshness.dataset_lag_hours}시간 지연` : '데이터셋 시각과 정렬'}</Badge>
}
export default function MachinesPage() {
  const [params, setParams] = useSearchParams()
  const search = params.get('search') ?? '', model = params.get('model') ?? ''
  const offset = Math.max(0, Number(params.get('offset')) || 0), limit = 20
  const [draft, setDraft] = useState(search)
  const query = useQuery({ queryKey: ['machines', search, model, offset], queryFn: () => operationsApi.machines(new URLSearchParams({ search, model, offset: String(offset), limit: String(limit) })), refetchInterval: 30000 })
  function apply(next: { search?: string; model?: string; offset?: number }) { setParams({ search: next.search ?? search, model: next.model ?? model, offset: String(next.offset ?? 0) }) }
  return <><PageTitle eyebrow="MACHINE EXPLORER" title="설비 탐색" actions={<Button variant="secondary" disabled={query.isFetching} onClick={() => void query.refetch()}><RefreshCw size={16} />새로고침</Button>}>등록된 설비의 최신 관측값과 센서 추이를 확인하세요.</PageTitle>
    <Notice>최신성은 데이터셋 내 최신 시각과 비교합니다. 실제 센서 연결 여부나 설비 정상 여부를 뜻하지 않습니다.</Notice><Card><form className="filters" onSubmit={event => { event.preventDefault(); apply({ search: draft.trim() }) }}><label className="grow">설비 번호 / 모델<input value={draft} onChange={e => setDraft(e.target.value)} placeholder="예: 64 또는 model2" /></label><label>모델<select value={model} onChange={e => apply({ model: e.target.value })}><option value="">전체 모델</option>{query.data?.models.map(value => <option key={value}>{value}</option>)}</select></label><Button><Search size={16} />검색</Button></form>
      <ErrorNotice error={query.error} />{query.isPending ? <Loading /> : query.data && <><div className="card-heading"><span className="caption">전체 {query.data.total}대 · 조회 시각 {displayTime(query.data.queried_at)}</span><Badge>{query.data.timezone}</Badge></div>{query.data.machines.length ? <div className="table-scroll"><table><thead><tr><th>설비</th><th>모델 / 연식</th><th>최신 관측</th><th>데이터 최신성</th><th>전압 / 회전수 / 압력 / 진동</th><th /></tr></thead><tbody>{query.data.machines.map(row => <tr key={row.machine_id}><td><Link className="text-link" to={`/machines/${row.machine_id}`}>설비 #{row.machine_id}</Link></td><td>{row.model} / {row.age}년</td><td className="numeric">{displayTime(row.latest_at)}</td><td><FreshnessBadge freshness={row.freshness} /></td><td className="numeric">{signals.map(s => row.sensors[s.key]?.toFixed(1) ?? '—').join(' / ')}</td><td><Link className="text-link" to={`/machines/${row.machine_id}`} aria-label={`설비 ${row.machine_id} 센서 상세`}><Activity size={16} />추이</Link></td></tr>)}</tbody></table></div> : <Empty>조건에 맞는 설비가 없습니다.</Empty>}<div className="pagination"><span>{query.data.total ? offset + 1 : 0}–{Math.min(offset + query.data.machines.length, query.data.total)} / {query.data.total}대</span><Button variant="secondary" disabled={!offset} onClick={() => apply({ offset: Math.max(0, offset - limit) })}>이전</Button><Button variant="secondary" disabled={offset + limit >= query.data.total} onClick={() => apply({ offset: offset + limit })}>다음</Button></div></>}
    </Card></>
}
export function MachineDetailPage() {
  const id = Number(useParams().machineId)
  const [hours, setHours] = useState(24), [bucket, setBucket] = useState(1)
  const valid = Number.isInteger(id) && id >= 1 && id <= 100
  const query = useQuery({ queryKey: ['telemetry', id, hours, bucket], queryFn: () => operationsApi.telemetry(id, hours, bucket), enabled: valid, refetchInterval: 30000 })
  if (!valid) return <Notice tone="error">설비 번호는 1~100 사이의 정수여야 합니다.</Notice>
  return <><PageTitle eyebrow="MACHINE TELEMETRY" title={`설비 #${id}`} actions={<><Link className="button button-secondary" to="/machines"><ArrowLeft size={16} />설비 목록</Link><Link className="button button-primary" to={`/agent?machine=${id}`}>이 설비 진단 <ArrowRight size={16} /></Link></>}>센서 관측값과 데이터 공백을 확인한 뒤 진단으로 이어가세요.</PageTitle>
    <Card><div className="card-heading"><h2>센서 추이</h2><Button variant="ghost" disabled={query.isFetching} onClick={() => void query.refetch()}><RefreshCw size={15} />새로고침</Button></div><div className="filters"><label>조회 기간<select value={hours} onChange={e => { const value = Number(e.target.value); setHours(value); setBucket(({ 24: 1, 72: 3, 168: 6, 720: 24 } as Record<number, number>)[value]) }}><option value={24}>최근 24시간</option><option value={72}>최근 3일</option><option value={168}>최근 7일</option><option value={720}>최근 30일</option></select></label><label>평균 집계 간격<select value={bucket} onChange={e => setBucket(Number(e.target.value))}>{[1, 3, 6, 12, 24].map(n => <option key={n} value={n}>{n}시간</option>)}</select></label></div><ErrorNotice error={query.error} />{query.isPending ? <Loading /> : query.data && <>
      <div className="telemetry-summary"><div><span>모델 / 연식</span><strong>{query.data.model} / {query.data.age}년</strong></div><div><span>설비 최신 관측</span><strong>{displayTime(query.data.latest_at)}</strong></div><div><span>시간당 표본</span><strong>{query.data.unique_samples} / 기대 {query.data.expected_samples}개</strong></div><FreshnessBadge freshness={query.data.freshness} /></div>
      <p className="caption">데이터 기준 {displayTime(query.data.dataset_now)} · 조회 {displayTime(query.data.queried_at)} · {query.data.timezone}</p><p className="caption">실제 시각 대비 관측 지연: {query.data.freshness.wall_clock_lag_hours === null ? '계산 불가' : query.data.freshness.wall_clock_lag_hours < 0 ? `${Math.abs(query.data.freshness.wall_clock_lag_hours)}시간 미래 (시뮬레이션 시각)` : `${query.data.freshness.wall_clock_lag_hours}시간`} · 이 표시는 정상 판정이 아닙니다.</p>
      {query.data.unique_samples < query.data.expected_samples && <Notice tone="warning">기간 내 시간당 표본이 부족합니다. 빈 집계 구간은 선으로 연결하지 않습니다. 일부 표본만 있는 구간은 해당 표본 평균입니다.</Notice>}{query.data.duplicate_samples > 0 && <Notice>동일 시각 중복 {query.data.duplicate_samples}건을 제외했습니다. 원본과 겹치면 시뮬레이션 값을 우선합니다.</Notice>}
      {query.data.unique_samples ? <><div className="sensor-grid">{signals.map(signal => <div className="sensor-panel" key={signal.key}><h3><span style={{ background: signal.color }} />{signal.label}</h3><Suspense fallback={<Loading />}><SensorChart data={query.data!} signal={signal} /></Suspense></div>)}</div><p className="caption">{bucket}시간 구간 평균 · 선형 연결 · 툴팁에서 구간 최소/최대와 표본 수 확인. 센서 단위는 원본 데이터에 명시되지 않아 추정하지 않습니다.</p><details><summary>집계 데이터 표 보기</summary><div className="table-scroll"><table><thead><tr><th>구간 시작</th><th>표본 수</th>{signals.map(s => <th key={s.key}>{s.label} 평균 (최소–최대)</th>)}</tr></thead><tbody>{query.data.points.map(row => <tr key={row.datetime}><td className="numeric">{displayTime(row.datetime)}</td><td>{row.sample_count}</td>{signals.map(s => <td key={s.key} className="numeric">{row[s.key] ?? '결측'} ({row[`${s.key}_min`] ?? '—'}–{row[`${s.key}_max`] ?? '—'})</td>)}</tr>)}</tbody></table></div></details></> : <Empty>선택한 기간의 센서 관측값이 없습니다.</Empty>}
    </>}</Card>
  </>
}

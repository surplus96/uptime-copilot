import { useQuery } from '@tanstack/react-query'
import { Activity, History, Sparkles } from 'lucide-react'
import { operationsApi, displayTime, type AgentResult } from '../lib/api'
import { failureCountdown, simulationStateLabel } from '../lib/simulation'
import { Badge, Button, ErrorNotice, Loading, Notice } from './ui'

export function DiagnosisContext({ result, machineHint, canPrepare, onPrepare }: {
  result: AgentResult; machineHint?: string; canPrepare: boolean; onPrepare: (id: number) => void;
}) {
  const machine = result.machine_id ?? ((result.status === 'done' || result.status === 'pending_approval') ? result.work_order_data?.machine_id : null) ?? (machineHint ? Number(machineHint) : null)
  const valid = machine !== null && Number.isInteger(machine) && machine >= 1 && machine <= 100
  const current = useQuery({ queryKey: ['machine-status', machine], queryFn: () => operationsApi.currentStatus(machine!), enabled: valid,
    refetchInterval: 10000, refetchOnWindowFocus: 'always', retry: false,
  })
  const changed = !!result.diagnosis_as_of && !!current.data?.dataset_now && result.diagnosis_as_of !== current.data.dataset_now
  return <section className="diagnosis-context" aria-label="저장된 진단과 현재 설비 상태">
    <div className="section-label"><History size={17} />저장된 진단</div><dl className="inline-dl"><dt>진단 데이터 기준 시각</dt><dd>{displayTime(result.diagnosis_as_of)}</dd><dt>진단 실행 시각 (KST)</dt><dd>{displayTime(result.diagnosed_at)}</dd></dl>
    <p className="caption">기존 진단과 작업지시서는 당시 결과로 보존됩니다. ‘상태 확인’은 저장된 처리 결과를 조회합니다.</p>
    {!result.diagnosis_as_of && <Notice>이전 진단에는 데이터 기준 시각이 기록되어 있지 않습니다. 현재 데이터와 같은 시점인지 확인할 수 없습니다.</Notice>}
    {valid && <><div className="section-label"><Activity size={17} />설비 #{machine} 현재 상태 <Badge>10초마다 조회</Badge></div><ErrorNotice error={current.error} />{current.isPending ? <Loading /> : current.data && <>
      <div className="actions"><Badge tone={current.data.simulation_state === 'FAULT' ? 'warning' : 'neutral'}>{simulationStateLabel(current.data.simulation_state)}</Badge><Badge tone={current.data.event?.severity === '긴급' ? 'danger' : 'neutral'}>{current.data.event ? `감지 이벤트: ${current.data.event.severity}` : '미완료 감지 이벤트 없음'}</Badge></div>
      <dl className="inline-dl"><dt>현재 데이터 기준 시각</dt><dd>{displayTime(current.data.dataset_now)}</dd><dt>설비 최신 관측</dt><dd>{displayTime(current.data.latest_observation_at)}</dd><dt>최근 고장 발생 기록</dt><dd>{current.data.latest_failure ? `${displayTime(current.data.latest_failure.datetime)} · ${current.data.latest_failure.component}` : '운영 구간 내 기록 없음'}</dd>{current.data.hours_until_failure !== null && <><dt>예정 고장까지</dt><dd>{failureCountdown(current.data.hours_until_failure)} · {displayTime(current.data.failure_due_at)}</dd></>}</dl>
      {current.data.simulation_state === 'FAULT' && <p className="caption">전조 오류가 발생한 단계입니다. 실제 고장 기록이 생기기 전까지 감지 이벤트는 ‘주의’일 수 있습니다.</p>}
      {changed && !current.isError && <Notice tone="warning">이 진단 이후 데이터 기준 시각이 변경됐습니다. 최신 상태가 필요하면 새 진단을 요청하세요.</Notice>}
    </>}
      <Button variant="secondary" type="button" disabled={!canPrepare} onClick={() => onPrepare(machine!)}><Sparkles size={16} />최신 데이터로 새 진단</Button>
      {result.status === 'pending_approval' && <p className="caption">새 진단 화면으로 이동해도 기존 승인 대기는 유지됩니다. 기존 작업지시서는 자동 승인·반려되지 않습니다.</p>}
    </>}
  </section>
}

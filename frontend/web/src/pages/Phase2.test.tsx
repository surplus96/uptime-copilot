import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api, operationsApi, type Telemetry, type WorkOrderData } from '../lib/api'
import ApprovalsPage from './Approvals'
import { MachineDetailPage } from './Machines'
import { EventsPage, Overview } from './Events'
import { WorkOrder } from './Agent'
vi.mock('../components/Charts', () => ({ SensorChart: () => <div>센서 차트</div>, InventoryChart: () => null }))
function mount(page: React.ReactNode, path = '/') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}>{page}</MemoryRouter></QueryClientProvider>)
}
beforeEach(() => {
  vi.spyOn(api, 'simulator').mockResolvedValue({ running: false, dataset_now: null, sim_now: null, timezone: 'Asia/Seoul', hours_per_tick: 12, tick_seconds: 15, last_error: null, consecutive_failures: 0, has_stale_events: false, degrading: [] })
  vi.spyOn(operationsApi, 'approvals').mockResolvedValue({ total: 0, approvals: [], offset: 0, limit: 20 })
})
describe('phase 2 operations views', () => {
  it('opens saved pending threads without invoking a diagnosis or approval', async () => {
    vi.mocked(operationsApi.approvals).mockResolvedValue({ total: 1, approvals: [{ thread_id: 'saved-1', machine_id: 64, priority: 'P1', updated_at: '2026-10-19T00:00:00Z' }], offset: 0, limit: 20 })
    const diagnose = vi.spyOn(api, 'diagnose'), resume = vi.spyOn(api, 'resume')
    mount(<ApprovalsPage />)
    expect((await screen.findByRole('link', { name: '검토하기' })).getAttribute('href')).toBe('/agent?thread=saved-1')
    expect(diagnose).not.toHaveBeenCalled()
    expect(resume).not.toHaveBeenCalled()
  })
  it('sends filters to the server and resets page and selection on a filter change', async () => {
    const events = vi.spyOn(api, 'events').mockResolvedValue({ total: 30, events: [{ machine_id: 1, severity: '긴급', diagnosis: '진동', detected_at: '', evidence_at: null }] })
    const user = userEvent.setup()
    mount(<EventsPage />)
    await user.click(await screen.findByRole('checkbox', { name: '설비 1 선택' }))
    await user.click(screen.getByRole('button', { name: '다음' }))
    await waitFor(() => expect(events.mock.calls.some(([params]) => params?.get('offset') === '20')).toBe(true))
    await user.type(screen.getByRole('textbox', { name: '설비 번호 또는 증상 검색' }), '진동')
    await user.click(screen.getByRole('button', { name: '검색' }))
    await waitFor(() => expect(events.mock.calls.some(([params]) => params?.get('search') === '진동' && params.get('offset') === '0')).toBe(true))
    expect((screen.getByRole('checkbox', { name: '설비 1 선택' }) as HTMLInputElement).checked).toBe(false)
  })
  it('uses global server counts rather than the current event page', async () => {
    vi.spyOn(api, 'events').mockResolvedValue({ total: 25, events: [], summary: { total: 25, urgent: 20, warning: 5 } })
    vi.spyOn(api, 'inventory').mockResolvedValue({ rows: [] })
    mount(<Overview />)
    await screen.findByText('전체 긴급 이벤트')
    expect(screen.getByText('20')).toBeTruthy()
    expect(screen.getByText('전체 주의 이벤트')).toBeTruthy()
  })
  it('shows data gaps and separate clock lag and requests the selected aggregation', async () => {
    const telemetry: Telemetry = { machine_id: 1, model: 'model1', age: 2, dataset_now: '2026-10-19 12:00:00', latest_at: '2026-10-19 10:00:00', queried_at: '2026-10-19 12:00:00', freshness: { status: 'aligned', dataset_lag_hours: 2, wall_clock_lag_hours: 2 }, timezone: 'Asia/Seoul', hours: 24, bucket_hours: 1, aggregation: 'mean', unique_samples: 0, expected_samples: 24, duplicate_samples: 1, window_start: null, window_end: null, points: [] }
    const fetch = vi.spyOn(operationsApi, 'telemetry').mockResolvedValue(telemetry)
    mount(<Routes><Route path="/machines/:machineId" element={<MachineDetailPage />} /></Routes>, '/machines/1')
    await screen.findByText('선택한 기간의 센서 관측값이 없습니다.')
    expect(screen.getByText(/기간 내 시간당 표본이 부족합니다/)).toBeTruthy()
    expect(screen.getByText(/실제 시각 대비 관측 지연: 2시간/)).toBeTruthy()
    await userEvent.selectOptions(screen.getByLabelText('조회 기간'), '168')
    await waitFor(() => expect(fetch).toHaveBeenLastCalledWith(1, 168, 6))
  })
  it('keeps the original text available alongside structured facts', () => {
    const data: WorkOrderData = { version: 1, machine_id: 1, severity: '긴급', priority: 'P1', recommend_shutdown: null, priority_reasons: [], components: [{ component: 'comp1', evidence: '진동 관측 근거', manual: '매뉴얼 근거', action: '', steps: ['전원을 차단하고 점검'], procurement: null }] }
    mount(<WorkOrder text="기존 원문" data={data} />)
    expect(screen.getByText('진동 관측 근거')).toBeTruthy()
    expect(screen.getByText('전원을 차단하고 점검')).toBeTruthy()
    expect(screen.getByText('정지 권고: 미확인')).toBeTruthy()
    expect(screen.getByText('기존 원문')).toBeTruthy()
  })
})

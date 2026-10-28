import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api, operationsApi, type AgentResult, type MachineStatus, type Simulator } from '../lib/api'
import AgentPage from './Agent'
import { SimulatorPage } from './Operations'
import { EventsPage } from './Events'
const live: MachineStatus = {
  machine_id: 2, dataset_now: '2026-10-26 03:00:00', latest_observation_at: '2026-10-26 03:00:00',
  simulation_state: 'FAULT', hours_until_failure: 3, failure_due_at: '2026-10-26 06:00:00',
  event: { severity: '주의', diagnosis: '전조 경보', detected_at: '', evidence_at: null }, latest_failure: null,
  queried_at: '2026-10-23 15:00:00', timezone: 'Asia/Seoul',
}
const simulator: Simulator = {
  running: false, dataset_now: live.dataset_now, sim_now: live.dataset_now, timezone: 'Asia/Seoul',
  hours_per_tick: 12, tick_seconds: 15, last_error: null, consecutive_failures: 0, has_stale_events: false,
  degrading: [{ machine_id: 2, state: 'FAULT', signal: 'volt', drift_sigma: 4.5, progress: 1.36, hours_until_failure: 3, failure_due_at: live.failure_due_at }],
  recent_failures: [{ machine_id: 3, datetime: '2026-10-25 06:00:00', component: 'comp2' }],
}
const snapshot: AgentResult = { status: 'done', machine_id: 2, result: '저장된 주의 진단', diagnosis_as_of: '2026-10-25 15:00:00', diagnosed_at: '2026-10-23 14:00:00' }
function mount(page: React.ReactNode, path = '/') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}>{page}</MemoryRouter></QueryClientProvider>)
  return client
}
beforeEach(() => {
  sessionStorage.clear()
  vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok', offline: false, llm_provider: 'test', llm_model: 'test' })
  vi.spyOn(api, 'simulator').mockResolvedValue(simulator)
  vi.spyOn(operationsApi, 'currentStatus').mockResolvedValue(live)
})
afterEach(() => vi.unstubAllGlobals())
describe('saved diagnosis and live observations', () => {
  it('updates the live view while preserving the completed diagnosis and never posting', async () => {
    vi.spyOn(api, 'status').mockResolvedValue(snapshot)
    const diagnose = vi.spyOn(api, 'diagnose'), resume = vi.spyOn(api, 'resume')
    const client = mount(<AgentPage />, '/agent?thread=old-result')
    await screen.findByText('이 진단 이후 데이터 기준 시각이 변경됐습니다. 최신 상태가 필요하면 새 진단을 요청하세요.')
    expect(screen.getByText('전조 오류 발생')).toBeTruthy()
    expect(screen.getByText('저장된 주의 진단')).toBeTruthy()
    vi.mocked(operationsApi.currentStatus).mockResolvedValue({ ...live, simulation_state: 'HEALTHY', dataset_now: '2026-10-26 15:00:00', latest_failure: { datetime: '2026-10-26 06:00:00', component: 'comp1' }, event: { ...live.event!, severity: '긴급' } })
    await act(async () => { await client.refetchQueries({ queryKey: ['machine-status', 2] }) })
    await screen.findByText('감지 이벤트: 긴급')
    expect(screen.getByText('열화 없음')).toBeTruthy()
    expect(screen.getByText('저장된 주의 진단')).toBeTruthy()
    expect(diagnose).not.toHaveBeenCalled()
    expect(resume).not.toHaveBeenCalled()
  })
  it('opens a fresh draft, preserves the prior link, and posts only after explicit submit', async () => {
    vi.spyOn(api, 'status').mockImplementation(async thread => thread === 'old-result' ? snapshot : { status: 'done', machine_id: 2, result: '새 진단 완료' })
    const diagnose = vi.spyOn(api, 'diagnose').mockResolvedValue({ status: 'done', machine_id: 2, result: '새 진단 완료' })
    const user = userEvent.setup()
    const client = mount(<AgentPage />, '/agent?thread=old-result')
    const button = await screen.findByRole('button', { name: '최신 데이터로 새 진단' })
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false))
    await user.click(button)
    expect(diagnose).not.toHaveBeenCalled()
    expect(screen.getByRole('link', { name: '이전 진단 보기' }).getAttribute('href')).toContain('thread=old-result')
    expect((screen.getByLabelText('설비 번호와 증상') as HTMLTextAreaElement).value).toBe('2번 설비의 최신 상태를 진단해줘')
    await user.click(screen.getByRole('button', { name: '진단 요청' }))
    await screen.findByText('새 진단 완료')
    expect(diagnose).toHaveBeenCalledTimes(1)
    expect(diagnose.mock.calls[0][0]).not.toBe('old-result')
    expect(client.getQueryData<AgentResult>(['agent', 'old-result'])).toEqual(snapshot)
  })
  it('keeps pending approvals intact when preparing a new diagnosis', async () => {
    vi.spyOn(api, 'status').mockResolvedValue({ ...snapshot, status: 'pending_approval', message: '승인 필요', work_order: '기존 작업지시서' })
    const diagnose = vi.spyOn(api, 'diagnose'), resume = vi.spyOn(api, 'resume')
    mount(<AgentPage />, '/agent?thread=pending-old')
    const button = await screen.findByRole('button', { name: '최신 데이터로 새 진단' })
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false))
    await userEvent.click(button)
    expect(screen.getByRole('link', { name: '이전 진단 보기' }).getAttribute('href')).toContain('pending-old')
    expect(diagnose).not.toHaveBeenCalled()
    expect(resume).not.toHaveBeenCalled()
  })
  it('does not fabricate a baseline for old checkpoints', async () => {
    vi.spyOn(api, 'status').mockResolvedValue({ status: 'done', machine_id: 2, result: '옛 진단' })
    mount(<AgentPage />, '/agent?thread=legacy')
    await screen.findByText('이전 진단에는 데이터 기준 시각이 기록되어 있지 않습니다. 현재 데이터와 같은 시점인지 확인할 수 없습니다.')
    expect(screen.queryByText(/이 진단 이후 데이터 기준 시각이 변경/)).toBeNull()
  })
  it('separates precursor phase, countdown, and actual failure history in the simulator', async () => {
    mount(<SimulatorPage />)
    await screen.findByText('전조 오류 발생')
    expect(screen.getByText('시뮬레이션 3시간 후')).toBeTruthy()
    expect(screen.queryByText('136%')).toBeNull()
    expect(screen.getByText('2026-10-25 06:00:00')).toBeTruthy()
    expect(screen.getByText('comp2')).toBeTruthy()
  })
  it('shows current simulation phase separately from the event severity', async () => {
    vi.spyOn(api, 'events').mockResolvedValue({ total: 1, events: [{ machine_id: 2, severity: '주의', diagnosis: '전조 경보', evidence_at: live.dataset_now, detected_at: '' }] })
    mount(<EventsPage />)
    await screen.findByText('전조 오류 발생')
    expect(screen.getByText('주의', { selector: '.badge' })).toBeTruthy()
  })
})


describe('scoped simulator controls', () => {
  it('posts the selected scope, displays it, and prevents a concurrent fleet run', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{}'))
    vi.stubGlobal('fetch', fetch)
    const user = userEvent.setup()
    mount(<SimulatorPage />)
    await screen.findByText('전조 오류 발생')
    await user.clear(screen.getByLabelText('설비 번호 (1~100)'))
    await user.type(screen.getByLabelText('설비 번호 (1~100)'), '2')
    vi.mocked(api.simulator).mockResolvedValue({ ...simulator, running: true, mode: 'single', machine_id: 2 })
    await user.click(screen.getByRole('button', { name: '선택 설비 시작' }))
    await screen.findByText('설비 #2만 진행 중입니다.')
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(fetch.mock.calls[0][0]).toBe('/api/simulator/start')
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ mode: 'single', machine_id: 2 })
    expect((screen.getByRole('button', { name: '전체 시작' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: '열화 주입' }) as HTMLButtonElement).disabled).toBe(true)
    vi.mocked(api.simulator).mockResolvedValue({ ...simulator, mode: 'single', machine_id: 2 })
    await user.click(screen.getByRole('button', { name: '선택 설비 정지' }))
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
    expect(fetch.mock.calls[1][0]).toBe('/api/simulator/stop')
  })
  it('injects without starting and enables the selected run after refreshed state arrives', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{}'))
    vi.stubGlobal('fetch', fetch)
    const user = userEvent.setup()
    mount(<SimulatorPage />)
    await screen.findByText('전조 오류 발생')
    expect((screen.getByRole('button', { name: '선택 설비 시작' }) as HTMLButtonElement).disabled).toBe(true)
    vi.mocked(api.simulator).mockResolvedValue({ ...simulator, degrading: [...simulator.degrading, { ...simulator.degrading[0], machine_id: 64 }] })
    await user.click(screen.getByRole('button', { name: '열화 주입' }))
    await user.click(screen.getByRole('button', { name: '확인' }))
    await screen.findByText('열화를 주입했습니다. 선택 설비 시작을 누르면 해당 설비만 진행됩니다.')
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(fetch.mock.calls[0][0]).toBe('/api/simulator/inject')
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ machine_id: 64, signal: null })
    await waitFor(() => expect((screen.getByRole('button', { name: '선택 설비 시작' }) as HTMLButtonElement).disabled).toBe(false))
  })
  it('shows the automatic stop reason from the server', async () => {
    vi.mocked(api.simulator).mockResolvedValue({ ...simulator, mode: 'single', machine_id: 2, stop_reason: 'failure_reached' })
    mount(<SimulatorPage />)
    await screen.findByText('선택 설비의 고장·자동 정비 기록을 생성하고 정지했습니다. 아래 고장 이력에서 진단으로 이동할 수 있습니다.')
  })
})

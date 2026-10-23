import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api } from '../lib/api'
import { EventsPage, Overview } from './Events'
function mount(page: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter>{page}</MemoryRouter></QueryClientProvider>)
}
beforeEach(() => {
  vi.spyOn(api, 'simulator').mockResolvedValue({ running: false, dataset_now: null, sim_now: null, timezone: 'Asia/Seoul', hours_per_tick: 12, tick_seconds: 15, last_error: null, consecutive_failures: 0, has_stale_events: false, degrading: [] })
  vi.unstubAllGlobals()
})
describe('event workflow', () => {
  it('links the selected event to a diagnosis with the correct machine context', async () => {
    vi.spyOn(api, 'events').mockResolvedValue({ total: 1, events: [{ machine_id: 84, severity: '긴급', diagnosis: '진동 이상', detected_at: '2026-10-19 10:00:00', evidence_at: '2026-10-19 09:00:00' }] })
    mount(<EventsPage />)
    await userEvent.click(await screen.findByRole('button', { name: /설비 #84/ }))
    expect(screen.getByRole('link', { name: /이 설비 AI 진단/ }).getAttribute('href')).toBe('/agent?machine=84')
    expect(screen.getByText('2026-10-19 09:00:00')).toBeTruthy()
  })
  it('does not send a deletion until the confirmation is accepted', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'events').mockResolvedValue({ total: 1, events: [{ machine_id: 84, severity: '긴급', diagnosis: '진동 이상', detected_at: '2026-10-19 10:00:00', evidence_at: null }] })
    const fetch = vi.fn().mockResolvedValue(new Response('{"deleted_count":1}'))
    vi.stubGlobal('fetch', fetch)
    mount(<EventsPage />)
    await user.click(await screen.findByRole('checkbox', { name: '설비 84 선택' }))
    await user.click(screen.getByRole('button', { name: '삭제' }))
    expect(fetch).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '취소' }))
    expect(fetch).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '삭제' }))
    await user.click(screen.getByRole('button', { name: '확인' }))
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1))
    expect(fetch.mock.calls[0][0]).toBe('/api/events/delete')
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ machine_ids: [84] })
  })
  it('distinguishes the total from the limited results and does not infer healthy machines', async () => {
    vi.spyOn(api, 'events').mockResolvedValue({ total: 101, events: [{ machine_id: 84, severity: '주의', diagnosis: '확인 필요', detected_at: '', evidence_at: null }] })
    vi.spyOn(api, 'inventory').mockResolvedValue({ rows: [] })
    mount(<Overview />)
    await screen.findByText('전체 101건 중 1건을 조회했습니다.')
    expect(screen.getByText(/이벤트가 없는 설비를 정상으로 집계하지 않습니다/)).toBeTruthy()
  })
})

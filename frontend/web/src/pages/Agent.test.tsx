import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api, ApiError, type AgentResult } from '../lib/api'
import AgentPage from './Agent'
const pending: AgentResult = { status: 'pending_approval', message: '긴급 점검 필요', work_order: '[부품] comp1\n[증상] 진동', perspectives: ['[안전] 점검 필요'] }
const approved: AgentResult = { status: 'done', result: '[긴급 승인됨] 서버 승인 기록', cmms_delivery: { status: 'skipped_unconfigured' } }
function mount(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const view = render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><AgentPage /></MemoryRouter></QueryClientProvider>)
  return { ...view, client }
}
beforeEach(() => {
  sessionStorage.clear()
  vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok', offline: false, llm_provider: 'test', llm_model: 'test' })
})
describe('diagnosis and approval recovery', () => {
  it.each([
    { result: '설비 #64: 최근 오류 이력 없음', expected: 'CMMS: 전송 대상 없음 · 작업지시서가 생성되지 않은 진단입니다.' },
    { result: '[긴급 승인됨] 승인 완료', expected: 'CMMS: 응답에 전송 결과 없음 · 감사 로그에서 확인' },
    { result: '[긴급 반려됨] 반려 완료', work_order: '긴급 작업지시서', expected: 'CMMS: 반려됨 · 전송하지 않음' },
    { result: '[사전 경보 - 예방 조치 권장] 점검 권장', work_order: '주의 작업지시서', expected: 'CMMS: 주의 진단 · 자동 전송 대상 아님' },
  ])('explains the delivery state for $result', async ({ result, work_order, expected }) => {
    vi.spyOn(api, 'status').mockResolvedValue({ status: 'done', result, work_order })
    const resume = vi.spyOn(api, 'resume'), retry = vi.spyOn(api, 'retryDelivery')
    mount('/agent?thread=delivery-state')
    await screen.findByText(expected)
    expect(resume).not.toHaveBeenCalled()
    expect(retry).not.toHaveBeenCalled()
  })
  it('restores a pending thread from its URL without posting', async () => {
    vi.spyOn(api, 'status').mockResolvedValue(pending)
    const diagnose = vi.spyOn(api, 'diagnose'), resume = vi.spyOn(api, 'resume')
    mount('/agent?thread=saved-thread')
    await screen.findByText('승인 대기')
    expect(diagnose).not.toHaveBeenCalled()
    expect(resume).not.toHaveBeenCalled()
    await waitFor(() => expect((screen.getByRole('button', { name: '승인' }) as HTMLButtonElement).disabled).toBe(false))
  })
  it('locks decisions after a timeout, then recovers the recorded result with GET', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'status').mockResolvedValue(pending)
    const resume = vi.spyOn(api, 'resume').mockRejectedValue(new ApiError('응답 시간이 초과됐습니다'))
    mount('/agent?thread=timeout-thread')
    const approve = await screen.findByRole('button', { name: '승인' })
    await waitFor(() => expect((approve as HTMLButtonElement).disabled).toBe(false))
    await user.click(approve)
    await user.click(screen.getByRole('button', { name: '확인' }))
    await screen.findByText('이전 요청의 결과가 확인되지 않았습니다. ‘상태 확인’으로 서버 기록을 확인한 뒤 다음 작업을 진행하세요.')
    expect((approve as HTMLButtonElement).disabled).toBe(true)
    expect(resume).toHaveBeenCalledTimes(1)
    vi.mocked(api.status).mockResolvedValue(approved)
    await user.click(screen.getByRole('button', { name: '상태 확인' }))
    await screen.findByText('[긴급 승인됨] 서버 승인 기록')
    expect(screen.getByText('CMMS: 미설정 · 전송 안 함')).toBeTruthy()
    expect(resume).toHaveBeenCalledTimes(1)
  })
  it('preserves an unconfirmed action across refresh until explicit status confirmation', async () => {
    sessionStorage.setItem('uptime:unconfirmed:refresh-thread', 'approve')
    vi.spyOn(api, 'status').mockResolvedValue(pending)
    const resume = vi.spyOn(api, 'resume')
    mount('/agent?thread=refresh-thread')
    const approve = await screen.findByRole('button', { name: '승인' })
    expect((approve as HTMLButtonElement).disabled).toBe(true)
    await userEvent.click(screen.getByRole('button', { name: '상태 확인' }))
    await waitFor(() => expect((approve as HTMLButtonElement).disabled).toBe(false))
    expect(resume).not.toHaveBeenCalled()
  })
  it('submits selected machine context only once', async () => {
    vi.spyOn(api, 'status').mockResolvedValue({ status: 'done', result: '진단 완료' })
    const diagnose = vi.spyOn(api, 'diagnose').mockResolvedValue({ status: 'done', result: '진단 완료' })
    mount('/agent?machine=64')
    expect((screen.getByLabelText('설비 번호와 증상') as HTMLTextAreaElement).value).toBe('64번 설비 상태를 진단해줘')
    await userEvent.click(screen.getByRole('button', { name: '진단 요청' }))
    await screen.findByText('진단 완료')
    expect(diagnose).toHaveBeenCalledTimes(1)
    expect(diagnose.mock.calls[0][1]).toBe('64번 설비 상태를 진단해줘')
  })
  it('keeps approval locked if the recovery lookup itself fails', async () => {
    sessionStorage.setItem('uptime:unconfirmed:error-thread', 'reject')
    vi.spyOn(api, 'status').mockRejectedValue(new ApiError('상태 조회 연결 실패'))
    const resume = vi.spyOn(api, 'resume')
    mount('/agent?thread=error-thread')
    await screen.findByText('상태 조회 연결 실패')
    expect(resume).not.toHaveBeenCalled()
    expect((screen.getByRole('button', { name: '진단 요청' }) as HTMLButtonElement).disabled).toBe(true)
  })
})

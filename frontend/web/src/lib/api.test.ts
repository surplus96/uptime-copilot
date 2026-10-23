import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, request } from './api'
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })
describe('API transport', () => {
  it('sends a mutation once and preserves a validation error', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: [{ msg: '설비 번호가 필요합니다' }] }), { status: 422 }))
    vi.stubGlobal('fetch', fetch)
    await expect(request('/agent/query', { message: 'test' })).rejects.toMatchObject({ status: 422, message: '설비 번호가 필요합니다' })
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(fetch.mock.calls[0][0]).toBe('/api/agent/query')
    expect(fetch.mock.calls[0][1].method).toBe('POST')
  })
  it('aborts on timeout without retrying or asserting server failure', async () => {
    vi.useFakeTimers()
    const fetch = vi.fn((_url: string, options: RequestInit) => new Promise((_resolve, reject) => options.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))))
    vi.stubGlobal('fetch', fetch)
    const result = request('/agent/resume', { approved: true }, 50)
    const assertion = expect(result).rejects.toThrow('서버 처리 결과를 확인하세요')
    await vi.advanceTimersByTimeAsync(60)
    await assertion
    expect(fetch).toHaveBeenCalledTimes(1)
  })
  it('handles a proxy HTML error without exposing network internals', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>proxy error</html>', { status: 502 })))
    await expect(request('/health')).rejects.toEqual(new ApiError('서버 요청 오류 (HTTP 502)', 502))
  })
  it('uses GET for status recovery', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{"status":"done"}'))
    vi.stubGlobal('fetch', fetch)
    await request('/agent/status/test')
    expect(fetch.mock.calls[0][1].method).toBe('GET')
    expect(fetch.mock.calls[0][1].body).toBeUndefined()
  })
})

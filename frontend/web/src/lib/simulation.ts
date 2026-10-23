export function simulationStateLabel(state?: string | null) {
  return state ? ({ HEALTHY: '열화 없음', DEGRADING: '열화 중', FAULT: '전조 오류 발생' } as Record<string, string>)[state] ?? state : '시뮬레이션 상태 없음'
}
export function failureCountdown(hours?: number | null) {
  return hours === null || hours === undefined ? '계산 정보 없음' : `시뮬레이션 ${hours}시간 후`
}

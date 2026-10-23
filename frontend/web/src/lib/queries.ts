import { useQuery } from '@tanstack/react-query'
import { api, operationsApi } from './api'
export const useHealth = () => useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 30000 })
export const useSimulator = () => useQuery({ queryKey: ['simulator'], queryFn: api.simulator, refetchInterval: 10000, refetchOnWindowFocus: 'always', refetchOnMount: 'always' })
export const useEvents = (search = '', severity = '', offset = 0, limit = 100) => useQuery({ queryKey: ['events', search, severity, offset, limit], queryFn: () => {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) }); if (search) params.set('search', search); if (severity) params.set('severity', severity)
  return api.events(params)
}, refetchInterval: 10000, refetchOnWindowFocus: 'always', refetchOnMount: 'always' })
export const useApprovals = (offset = 0, limit = 20) => useQuery({ queryKey: ['approvals', offset, limit], queryFn: () => operationsApi.approvals(offset, limit), refetchInterval: 15000 })
export const useInventory = () => useQuery({ queryKey: ['inventory'], queryFn: api.inventory, staleTime: 300000 })

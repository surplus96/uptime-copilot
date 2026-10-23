import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { InventoryRow, Telemetry } from '../lib/api'
import { Empty } from './ui'
export const signals = [
  { key: 'volt', label: '전압', color: '#df681b' },
  { key: 'rotate', label: '회전수', color: '#4d8aab' },
  { key: 'pressure', label: '압력', color: '#9b78b5' },
  { key: 'vibration', label: '진동', color: '#3b987b' },
] as const
export function SensorChart({ data, signal }: { data: Telemetry; signal: typeof signals[number] }) {
  const points = data.points
  if (!points.some(row => row[signal.key] !== null)) return <Empty>{signal.label} 관측값이 없습니다.</Empty>
  return <div className="sensor-chart" aria-label={`${signal.label} ${data.hours}시간 추이`}>
    <ResponsiveContainer width="100%" height={210} initialDimension={{ width: 500, height: 210 }}>
      <LineChart data={points} accessibilityLayer margin={{ top: 8, right: 15, bottom: 0, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#edf0f4" />
        <XAxis dataKey="datetime" tickFormatter={value => String(value).slice(5, 16)} minTickGap={45} tick={{ fontSize: 10 }} axisLine={false} tickLine={false} />
        <YAxis width={48} domain={['auto', 'auto']} tick={{ fontSize: 10 }} axisLine={false} tickLine={false} />
        <Tooltip content={({ active, payload }) => { const point = payload?.[0]?.payload; return active && point ? <div className="chart-tooltip"><strong>{point.datetime}</strong><p>{signal.label} 평균: {point[signal.key] ?? '결측'}</p><p>최소 / 최대: {point[`${signal.key}_min`] ?? '—'} / {point[`${signal.key}_max`] ?? '—'}</p><p>표본: {point.sample_count}개</p></div> : null }} />
        <Line dataKey={signal.key} name={signal.label} type="linear" stroke={signal.color} strokeWidth={2} dot={false} connectNulls={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  </div>
}
export function InventoryChart({ rows }: { rows: InventoryRow[] }) {
  return <div className="inventory-chart" aria-label="현재 재고와 30일·90일 예상 수요 비교">
    <ResponsiveContainer width="100%" height={280} initialDimension={{ width: 700, height: 280 }}>
      <BarChart data={rows.map(row => ({ component: row.component, stock: row.on_hand, d30: Math.ceil(row.demand_30d), d90: Math.ceil(row.demand_90d) }))} accessibilityLayer margin={{ top: 12, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#edf0f4" /><XAxis dataKey="component" tick={{ fontSize: 11 }} axisLine={false} tickLine={false} /><YAxis allowDecimals={false} tick={{ fontSize: 10 }} axisLine={false} tickLine={false} /><Tooltip /><Legend wrapperStyle={{ fontSize: 11 }} /><Bar dataKey="stock" name="현재 재고" fill="#526e87" radius={[3, 3, 0, 0]} isAnimationActive={false} /><Bar dataKey="d30" name="30일 예상 수요" fill="#f2a060" radius={[3, 3, 0, 0]} isAnimationActive={false} /><Bar dataKey="d90" name="90일 예상 수요" fill="#e6691e" radius={[3, 3, 0, 0]} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  </div>
}

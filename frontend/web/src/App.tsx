import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { Activity, ArrowUpRight, BookOpen, Boxes, ClipboardCheck, ClipboardList, FlaskConical, LayoutDashboard, Server, ShieldCheck, Sparkles } from 'lucide-react'
import { Badge } from './components/ui'
import { useHealth, useSimulator } from './lib/queries'
import { displayTime } from './lib/api'
import { Overview, EventsPage } from './pages/Events'
import AgentPage from './pages/Agent'
import MachinesPage, { MachineDetailPage } from './pages/Machines'
import ApprovalsPage from './pages/Approvals'
import { InventoryPage, ManualPage, AuditPage, SimulatorPage } from './pages/Operations'
const navigation = [
  { to: '/', label: '운영 개요', icon: LayoutDashboard },
  { to: '/machines', label: '설비 탐색', icon: Server },
  { to: '/events', label: '감지 이벤트', icon: Activity },
  { to: '/approvals', label: '승인 대기함', icon: ClipboardCheck },
  { to: '/agent', label: 'AI 진단', icon: Sparkles },
  { to: '/inventory', label: '부품·조달', icon: Boxes },
  { to: '/manual', label: '매뉴얼 검색', icon: BookOpen },
  { to: '/audit', label: '감사·연동', icon: ClipboardList },
  { to: '/simulator', label: '시뮬레이터', icon: FlaskConical },
]
export default function App() {
  const health = useHealth(), simulator = useSimulator()
  return <div className="app-shell"><a className="skip-link" href="#main">본문으로 이동</a><aside className="sidebar">
    <NavLink className="brand" to="/"><div className="brand-icon"><ShieldCheck size={26} /></div><div>Uptime<span>COPILOT</span></div></NavLink>
    <p className="nav-label">정비 운영 워크스페이스</p><nav aria-label="주 메뉴">{navigation.map(({ to, label, icon: Icon }) => <NavLink end={to === '/'} key={to} to={to} className={({ isActive }) => cnNav(isActive)}><Icon size={19} />{label}<ArrowUpRight size={14} className="nav-arrow" /></NavLink>)}</nav>
    <div className="sidebar-bottom"><ShieldCheck size={19} /><div><strong>Predictive maintenance</strong><p>진단에서 조치까지, 근거와 함께.</p></div></div>
  </aside><div className="workspace"><header className="topbar"><div className="workspace-label">OPERATIONS <span>/</span> Uptime Copilot</div><div className="statusbar">
    <Badge tone={health.isError ? 'danger' : health.data ? 'success' : 'neutral'}>{health.isError ? '연결 확인 실패' : health.data ? '백엔드 연결됨' : '연결 확인 중'}</Badge>
    <Badge>{health.isError || !health.data ? '운영 모드 미확인' : health.data.offline ? '오프라인 모드' : '온라인 모드'}</Badge>
    <span className="data-clock">데이터 기준 <strong>{displayTime(simulator.isError ? null : simulator.data?.dataset_now)}</strong> {simulator.data?.timezone}</span>
  </div></header><main id="main" className="main"><Routes>
    <Route path="/" element={<Overview />} /><Route path="/events" element={<EventsPage />} /><Route path="/agent" element={<AgentPage />} />
    <Route path="/machines" element={<MachinesPage />} /><Route path="/machines/:machineId" element={<MachineDetailPage />} /><Route path="/approvals" element={<ApprovalsPage />} />
    <Route path="/inventory" element={<InventoryPage />} /><Route path="/manual" element={<ManualPage />} /><Route path="/audit" element={<AuditPage />} /><Route path="/simulator" element={<SimulatorPage />} />
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes></main><footer className="footer"><span>UPTIME COPILOT</span><span>{health.data && !health.isError ? `응답 모델: ${health.data.llm_provider} / ${health.data.llm_model}` : '응답 모델 미확인'} · 독립 프로젝트 / 시뮬레이션 데이터 기반</span></footer></div></div>
}
function cnNav(active: boolean) { return `nav-item${active ? ' active' : ''}` }

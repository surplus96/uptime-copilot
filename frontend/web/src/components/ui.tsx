import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from 'react'
import * as AlertDialog from '@radix-ui/react-alert-dialog'
import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'
import { AlertCircle, Inbox, LoaderCircle } from 'lucide-react'
import { errorMessage } from '../lib/api'

export function cn(...values: ClassValue[]) { return twMerge(clsx(values)) }
export function Button({ className, variant = 'primary', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'danger' | 'ghost' }) {
  return <button className={cn('button', `button-${variant}`, className)} {...props} />
}
export function Card({ className, ...props }: HTMLAttributes<HTMLElement>) { return <section className={cn('card', className)} {...props} /> }
export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: string }) { return <span className={`badge badge-${tone}`}>{children}</span> }
export function Notice({ children, tone = 'info' }: { children: ReactNode; tone?: string }) { return <div className={`notice notice-${tone}`} role={tone === 'error' ? 'alert' : 'status'}><AlertCircle size={17} /><div>{children}</div></div> }
export function ErrorNotice({ error }: { error: unknown }) { return error ? <Notice tone="error">{errorMessage(error)}</Notice> : null }
export function Loading() { return <div className="empty" role="status"><LoaderCircle className="spin" size={24} />불러오는 중…</div> }
export function Empty({ children }: { children: ReactNode }) { return <div className="empty"><Inbox size={28} /><div>{children}</div></div> }
export function PageTitle({ eyebrow, title, children, actions }: { eyebrow: string; title: string; children: ReactNode; actions?: ReactNode }) {
  return <div className="page-heading"><div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1><p className="muted">{children}</p></div>{actions && <div className="actions">{actions}</div>}</div>
}
export function Confirm({ label, title, children, onConfirm, disabled, danger = false }: { label: ReactNode; title: string; children: ReactNode; onConfirm: () => void; disabled?: boolean; danger?: boolean }) {
  return <AlertDialog.Root><AlertDialog.Trigger asChild><Button variant={danger ? 'danger' : 'secondary'} disabled={disabled}>{label}</Button></AlertDialog.Trigger>
    <AlertDialog.Portal><AlertDialog.Overlay className="dialog-overlay" /><AlertDialog.Content className="dialog">
      <AlertDialog.Title>{title}</AlertDialog.Title><AlertDialog.Description asChild><div className="dialog-description">{children}</div></AlertDialog.Description>
      <div className="actions"><AlertDialog.Cancel asChild><Button variant="secondary">취소</Button></AlertDialog.Cancel><AlertDialog.Action asChild><Button variant={danger ? 'danger' : 'primary'} onClick={onConfirm}>확인</Button></AlertDialog.Action></div>
    </AlertDialog.Content></AlertDialog.Portal></AlertDialog.Root>
}

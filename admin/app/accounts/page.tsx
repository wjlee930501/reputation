'use client'

import { useCallback, useEffect, useState } from 'react'

import { ApiError, fetchAPI } from '@/lib/api'
import { fetchCurrentAccount, type CurrentAccount } from '@/lib/current-account'

type AdminAccount = {
  id: string
  email: string
  name: string
  role: 'OWNER' | 'OPERATOR' | string
  is_active: boolean
  last_login_at: string | null
  created_at: string | null
}

const ROLE_LABELS: Record<string, string> = {
  OWNER: '소유자',
  OPERATOR: '운영자',
}

const ROLE_HINTS: Record<string, string> = {
  OWNER: '병원 운영 전체 + 계정 관리',
  OPERATOR: '병원 운영 전체 (계정 관리 불가)',
}

const MIN_PASSWORD_LENGTH = 14

function formatDateTime(value: string | null): string {
  if (!value) return '—'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return '—'
  return parsed.toLocaleString('ko-KR', { dateStyle: 'medium', timeStyle: 'short' })
}

function errorMessage(e: unknown, fallback: string): string {
  if (e instanceof ApiError) return e.message
  if (e instanceof Error) return e.message
  return fallback
}

export default function AccountsPage() {
  const [accounts, setAccounts] = useState<AdminAccount[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [actionError, setActionError] = useState('')
  const [actionSuccess, setActionSuccess] = useState('')
  const [busyId, setBusyId] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState({ email: '', name: '', role: 'OPERATOR', password: '' })
  const [resetTarget, setResetTarget] = useState<AdminAccount | null>(null)
  const [resetPassword, setResetPassword] = useState('')
  const [resetting, setResetting] = useState(false)

  const [me, setMe] = useState<CurrentAccount | null>(null)
  // 내 역할의 정본은 방금 불러온 명부의 내 행이다 — 세션 토큰의 role은 로그인 시점
  // 값이라 승격·강등 직후에는 낡는다. 신원을 모르면 소유자 전용 조작을 감춘다(fail-closed).
  const myRow = me ? accounts.find((a) => a.email === me.email) : undefined
  const canManage = (myRow?.role ?? me?.role) === 'OWNER'

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError('')
    try {
      setAccounts(await fetchAPI<AdminAccount[]>('/admin/accounts'))
    } catch (e) {
      setLoadError(errorMessage(e, '운영자 목록을 불러오지 못했습니다.'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void fetchCurrentAccount().then(setMe)
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  function resetBanners() {
    setActionError('')
    setActionSuccess('')
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    resetBanners()
    setCreating(true)
    try {
      await fetchAPI('/admin/accounts', { method: 'POST', body: JSON.stringify(form) })
      setActionSuccess(`${form.email} 계정을 만들었습니다. 비밀번호를 본인에게 직접 전달해 주세요.`)
      setForm({ email: '', name: '', role: 'OPERATOR', password: '' })
      setShowCreate(false)
      await load()
    } catch (e) {
      setActionError(errorMessage(e, '계정 생성에 실패했습니다.'))
    } finally {
      setCreating(false)
    }
  }

  async function patchAccount(account: AdminAccount, patch: Record<string, unknown>, success: string) {
    resetBanners()
    setBusyId(account.id)
    try {
      await fetchAPI(`/admin/accounts/${account.id}`, {
        method: 'PATCH',
        body: JSON.stringify(patch),
      })
      setActionSuccess(success)
      await load()
    } catch (e) {
      setActionError(errorMessage(e, '변경에 실패했습니다.'))
    } finally {
      setBusyId(null)
    }
  }

  async function handleResetPassword(e: React.FormEvent) {
    e.preventDefault()
    if (!resetTarget) return
    resetBanners()
    setResetting(true)
    try {
      await fetchAPI(`/admin/accounts/${resetTarget.id}/password`, {
        method: 'POST',
        body: JSON.stringify({ password: resetPassword }),
      })
      setActionSuccess(`${resetTarget.email}의 비밀번호를 재설정했습니다. 본인에게 직접 전달해 주세요.`)
      setResetTarget(null)
      setResetPassword('')
    } catch (e) {
      setActionError(errorMessage(e, '비밀번호 재설정에 실패했습니다.'))
    } finally {
      setResetting(false)
    }
  }

  const activeOwners = accounts.filter((a) => a.role === 'OWNER' && a.is_active).length

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="admin-eyebrow">운영 설정</p>
          <h1 className="title2 mt-1 text-slate-900">운영자 계정</h1>
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-slate-600">
            Admin 콘솔에 로그인할 수 있는 운영자를 관리합니다. 계정을 추가·정지하는 조작은
            소유자만 할 수 있고, 모든 변경은 감사 로그에 남습니다.
          </p>
        </div>
        {canManage && (
          <button
            type="button"
            onClick={() => {
              resetBanners()
              setShowCreate((v) => !v)
            }}
            className="shrink-0 rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-800"
          >
            {showCreate ? '닫기' : '운영자 추가'}
          </button>
        )}
      </header>

      {!canManage && (
        <p className="mt-4 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
          현재 계정은 <strong>운영자</strong> 권한입니다. 계정 추가·정지·비밀번호 재설정은 소유자에게 요청해 주세요.
        </p>
      )}

      {actionError && (
        <p role="alert" className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {actionError}
        </p>
      )}
      {actionSuccess && (
        <p role="status" className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
          {actionSuccess}
        </p>
      )}

      {showCreate && canManage && (
        <form onSubmit={handleCreate} className="admin-panel mt-5 space-y-4 p-5">
          <h2 className="title3 text-slate-900">새 운영자 추가</h2>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block text-sm">
              <span className="font-medium text-slate-700">이메일</span>
              <input
                type="email"
                required
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
                placeholder="teammate@motionlabs.kr"
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
            <label className="block text-sm">
              <span className="font-medium text-slate-700">이름</span>
              <input
                type="text"
                required
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="예: 김운영"
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
            <label className="block text-sm">
              <span className="font-medium text-slate-700">권한</span>
              <select
                value={form.role}
                onChange={(e) => setForm({ ...form, role: e.target.value })}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              >
                <option value="OPERATOR">운영자 — {ROLE_HINTS.OPERATOR}</option>
                <option value="OWNER">소유자 — {ROLE_HINTS.OWNER}</option>
              </select>
            </label>
            <label className="block text-sm">
              <span className="font-medium text-slate-700">초기 비밀번호</span>
              <input
                type="password"
                required
                minLength={MIN_PASSWORD_LENGTH}
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                placeholder={`${MIN_PASSWORD_LENGTH}자 이상`}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
          </div>
          <p className="text-xs text-slate-500">
            비밀번호는 저장 후 다시 볼 수 없습니다. 만든 값을 본인에게 직접 전달하고, 첫 로그인 후
            바꾸도록 안내해 주세요.
          </p>
          <button
            type="submit"
            disabled={creating}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {creating ? '만드는 중...' : '계정 만들기'}
          </button>
        </form>
      )}

      <section className="admin-panel mt-6 overflow-hidden">
        {loading ? (
          <p className="px-6 py-10 text-center text-sm text-slate-500">불러오는 중...</p>
        ) : loadError ? (
          <div className="px-6 py-10 text-center">
            <p role="alert" className="text-sm text-red-600">{loadError}</p>
            <button
              type="button"
              onClick={() => void load()}
              className="mt-3 rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
            >
              다시 시도
            </button>
          </div>
        ) : accounts.length === 0 ? (
          <p className="px-6 py-10 text-center text-sm text-slate-500">등록된 운영자가 없습니다.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-slate-200 bg-slate-50">
                <tr>
                  <th className="px-6 py-3 text-left font-medium text-slate-600">운영자</th>
                  <th className="px-4 py-3 text-left font-medium text-slate-600">권한</th>
                  <th className="px-4 py-3 text-left font-medium text-slate-600">상태</th>
                  <th className="px-4 py-3 text-left font-medium text-slate-600">마지막 로그인</th>
                  <th className="px-4 py-3" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {accounts.map((account) => {
                  const isMe = me?.email === account.email
                  const busy = busyId === account.id
                  // 마지막 활성 소유자와 자기 자신은 백엔드가 거부한다 — 버튼부터 막아
                  // 눌러본 뒤 알게 되는 구조를 피한다.
                  const isLastActiveOwner =
                    account.role === 'OWNER' && account.is_active && activeOwners <= 1
                  const lockReason = isMe
                    ? '자기 계정은 변경할 수 없습니다'
                    : isLastActiveOwner
                      ? '마지막 소유자입니다'
                      : ''
                  return (
                    <tr key={account.id} className={account.is_active ? '' : 'bg-slate-50/60'}>
                      <td className="px-6 py-4">
                        <div className="font-medium text-slate-900">
                          {account.name}
                          {isMe && <span className="ml-2 text-xs font-normal text-blue-600">내 계정</span>}
                        </div>
                        <div className="text-xs text-slate-500">{account.email}</div>
                      </td>
                      <td className="px-4 py-4">
                        <span
                          className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ${
                            account.role === 'OWNER'
                              ? 'bg-violet-100 text-violet-700'
                              : 'bg-slate-100 text-slate-700'
                          }`}
                        >
                          {ROLE_LABELS[account.role] ?? account.role}
                        </span>
                      </td>
                      <td className="px-4 py-4">
                        <span
                          className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ${
                            account.is_active ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-200 text-slate-600'
                          }`}
                        >
                          {account.is_active ? '활성' : '정지'}
                        </span>
                      </td>
                      <td className="px-4 py-4 text-slate-600">{formatDateTime(account.last_login_at)}</td>
                      <td className="px-4 py-4">
                        {canManage && (
                          <div className="flex flex-wrap justify-end gap-2">
                            <button
                              type="button"
                              disabled={busy || Boolean(lockReason)}
                              title={lockReason || undefined}
                              onClick={() =>
                                void patchAccount(
                                  account,
                                  { role: account.role === 'OWNER' ? 'OPERATOR' : 'OWNER' },
                                  `${account.email}의 권한을 ${
                                    account.role === 'OWNER' ? '운영자' : '소유자'
                                  }로 바꿨습니다.`,
                                )
                              }
                              className="rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs text-slate-700 hover:bg-slate-50 disabled:opacity-40"
                            >
                              {account.role === 'OWNER' ? '운영자로' : '소유자로'}
                            </button>
                            <button
                              type="button"
                              disabled={busy || (account.is_active && Boolean(lockReason))}
                              title={account.is_active ? lockReason || undefined : undefined}
                              onClick={() =>
                                void patchAccount(
                                  account,
                                  { is_active: !account.is_active },
                                  account.is_active
                                    ? `${account.email} 계정을 정지했습니다. 열려 있던 세션도 곧 끊깁니다.`
                                    : `${account.email} 계정을 다시 활성화했습니다.`,
                                )
                              }
                              className="rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs text-slate-700 hover:bg-slate-50 disabled:opacity-40"
                            >
                              {account.is_active ? '정지' : '활성화'}
                            </button>
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => {
                                resetBanners()
                                setResetTarget(account)
                                setResetPassword('')
                              }}
                              className="rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs text-slate-700 hover:bg-slate-50 disabled:opacity-40"
                            >
                              비밀번호 재설정
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {resetTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4">
          <form onSubmit={handleResetPassword} className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="title3 text-slate-900">비밀번호 재설정</h2>
            <p className="mt-1 text-sm text-slate-600">
              <strong>{resetTarget.email}</strong>의 비밀번호를 새로 정합니다. 기존 비밀번호는 즉시 쓸 수 없게 됩니다.
            </p>
            <label className="mt-4 block text-sm">
              <span className="font-medium text-slate-700">새 비밀번호</span>
              <input
                type="password"
                required
                autoFocus
                minLength={MIN_PASSWORD_LENGTH}
                value={resetPassword}
                onChange={(e) => setResetPassword(e.target.value)}
                placeholder={`${MIN_PASSWORD_LENGTH}자 이상`}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => {
                  setResetTarget(null)
                  setResetPassword('')
                }}
                className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-700"
              >
                취소
              </button>
              <button
                type="submit"
                disabled={resetting}
                className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {resetting ? '변경 중...' : '재설정'}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  )
}

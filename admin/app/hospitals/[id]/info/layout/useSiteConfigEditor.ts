'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { ADMIN_COPY } from '@/lib/admin-copy'
import { ApiError, fetchAPI } from '@/lib/api'
import { isExpectedOperatorRequestFailure } from '@/lib/operations-journey'
import { platformSiteHost } from '@/lib/platform-domain'
import {
  buildOverrides,
  overridesCompareKey,
  discardDraftPath,
  draftExpectation,
  previewUrl,
  readRow,
  readSiteConfigFailure,
  readSiteConfigState,
  readIssues,
  restorePath,
  siteConfigPath,
  type ResolvedSiteConfig,
  type SiteConfigIssue,
  type SiteConfigState,
} from '@/lib/site-config'
import type { SiteLayout } from '@/lib/site-layout'

const COPY = ADMIN_COPY.siteEditor

export type EditorBusy = 'save' | 'publish' | 'discard' | 'restore' | 'reset' | 'preview' | null

export interface EditorWorking {
  layout: SiteLayout
  resolved: ResolvedSiteConfig
}

export interface PreviewLink {
  url: string
  expiresAt: string
  draftId: string
  revision: number
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

/** 편집을 시작할 판 — 임시 저장본, 없으면 공개 판, 없으면 병원 레이아웃의 기본값. */
function startingPoint(state: SiteConfigState): EditorWorking | null {
  const row = state.draft ?? state.published
  if (row) return { layout: row.layout, resolved: clone(row.resolved) }
  if (!state.site_layout) return null
  return { layout: state.site_layout, resolved: clone(state.defaults[state.site_layout]) }
}

function overridesKey(working: EditorWorking | null, state: SiteConfigState | null, asStored = false): string {
  if (!working || !state) return ''
  return overridesCompareKey(working.layout, working.resolved, state.defaults[working.layout], asStored)
}

/**
 * 레이아웃 편집기의 상태와 쓰기. 쓰기는 모두 지금 초안의 `{draft_id, revision}`을 싣고(서버가 다르면 409),
 * 공개 반영은 시도마다 요청 키 하나를 만든다 — 같은 초안을 다시 누르면 같은 키(재요청은 같은 결과),
 * 초안이 바뀌면 새 키다(같은 키로 다른 초안을 보내면 서버가 409로 막는다).
 */
export function useSiteConfigEditor(hospitalId: string) {
  const [state, setState] = useState<SiteConfigState | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [working, setWorking] = useState<EditorWorking | null>(null)
  const [busy, setBusy] = useState<EditorBusy>(null)
  const [issues, setIssues] = useState<SiteConfigIssue[]>([])
  const [warnings, setWarnings] = useState<SiteConfigIssue[]>([])
  const [notice, setNotice] = useState<{ tone: 'ok' | 'warn' | 'error'; text: string } | null>(null)
  const [preview, setPreview] = useState<PreviewLink | null>(null)
  const [previewDisabled, setPreviewDisabled] = useState(false)
  const publishKey = useRef<{ key: string; draftId: string; revision: number } | null>(null)
  const loadSeq = useRef(0)

  const adopt = useCallback((next: SiteConfigState, extraWarnings: SiteConfigIssue[] = []) => {
    setState(next)
    setWorking(startingPoint(next))
    setWarnings([...next.warnings, ...extraWarnings.filter((w) => !next.warnings.some((n) => n.code === w.code && n.path === w.path))])
    setIssues([])
    setPreviewDisabled(!next.preview_enabled)
  }, [])

  const load = useCallback(
    async (extraWarnings: SiteConfigIssue[] = []) => {
      const seq = ++loadSeq.current
      setLoadError(null)
      try {
        const next = readSiteConfigState(await fetchAPI<unknown>(siteConfigPath(hospitalId)))
        if (seq !== loadSeq.current) return
        if (!next) {
          setLoadError(COPY.loadFailed)
          return
        }
        adopt(next, extraWarnings)
      } catch (cause: unknown) {
        if (!isExpectedOperatorRequestFailure(cause)) throw cause
        if (seq === loadSeq.current) setLoadError(cause instanceof ApiError && cause.message ? cause.message : COPY.loadFailed)
      }
    },
    [adopt, hospitalId],
  )

  useEffect(() => {
    void load()
  }, [load])

  const savedKey = useMemo(() => (state ? overridesKey(state ? startingPoint(state) : null, state, true) : ''), [state])
  const dirty = Boolean(working && state && overridesKey(working, state) !== savedKey)
  const draft = state?.draft ?? null

  // 초안이 바뀌면 옛 미리보기 주소는 열리지 않는다(토큰이 초안·revision에 묶인다).
  useEffect(() => {
    if (preview && (!draft || draft.id !== preview.draftId || draft.revision !== preview.revision)) setPreview(null)
  }, [draft, preview])

  // 저장하지 않고 떠나지 않게.
  useEffect(() => {
    if (!dirty) return
    const guard = (event: BeforeUnloadEvent) => {
      event.preventDefault()
    }
    window.addEventListener('beforeunload', guard)
    return () => window.removeEventListener('beforeunload', guard)
  }, [dirty])

  // 쓰기 뒤 다시 불러오기까지 잠금(busy)을 유지한다 — 재조회가 늦게 오면 그 사이 입력한 변경을 덮는다.
  // 앱 안 링크(Next Link)는 문서를 떠나지 않아 beforeunload가 울리지 않는다 — 링크 누름을 먼저 가로챈다.
  useEffect(() => {
    if (!dirty) return
    const guard = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
      const anchor = (event.target as Element | null)?.closest?.('a[href]') as HTMLAnchorElement | null
      if (!anchor || anchor.target === '_blank' || anchor.hasAttribute('download')) return
      const next = new URL(anchor.href, window.location.href)
      if (next.origin !== window.location.origin) return
      if (next.pathname === window.location.pathname && next.search === window.location.search) return
      if (!window.confirm(COPY.leaveConfirm)) {
        event.preventDefault()
        event.stopPropagation()
      }
    }
    document.addEventListener('click', guard, true)
    return () => document.removeEventListener('click', guard, true)
  }, [dirty])

  async function reloadAfter<T extends { warnings?: unknown } | null>(response: T): Promise<T> {
    await load(readIssues(response?.warnings))
    return response
  }

  async function run<T>(kind: Exclude<EditorBusy, null>, request: () => Promise<T>): Promise<T | null> {
    setBusy(kind)
    setNotice(null)
    try {
      return await request()
    } catch (cause: unknown) {
      if (!isExpectedOperatorRequestFailure(cause)) throw cause
      const failure =
        cause instanceof ApiError
          ? readSiteConfigFailure(cause.status, cause.message, cause.detail)
          : { kind: 'other' as const, message: COPY.loadFailed }
      if (failure.kind === 'draft_changed') {
        await load()
        setNotice({ tone: 'warn', text: COPY.conflict })
      } else if (failure.kind === 'invalid') {
        setIssues(failure.errors)
        setNotice({ tone: 'error', text: COPY.invalid })
      } else if (failure.kind === 'preview_disabled') {
        setPreviewDisabled(true)
        setNotice({ tone: 'warn', text: COPY.previewNotReady })
      } else {
        setNotice({ tone: 'error', text: failure.message })
      }
      return null
    } finally {
      setBusy(null)
    }
  }

  async function save() {
    if (!state || !working) return
    const body = {
      layout: working.layout,
      config: buildOverrides(working.resolved, state.defaults[working.layout], working.layout),
      ...draftExpectation(state.draft),
    }
    const response = await run('save', () =>
      fetchAPI<{ draft: unknown; warnings: unknown }>(siteConfigPath(hospitalId, '/draft'), {
        method: 'PUT',
        body: JSON.stringify(body),
      }),
    )
    const row = response ? readRow(response.draft) : null
    if (!response || !row) return
    const next = { ...state, draft: row }
    setState(next)
    setWorking({ layout: row.layout, resolved: clone(row.resolved) })
    setIssues([])
    setWarnings([...state.warnings.filter((w) => w.path === 'layout'), ...readIssues(response.warnings)])
    setNotice({ tone: 'ok', text: COPY.saved })
  }

  async function publish() {
    if (!state?.draft || dirty) return
    const current = state.draft
    if (!publishKey.current || publishKey.current.draftId !== current.id || publishKey.current.revision !== current.revision) {
      publishKey.current = { key: crypto.randomUUID(), draftId: current.id, revision: current.revision }
    }
    const key = publishKey.current.key
    const response = await run('publish', () =>
      fetchAPI<{ published: unknown; warnings: unknown }>(siteConfigPath(hospitalId, '/draft/publish'), {
        method: 'POST',
        headers: { 'Idempotency-Key': key },
        body: JSON.stringify(draftExpectation(current)),
      }).then(reloadAfter),
    )
    if (!response) return
    publishKey.current = null
    const row = readRow(response.published)
    setNotice({ tone: 'ok', text: COPY.published.replace('{version}', String(row?.version ?? '')) })
    return true
  }

  async function discard() {
    if (!state?.draft) return
    const current = state.draft
    const response = await run('discard', () =>
      fetchAPI<{ warnings?: unknown }>(discardDraftPath(hospitalId, current), { method: 'DELETE' }).then(reloadAfter),
    )
    if (response === null) return
    setNotice({ tone: 'ok', text: COPY.discarded })
    return true
  }

  async function restore(versionId: string) {
    if (!state || dirty) return
    const response = await run('restore', () =>
      fetchAPI<{ warnings: unknown }>(restorePath(hospitalId, versionId), {
        method: 'POST',
        body: JSON.stringify(draftExpectation(state.draft)),
      }).then(reloadAfter),
    )
    if (!response) return
    setNotice({ tone: 'ok', text: COPY.restored })
    return true
  }

  async function reset(layout: SiteLayout) {
    if (!state) return
    const response = await run('reset', () =>
      fetchAPI<{ warnings: unknown }>(siteConfigPath(hospitalId, '/reset'), {
        method: 'POST',
        body: JSON.stringify({ layout, ...draftExpectation(state.draft) }),
      }).then(reloadAfter),
    )
    if (!response) return
    setNotice({ tone: 'ok', text: COPY.resetDone })
    return true
  }

  async function requestPreview() {
    if (!state?.draft || dirty) return
    const current = state.draft
    const response = await run('preview', () =>
      fetchAPI<{ preview_path?: unknown; expires_at?: unknown }>(siteConfigPath(hospitalId, '/preview-token'), {
        method: 'POST',
        body: JSON.stringify(draftExpectation(current)),
      }),
    )
    if (!response) return
    const url = previewUrl(platformSiteHost(), response.preview_path)
    if (!url || typeof response.expires_at !== 'string') {
      setNotice({ tone: 'error', text: COPY.previewFailed })
      return
    }
    setPreview({ url, expiresAt: response.expires_at, draftId: current.id, revision: current.revision })
  }

  function revert() {
    if (!state) return
    setWorking(startingPoint(state))
    setIssues([])
    setNotice(null)
  }

  return {
    state,
    loadError,
    working,
    setWorking,
    dirty,
    busy,
    issues,
    warnings,
    notice,
    preview,
    previewDisabled,
    reload: load,
    save,
    publish,
    discard,
    restore,
    reset,
    requestPreview,
    revert,
  }
}

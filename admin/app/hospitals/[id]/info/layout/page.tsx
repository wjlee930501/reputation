'use client'

import Link from 'next/link'
import { useParams } from 'next/navigation'
import { useEffect, useRef, useState } from 'react'

import { ConfirmDialog } from '@/app/_components/ConfirmDialog'
import { ADMIN_COPY } from '@/lib/admin-copy'
import { isPubliclyServing } from '@/lib/public-service-state'
import {
  buildOverrides,
  showsBOnlyFields,
  changeLabels,
  shownCopyKeys,
  LINE_BREAK_PATHS,
  lineBreakPathShown,
  issuesFor,
  shownSectionTitle,
  type SectionId,
  type SiteSection,
} from '@/lib/site-config'
import { SITE_LAYOUTS, type SiteLayout } from '@/lib/site-layout'
import { useHospitalHeader } from '../../hospital-context'
import { FaqSettings, HeroSettings, SectionList, SectionSettings } from './SectionPanels'
import {
  CareFlowSettings,
  CarePrinciplesSettings,
  CopyNotes,
  DoctorSettings,
  HeroImageSettings,
  LineBreaksSettings,
  PageWideSettings,
  TreatmentsSettings,
  VisitSettings,
} from './SectionExtras'
import { useSiteConfigEditor } from './useSiteConfigEditor'

const COPY = ADMIN_COPY.siteEditor
const LAYOUT_NAMES = ADMIN_COPY.siteLayout.options

function when(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', dateStyle: 'short', timeStyle: 'short' })
}

function Card({ title, children, right }: { title: string; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold text-slate-800">{title}</h3>
        <span className="flex-1" />
        {right}
      </div>
      {children}
    </section>
  )
}

/** 고른 섹션의 세부 설정 묶음 — 섹션 기본 설정 아래 구분선 하나. */
function Extra({ title, children }: { title?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-4 border-t border-slate-100 pt-4">
      {title && <h4 className="text-sm font-semibold text-slate-800">{title}</h4>}
      {children}
    </div>
  )
}

const button =
  'inline-flex min-h-9 items-center rounded-lg border border-slate-300 bg-white px-3 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50'
const primary =
  'inline-flex min-h-9 items-center rounded-lg bg-blue-600 px-4 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50'

/** 공개 페이지 레이아웃 편집 — 섹션·첫 화면·자주 묻는 질문, 임시 저장 → 미리보기 → 공개 반영 → 이력·복원. */
export default function SiteLayoutEditorPage() {
  const { id: hospitalId } = useParams<{ id: string }>()
  const { hospital } = useHospitalHeader()
  const editor = useSiteConfigEditor(hospitalId)
  const { state, working, busy, issues } = editor
  const [selected, setSelected] = useState<SectionId | null>('hero')
  const [dialog, setDialog] = useState<'publish' | 'discard' | 'reset' | null>(null)
  const [resetLayout, setResetLayout] = useState<SiteLayout | null>(null)
  // 같은 화면 미리보기(iframe). 토큰은 저장할 때마다 무효가 되므로 열려 있으면 새 판마다 한 번 다시 받는다.
  const [previewOpen, setPreviewOpen] = useState(false)
  const autoRequested = useRef<string | null>(null)
  const savedDraft = editor.state?.draft ?? null
  const draftKey = savedDraft ? `${savedDraft.id}:${savedDraft.revision}` : null
  useEffect(() => {
    if (!previewOpen || !draftKey || editor.dirty || editor.preview || editor.busy || editor.previewDisabled) return
    if (autoRequested.current === draftKey) return
    autoRequested.current = draftKey
    void editor.requestPreview()
  }, [previewOpen, draftKey, editor])

  const back = (
    <Link href={`/hospitals/${hospitalId}/info#info-brand`} className="text-sm font-semibold text-blue-700 hover:underline">
      ← {COPY.back}
    </Link>
  )

  if (editor.loadError) {
    return (
      <div className="space-y-3 p-4 sm:p-6 lg:p-8">
        {back}
        <p className="text-sm font-semibold text-red-700">{editor.loadError}</p>
        <button type="button" className={button} onClick={() => void editor.reload()}>
          {COPY.retry}
        </button>
      </div>
    )
  }
  if (!state) return <p className="p-4 text-sm text-slate-500 sm:p-6 lg:p-8">{COPY.loading}</p>
  if (!working) {
    return (
      <div className="space-y-3 p-4 sm:p-6 lg:p-8">
        {back}
        <p className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">{COPY.chooseLayoutFirst}</p>
      </div>
    )
  }

  const defaults = state.defaults[working.layout]
  const defaultTitleOf = (id: SectionId) => defaults.sections.find((section) => section.id === id)?.title ?? null
  const hospitalTitles = state.hospital_section_titles
  const overrides = buildOverrides(working.resolved, defaults, working.layout)
  const bOnly = showsBOnlyFields(working.layout)
  const live = isPubliclyServing(hospital)
  const locked = busy !== null
  const sections = working.resolved.sections
  const current = sections.find((section) => section.id === selected) ?? null
  const draft = state.draft
  const published = state.published
  const hero = working.resolved.hero as Parameters<typeof HeroSettings>[0]['hero']
  const faq = working.resolved.faq as Parameters<typeof FaqSettings>[0]['faq']
  // 오류는 한 곳에만 — 섹션 칸(섹션 설정 맨 위), 페이지 전체, 그 밖은 위쪽 상자.
  const unplaced = issuesFor(issues, overrides, null)
  const group = (key: string): Record<string, unknown> => {
    const value = working.resolved[key]
    return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
  }
  const copyValues = group('copy') as Record<string, string>
  const lineBreaks = (group('hero').line_breaks ?? {}) as Record<string, { pc?: string | null; mobile?: string | null }>
  const setLineBreaks = (next: typeof lineBreaks) => update({ hero: { ...group('hero'), line_breaks: next } })

  // 지금 상태 위에 합친다 — 같은 순간의 두 변경(다른 묶음)이 서로를 지우지 않게.
  const update = (patch: Partial<typeof working.resolved>) =>
    editor.setWorking((prev) => (prev ? { ...prev, resolved: { ...prev.resolved, ...patch } } : prev))
  const setSections = (next: SiteSection[]) => update({ sections: next })

  const canPublish = Boolean(draft) && !editor.dirty && !locked

  return (
    <div className="space-y-4 p-4 sm:p-6 lg:p-8">
      {back}
      <div className="space-y-1">
        <h2 className="text-lg font-semibold text-slate-900">{COPY.title}</h2>
        <p className="break-keep text-xs text-slate-500">{COPY.intro}</p>
      </div>

      {/* 상태 줄 + 쓰기 버튼 */}
      <div className="sticky top-0 z-10 space-y-2 rounded-xl border border-slate-200 bg-white/95 p-3 backdrop-blur">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-600">
          <span className="font-semibold text-slate-800">{LAYOUT_NAMES[working.layout].name}</span>
          <span>
            {published
              ? COPY.statusPublished.replace('{version}', String(published.version ?? '')).replace('{when}', when(published.published_at))
              : COPY.statusNoPublished.replace('{layout}', working.layout)}
          </span>
          <span>{draft ? COPY.statusDraft.replace('{when}', when(draft.updated_at)) : COPY.statusNoDraft}</span>
          {editor.dirty && <span className="font-semibold text-amber-700">{COPY.unsaved}</span>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className={primary} disabled={!editor.dirty || locked} onClick={() => void editor.save()}>
            {busy === 'save' ? COPY.saving : COPY.save}
          </button>
          {editor.dirty && (
            <button type="button" className={button} disabled={locked} onClick={editor.revert}>
              {COPY.revert}
            </button>
          )}
          <button
            type="button"
            className={button}
            disabled={!draft || editor.dirty || locked || editor.previewDisabled}
            onClick={() => {
              setPreviewOpen(true)
              autoRequested.current = draftKey
              void editor.requestPreview()
            }}
          >
            {editor.dirty ? COPY.previewNeedsSave : COPY.preview}
          </button>
          <span className="flex-1" />
          {draft && (
            <button type="button" className={button} disabled={locked} onClick={() => setDialog('discard')}>
              {COPY.discard}
            </button>
          )}
          <button type="button" className={primary} disabled={!canPublish} onClick={() => setDialog('publish')}>
            {busy === 'publish' ? COPY.publishing : COPY.publish}
          </button>
        </div>
        {editor.previewDisabled && <p className="text-xs text-slate-500">{COPY.previewNotReady}</p>}
        {draft && editor.dirty && <p className="text-xs text-slate-500">{COPY.publishNeedsSave}</p>}
        {editor.notice && (
          <p
            role="status"
            className={`text-sm font-semibold ${
              editor.notice.tone === 'ok' ? 'text-green-700' : editor.notice.tone === 'warn' ? 'text-amber-700' : 'text-red-700'
            }`}
          >
            {editor.notice.text}
          </p>
        )}
      </div>

      {previewOpen && (
        <Card
          title={COPY.previewTitle}
          right={
            <>
              {editor.preview && (
                <a href={editor.preview.url} target="_blank" rel="noopener noreferrer" className="text-sm font-semibold text-blue-700 underline">
                  {COPY.previewNewTab}
                </a>
              )}
              {/* 미리보기 안에서 링크를 누르면 iframe이 공개 페이지로 넘어갈 수 있다 — 새 토큰으로 처음 화면을 다시 띄운다. */}
              <button
                type="button"
                className={button}
                disabled={!savedDraft || editor.dirty || editor.busy !== null || editor.previewDisabled}
                onClick={() => void editor.requestPreview()}
              >
                {COPY.previewReload}
              </button>
              <button type="button" className={button} onClick={() => setPreviewOpen(false)}>
                {COPY.previewClose}
              </button>
            </>
          }
        >
          {editor.preview ? (
            <iframe
              key={editor.preview.url}
              src={editor.preview.url}
              title={COPY.previewTitle}
              sandbox="allow-scripts allow-same-origin"
              referrerPolicy="no-referrer"
              className="h-[720px] w-full rounded-lg border border-slate-200"
            />
          ) : (
            <p className="text-xs text-slate-500">{editor.dirty ? COPY.previewNeedsSave : COPY.loading}</p>
          )}
          <p className="text-[11px] text-slate-500">{COPY.previewFrameHint}</p>
        </Card>
      )}

      {(editor.warnings.length > 0 || unplaced.length > 0) && (
        <div className="space-y-1 rounded-lg border border-amber-300 bg-amber-50 p-3">
          <p className="text-xs font-semibold text-amber-900">{COPY.warningsTitle}</p>
          <ul className="space-y-0.5 text-xs text-amber-900">
            {[...unplaced, ...editor.warnings].map((item, index) => (
              <li key={`${item.code}:${item.path}:${index}`}>{item.message}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,320px)_minmax(0,1fr)]">
        <Card title={COPY.sectionsTitle}>
          <p className="break-keep text-[11px] text-slate-500">{COPY.sectionsHint}</p>
          <SectionList
            sections={sections}
            selected={selected}
            onSelect={setSelected}
            onChange={setSections}
            issueCount={(id) => issuesFor(issues, overrides, id).length}
            titleOf={(section) => shownSectionTitle(section, defaultTitleOf(section.id), hospitalTitles)}
            disabled={locked}
          />
        </Card>

        <Card title={COPY.sectionSettings}>
          {current ? (
            <div className="space-y-6">
              <SectionSettings
                section={current}
                defaultTitle={defaultTitleOf(current.id)}
                placeholderTitle={shownSectionTitle({ id: current.id, title: null }, defaultTitleOf(current.id), hospitalTitles)}
                issues={issuesFor(issues, overrides, current.id)}
                onChange={(next) => setSections(sections.map((section) => (section.id === next.id ? next : section)))}
                disabled={locked}
                layout={working.layout}
              />
              {current.id === 'hero' && (
                <Extra title={COPY.heroTitle}>
                  <HeroSettings hero={hero} issues={[]} onChange={(next) => update({ hero: next })} disabled={locked} showBlocks={bOnly} />
                  <HeroImageSettings hero={group('hero')} onChange={(next) => update({ hero: next })} disabled={locked} />
                  <LineBreaksSettings
                    paths={['hero.headline', 'hero.description']}
                    hospital={hospital}
                    lineBreaks={lineBreaks}
                    issues={[]}
                    onChange={setLineBreaks}
                    disabled={locked}
                  />
                </Extra>
              )}
              {current.id === 'faq' && (
                <Extra title={COPY.faqTitle}>
                  <FaqSettings hospitalId={hospitalId} faq={faq} issues={[]} onChange={(next) => update({ faq: next })} disabled={locked} />
                </Extra>
              )}
              {/* 진료 항목 설명 보이기·모양은 B에만 그려진다 — A·C에는 세부 설정이 없다. */}
              {current.id === 'treatments' && bOnly && (
                <Extra>
                  <TreatmentsSettings value={group('treatments')} onChange={(next) => update({ treatments: next })} disabled={locked} />
                </Extra>
              )}
              {current.id === 'doctor' && (
                <Extra>
                  <DoctorSettings value={group('doctor')} onChange={(next) => update({ doctor: next })} disabled={locked} />
                </Extra>
              )}
              {current.id === 'care_principles' && (
                <Extra>
                  <CarePrinciplesSettings value={group('care_principles')} onChange={(next) => update({ care_principles: next })} disabled={locked} />
                  <LineBreaksSettings
                    paths={LINE_BREAK_PATHS.filter((path) => path.startsWith('care_principles.') && lineBreakPathShown(working.layout, path))}
                    hospital={hospital}
                    lineBreaks={lineBreaks}
                    issues={[]}
                    onChange={setLineBreaks}
                    disabled={locked}
                  />
                </Extra>
              )}
              {current.id === 'care_flow' && (
                <Extra>
                  <CareFlowSettings
                    value={group('care_flow')}
                    defaults={(defaults.care_flow ?? {}) as Record<string, unknown>}
                    issues={[]}
                    onChange={(next) => update({ care_flow: next })}
                    disabled={locked}
                  />
                </Extra>
              )}
              {current.id === 'visit' && bOnly && (
                <Extra>
                  <VisitSettings value={group('visit')} onChange={(next) => update({ visit: next })} disabled={locked} />
                </Extra>
              )}
              {shownCopyKeys(current.id).length > 0 && (
                <Extra>
                  <CopyNotes
                    keys={shownCopyKeys(current.id)}
                    copy={copyValues}
                    issues={[]}
                    onChange={(next) => update({ copy: next })}
                    disabled={locked}
                  />
                </Extra>
              )}
            </div>
          ) : (
            <p className="text-xs text-slate-500">{COPY.pickSection}</p>
          )}
        </Card>
      </div>

      <Card title={COPY.pageTitle}>
        <PageWideSettings
          hospitalId={hospitalId}
          resolved={working.resolved}
          issues={issuesFor(issues, overrides, 'page')}
          onChange={(patch) => update(patch)}
          disabled={locked}
        />
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={COPY.historyTitle}>
          {state.history.length === 0 ? (
            <p className="text-xs text-slate-500">{COPY.historyEmpty}</p>
          ) : (
            <ul className="divide-y divide-slate-100">
              {state.history.map((entry) => (
                <li key={entry.id} className="flex flex-wrap items-center gap-2 py-2 text-xs text-slate-700">
                  <span className="font-semibold">{COPY.historyVersion.replace('{version}', String(entry.version))}</span>
                  <span>{LAYOUT_NAMES[entry.layout].name}</span>
                  <span className="text-slate-500">
                    {when(entry.published_at)} · {entry.published_by ?? '—'}
                  </span>
                  {entry.state === 'PUBLISHED' && (
                    <span className="rounded bg-green-100 px-1.5 py-0.5 font-semibold text-green-800">{COPY.historyCurrent}</span>
                  )}
                  <span className="flex-1" />
                  <button type="button" className={button} disabled={locked || editor.dirty} onClick={() => void editor.restore(entry.id)}>
                    {COPY.restore}
                  </button>
                  <span className="w-full text-slate-500">
                    {entry.version === 1
                      ? COPY.historyFirst
                      : entry.change_summary.length > 0 &&
                        COPY.historyChanged.replace('{keys}', changeLabels(entry.change_summary, COPY).join(', '))}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title={COPY.resetTitle}>
          <p className="break-keep text-xs text-slate-600">{COPY.resetBody}</p>
          <fieldset className="flex flex-wrap gap-1.5">
            <legend className="mb-1 text-xs font-semibold text-slate-700">{COPY.resetLayout}</legend>
            {SITE_LAYOUTS.map((layout) => (
              <button
                key={layout}
                type="button"
                aria-pressed={(resetLayout ?? working.layout) === layout}
                onClick={() => setResetLayout(layout)}
                className={`min-h-9 rounded-lg border px-3 text-xs font-semibold ${
                  (resetLayout ?? working.layout) === layout ? 'border-blue-600 bg-blue-50 text-blue-700' : 'border-slate-300 bg-white text-slate-700'
                }`}
              >
                {LAYOUT_NAMES[layout].name}
              </button>
            ))}
          </fieldset>
          <button type="button" className={button} disabled={locked || editor.dirty} onClick={() => setDialog('reset')}>
            {COPY.resetConfirm}
          </button>
        </Card>
      </div>

      <ConfirmDialog
        open={dialog === 'publish'}
        tone="neutral"
        title={COPY.publishTitle}
        description={live ? COPY.publishBodyLive : COPY.publishBodyNotLive}
        confirmLabel={COPY.publish}
        busy={busy === 'publish'}
        onConfirm={() => void editor.publish().then((ok) => ok && setDialog(null))}
        onClose={() => setDialog(null)}
      />
      <ConfirmDialog
        open={dialog === 'discard'}
        title={COPY.discardTitle}
        description={COPY.discardBody}
        confirmLabel={COPY.discard}
        busy={busy === 'discard'}
        onConfirm={() => void editor.discard().then(() => setDialog(null))}
        onClose={() => setDialog(null)}
      />
      <ConfirmDialog
        open={dialog === 'reset'}
        tone="neutral"
        title={COPY.resetTitle}
        description={COPY.resetBody}
        confirmLabel={COPY.resetConfirm}
        busy={busy === 'reset'}
        onConfirm={() => void editor.reset(resetLayout ?? working.layout).then(() => setDialog(null))}
        onClose={() => setDialog(null)}
      />
    </div>
  )
}

'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  buildFlow,
  compactAnswers,
  ensureRowIds,
  focusScope,
  questionAnswered,
  questionDesc,
  questionTitle,
  resolveStep,
  type Answers,
  type FlowItem,
  type SurveySchema,
} from '@/lib/doctor-survey'
import { FieldList, resolveChange, type ChangeValue, type FieldContext } from './SurveyFields'

/**
 * 원장 사전 설문 화면 — 시작(동의) → 문항 → 제출 전 확인 → 완료.
 *
 * 입력하는 즉시(잠깐 멈춘 뒤) 서버에 저장한다. 다른 기기에서 같은 링크를 열어도 마지막 화면부터 이어 쓴다.
 * 근거 자료에는 '제출하기'를 누른 답만 들어간다. 제출 뒤에도 같은 링크에서 고쳐 다시 제출할 수 있다.
 */

interface SurveyData {
  hospital_name: string | null
  new_clinic: boolean
  schema: SurveySchema
  answers: Answers
  current_step: string | null
  respondent: string | null
  consented: boolean
  submitted_at: string | null
  submission_count: number
  has_unsubmitted_changes: boolean
}

type SaveState = 'idle' | 'saving' | 'saved' | 'error'

const SAVE_DELAY_MS = 900

function clock(iso: string | null): string {
  if (!iso) return ''
  const date = new Date(iso)
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}

async function readDetail(response: Response, fallback: string): Promise<string> {
  const payload = (await response.json().catch(() => ({}))) as { detail?: unknown }
  return typeof payload.detail === 'string' ? payload.detail : fallback
}

export default function SurveyApp({ token }: { token: string }) {
  const base = `/api/doctor-survey/${encodeURIComponent(token)}`
  const [state, setState] = useState<'loading' | 'ready' | 'gone' | 'error'>('loading')
  const [data, setData] = useState<SurveyData | null>(null)
  const [answers, setAnswers] = useState<Answers>({})
  const [stepKey, setStepKey] = useState('intro')
  const [saveState, setSaveState] = useState<SaveState>('idle')
  const [savedAt, setSavedAt] = useState<string | null>(null)
  const [consentChecked, setConsentChecked] = useState(false)
  const [respondent, setRespondent] = useState<string | null>(null)
  const [consented, setConsented] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [tocOpen, setTocOpen] = useState(false)
  const dirty = useRef(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const latest = useRef({ answers, stepKey })
  useEffect(() => {
    latest.current = { answers, stepKey }
  }, [answers, stepKey])

  useEffect(() => {
    let cancelled = false
    fetch(base, { cache: 'no-store' })
      .then(async (response) => {
        if (cancelled) return
        if (response.status === 404) {
          setState('gone')
          return
        }
        if (!response.ok) {
          setState('error')
          return
        }
        const payload = (await response.json()) as SurveyData
        setData(payload)
        setAnswers(ensureRowIds(payload.schema, payload.answers ?? {}))
        setRespondent(payload.respondent)
        setConsented(payload.consented)
        setConsentChecked(payload.consented)
        setSavedAt(null)
        const submittedClean = payload.submission_count > 0 && !payload.has_unsubmitted_changes
        setStepKey(submittedClean ? 'done' : payload.consented && payload.current_step ? payload.current_step : 'intro')
        setState('ready')
      })
      .catch(() => !cancelled && setState('error'))
    return () => {
      cancelled = true
    }
  }, [base])

  const schema = data?.schema
  const schemaVersion = schema?.version
  const newClinic = data?.new_clinic ?? false
  const flow = useMemo(() => (schema ? buildFlow(schema, answers, newClinic) : []), [schema, answers, newClinic])
  const index = resolveStep(flow, stepKey)
  const current: FlowItem | undefined = flow[index]
  const questions = flow.filter((item) => item.question)
  const questionIndex = questions.findIndex((item) => item.key === current?.key)

  // 저장은 한 번에 하나만 보낸다. 느린 요청이 나중에 도착해 새 답을 덮지 않게, 보내는 동안 바뀐 답은
  // 같은 반복에서 이어서 보낸다. 실패하면 멈추고 다음 입력 때 다시 보낸다.
  const inFlight = useRef<Promise<void> | null>(null)
  // 올리는 중인 파일 — 끝나기 전에 제출하면 그 파일이 제출본에서 빠진다.
  const uploads = useRef(new Set<Promise<void>>())
  const [uploading, setUploading] = useState(0)
  const trackUpload = useCallback((work: Promise<void>) => {
    uploads.current.add(work)
    setUploading(uploads.current.size)
    void work.finally(() => {
      uploads.current.delete(work)
      setUploading(uploads.current.size)
    })
  }, [])
  // 이 화면의 저장 순번 — 서버는 같은 화면에서 이미 받은 순번보다 작거나 같은 저장을 버린다(늦게 도착한 옛 답이
  // 새 답을 덮지 않게). 화면 id가 다르면(다른 기기·새로 연 화면) 비교하지 않으므로 기기 시계와 무관하다.
  const session = useRef('')
  const seq = useRef(0)
  const nextSeq = useCallback(() => {
    if (!session.current) {
      session.current = Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) => b.toString(16).padStart(2, '0')).join('')
    }
    seq.current += 1
    return { client_session: session.current, client_seq: seq.current }
  }, [])

  const save = useCallback((): Promise<void> => {
    if (inFlight.current) return inFlight.current
    const run = (async () => {
      while (dirty.current) {
        dirty.current = false
        setSaveState('saving')
        try {
          const response = await fetch(`${base}/answers`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              answers: compactAnswers(latest.current.answers),
              current_step: latest.current.stepKey,
              schema_version: schemaVersion,
              ...nextSeq(),
            }),
          })
          if (!response.ok) {
            dirty.current = true
            setSaveState('error')
            setError(await readDetail(response, '저장하지 못했습니다. 잠시 뒤 다시 시도해 주세요.'))
            return
          }
          const payload = (await response.json()) as { saved_at: string }
          setSavedAt(payload.saved_at)
          setSaveState('saved')
          setError(null)
        } catch {
          dirty.current = true
          setSaveState('error')
          return
        }
      }
    })().finally(() => {
      inFlight.current = null
    })
    inFlight.current = run
    return run
  }, [base, schemaVersion, nextSeq])


  const scheduleSave = useCallback(() => {
    if (!consented) return
    dirty.current = true
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => void save(), SAVE_DELAY_MS)
  }, [consented, save])

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current)
  }, [])

  // 화면을 떠나기 전에 남은 답을 보낸다.
  useEffect(() => {
    const flush = () => {
      if (!dirty.current || !consented) return
      const body = JSON.stringify({
        answers: compactAnswers(latest.current.answers),
        current_step: latest.current.stepKey,
        schema_version: schemaVersion,
        ...nextSeq(),
      })
      void fetch(`${base}/answers`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body, keepalive: true })
      dirty.current = false
    }
    const onHide = () => document.visibilityState === 'hidden' && flush()
    document.addEventListener('visibilitychange', onHide)
    window.addEventListener('pagehide', flush)
    return () => {
      document.removeEventListener('visibilitychange', onHide)
      window.removeEventListener('pagehide', flush)
    }
  }, [base, consented, schemaVersion, nextSeq])

  const setTop = useCallback(
    (key: string, value: ChangeValue) => {
      setAnswers((prev) => ({ ...prev, [key]: resolveChange(prev[key], value) }))
      scheduleSave()
    },
    [scheduleSave],
  )

  const setFocus = useCallback(
    (focus: string, key: string, value: ChangeValue) => {
      if (!schema) return
      setAnswers((prev) => {
        const all = { ...((prev[schema.focus_key] as Record<string, Answers> | undefined) ?? {}) }
        all[focus] = { ...(all[focus] ?? {}), [key]: resolveChange((all[focus] ?? {})[key], value) }
        return { ...prev, [schema.focus_key]: all }
      })
      scheduleSave()
    },
    [schema, scheduleSave],
  )

  function go(key: string) {
    setStepKey(key)
    setError(null)
    window.scrollTo(0, 0)
    scheduleSave()
  }

  function step(delta: number) {
    const next = flow[Math.max(0, Math.min(flow.length - 1, index + delta))]
    if (next) go(next.key)
  }

  async function start() {
    if (!schema) return
    if (!consented) {
      if (!consentChecked || !respondent) return
      setBusy(true)
      try {
        const response = await fetch(`${base}/consent`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ consent_version: schema.intro.consent.version, respondent }),
        })
        if (!response.ok) {
          setError(await readDetail(response, '동의를 저장하지 못했습니다. 잠시 뒤 다시 시도해 주세요.'))
          return
        }
        setConsented(true)
      } finally {
        setBusy(false)
      }
    }
    const resume = data?.current_step && data.current_step !== 'intro' ? data.current_step : null
    const target = resume && flow.some((item) => item.key === resume) ? resume : flow[1]?.key
    if (target) go(target)
  }

  async function submit() {
    if (!schema) return
    if (timer.current) clearTimeout(timer.current)
    setBusy(true)
    setError(null)
    if (uploads.current.size) {
      setBusy(false)
      setError('파일을 올리는 중입니다. 다 올라간 뒤 제출해 주세요.')
      return
    }
    dirty.current = false
    // 보내던 저장이 제출 뒤에 도착해 제출한 답을 덮지 않게 먼저 끝낸다.
    if (inFlight.current) await inFlight.current
    try {
      const response = await fetch(`${base}/submit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          answers: compactAnswers(answers),
          current_step: 'review',
          schema_version: schema.version,
          ...nextSeq(),
        }),
      })
      if (!response.ok) {
        setError(await readDetail(response, '제출하지 못했습니다. 잠시 뒤 다시 시도해 주세요.'))
        // 제출이 안 됐으면 답도 저장되지 않았을 수 있다 — 다시 저장 대상으로 되돌린다.
        scheduleSave()
        return
      }
      dirty.current = false
      setData((prev) => (prev ? { ...prev, submission_count: prev.submission_count + 1, has_unsubmitted_changes: false } : prev))
      go('done')
      dirty.current = false
    } catch {
      setError('제출하지 못했습니다. 연결을 확인한 뒤 다시 시도해 주세요.')
      scheduleSave()
    } finally {
      setBusy(false)
    }
  }

  if (state === 'loading') {
    return <div className="dsv"><main><div className="wrap"><p className="desc">불러오는 중입니다…</p></div></main></div>
  }
  if (state === 'gone' || state === 'error' || !schema || !data) {
    return (
      <div className="dsv">
        <main>
          <div className="wrap gone">
            <h1 className="title">{state === 'gone' ? '링크를 열 수 없습니다' : '잠시 연결이 원활하지 않습니다'}</h1>
            <p className="desc">
              {state === 'gone'
                ? '유효하지 않은 링크입니다. 담당자에게 새 링크를 요청해 주세요.'
                : '잠시 뒤 새로고침해 주세요.'}
            </p>
          </div>
        </main>
      </div>
    )
  }

  const ctx: FieldContext = { schema, answers, token, trackUpload }
  const isQuestion = Boolean(current?.question)
  const nextLabel = current?.key === 'intro' ? (consented ? '이어서 작성하기' : '시작하기') : current?.key === 'review' ? '제출하기' : '다음'
  const nextDisabled =
    busy ||
    (current?.key === 'review' && uploading > 0) ||
    (current?.key === 'intro' && !consented && (!consentChecked || !respondent))
  const missing = questions.filter((item) => !questionAnswered(schema, answers, item))
  const savedLabel =
    saveState === 'error' ? '저장 안 됨' : saveState === 'saving' ? '저장 중' : savedAt ? `저장됨 ${clock(savedAt)}` : ''

  return (
    <div className="dsv">
      <header className={`hd${isQuestion ? '' : ' bare'}`}>
        <div className="wrap">
          <button
            className="back"
            aria-label="이전"
            style={{ visibility: index === 0 || current?.key === 'done' ? 'hidden' : 'visible' }}
            onClick={() => step(-1)}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M15 5l-7 7 7 7" /></svg>
          </button>
          <div className="prog">
            <div
              style={{
                width: `${questionIndex >= 0 ? ((questionIndex + 1) / questions.length) * 100 : current?.key === 'review' || current?.key === 'done' ? 100 : 0}%`,
              }}
            />
          </div>
          <span className="cnt">{questionIndex >= 0 ? `${questionIndex + 1} / ${questions.length}` : ''}</span>
          <span className={`saved${savedLabel ? ' on' : ''}${saveState === 'error' ? ' err' : ''}`}>{savedLabel}</span>
          {consented ? (
            <button className="toc-btn" onClick={() => setTocOpen(true)}>목차</button>
          ) : null}
        </div>
      </header>

      <main>
        <div className="wrap">
          {current?.key === 'intro' ? (
            <Intro
              schema={schema}
              hospitalName={data.hospital_name}
              consented={consented}
              consentChecked={consentChecked}
              respondent={respondent}
              onConsent={setConsentChecked}
              onRespondent={setRespondent}
            />
          ) : null}

          {current?.question ? (
            <section className="screen on">
              <div className="eyebrow">
                {(current.partIndex ?? 0) + 1}부 {schema.parts[current.partIndex ?? 0]?.name}
                <span>{current.focus ? ` · ${current.focus}` : ''}</span>
              </div>
              <h1 className="title">{questionTitle(current.question, current.focus)}</h1>
              {questionDesc(current.question, current.focus) ? (
                <p className="desc">{questionDesc(current.question, current.focus)}</p>
              ) : null}
              {current.question.why ? <div className="why">{current.question.why}</div> : null}
              <div className="body">
                {current.focus !== undefined ? (
                  <FieldList
                    fields={current.question.b}
                    scope={focusScope(answers, schema, current.focus)}
                    onChange={(key, value) => setFocus(current.focus!, key, value)}
                    ctx={ctx}
                  />
                ) : (
                  <FieldList fields={current.question.b} scope={answers} onChange={setTop} ctx={ctx} />
                )}
              </div>
            </section>
          ) : null}

          {current?.key === 'review' ? (
            <section className="screen on">
              <div className="eyebrow">마지막 확인</div>
              <h1 className="title">{schema.review.title}</h1>
              <p className="desc">{schema.review.desc}</p>
              {missing.length ? (
                <div className="miss">
                  {missing.map((item) => (
                    <button key={item.key} type="button" onClick={() => go(item.key)}>
                      {itemLabel(item)}
                      <span>채우기 →</span>
                    </button>
                  ))}
                </div>
              ) : (
                <div className="all-ok">비어 있는 문항이 없습니다.</div>
              )}
            </section>
          ) : null}

          {current?.key === 'done' ? (
            <section className="screen on done">
              <div className="ck">✓</div>
              <h1 className="title">{schema.done.title}</h1>
              <p className="desc">
                {schema.done.desc}
                <br />
                {schema.done.edit_note}
              </p>
              <button type="button" className="edit-btn" onClick={() => go(flow[1]?.key ?? 'intro')}>답 고치기</button>
            </section>
          ) : null}

          {error ? <p className="err-msg" role="alert">{error}</p> : null}
        </div>
      </main>

      {current?.key === 'done' ? null : (
        <div className="ft">
          <div className="wrap">
            <div className="btns">
              {index === 0 ? null : (
                <button className="prev" onClick={() => step(-1)}>이전</button>
              )}
              <button
                className="next"
                disabled={nextDisabled}
                onClick={() => {
                  if (current?.key === 'intro') void start()
                  else if (current?.key === 'review') void submit()
                  else step(1)
                }}
              >
                {current?.key === 'review' && uploading > 0
                  ? '파일을 올리는 중…'
                  : busy && current?.key === 'review'
                    ? '제출하는 중…'
                    : nextLabel}
              </button>
            </div>
            {isQuestion ? (
              <button className="skip" onClick={() => step(1)}>건너뛰기</button>
            ) : null}
          </div>
        </div>
      )}

      {tocOpen ? (
        <div className="toc on" onClick={(event) => event.target === event.currentTarget && setTocOpen(false)}>
          <div className="toc-p">
            <h3>
              목차
              <button type="button" aria-label="닫기" onClick={() => setTocOpen(false)}>×</button>
            </h3>
            <div className="toc-g">
              <button type="button" className={current?.key === 'intro' ? 'cur' : ''} onClick={() => { setTocOpen(false); go('intro') }}>
                <i />설문 시작 화면
              </button>
            </div>
            {schema.parts.map((part, partIndex) => {
              const items = flow.filter((item) => item.question && item.partIndex === partIndex)
              return (
                <div className="toc-g" key={part.name}>
                  <b>{partIndex + 1}부 {part.name}</b>
                  {items.length ? (
                    items.map((item) => (
                      <button
                        key={item.key}
                        type="button"
                        className={`${questionAnswered(schema, answers, item) ? 'ok' : ''}${item.key === current?.key ? ' cur' : ''}`}
                        onClick={() => { setTocOpen(false); go(item.key) }}
                      >
                        <i />
                        {itemLabel(item)}
                      </button>
                    ))
                  ) : (
                    <div className="help" style={{ padding: '10px 0' }}>&lsquo;늘리고 싶은 진료&rsquo;를 적으시면 표시됩니다</div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      ) : null}
    </div>
  )
}

/** 목차·확인 화면의 문항 이름. 제목에 진료 이름이 없으면 앞에 붙인다. */
function itemLabel(item: FlowItem): string {
  const title = questionTitle(item.question!, item.focus)
  return item.focus && !title.includes(item.focus) ? `${item.focus} · ${title}` : title
}

function Intro({
  schema,
  hospitalName,
  consented,
  consentChecked,
  respondent,
  onConsent,
  onRespondent,
}: {
  schema: SurveySchema
  hospitalName: string | null
  consented: boolean
  consentChecked: boolean
  respondent: string | null
  onConsent: (value: boolean) => void
  onRespondent: (value: string) => void
}) {
  const intro = schema.intro
  return (
    <section className="screen on hero">
      <div className="eyebrow">Re:putation{hospitalName ? <span> · {hospitalName}</span> : null}</div>
      <h1 className="title">{intro.title}</h1>
      <p className="desc">{intro.desc}</p>
      <div className="badges">
        {intro.badges.map((badge) => (
          <span key={badge}>{badge}</span>
        ))}
      </div>
      <ul className="parts">
        {schema.parts.map((part, index) => (
          <li key={part.name}>
            <b>{index + 1}부</b>
            <div>
              {part.name}
              <span>{part.sum}</span>
            </div>
          </li>
        ))}
      </ul>
      <p className="notice">{intro.notice}</p>
      {consented ? (
        <p className="resume">작성하시던 답이 저장되어 있습니다. 이어서 작성하실 수 있습니다.</p>
      ) : (
        <>
          <div className="respondent fld">
            <span className="lb">{intro.respondent_label}</span>
            <div className="chips">
              {intro.respondents.map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  className={`chip${respondent === value ? ' on' : ''}`}
                  aria-pressed={respondent === value}
                  onClick={() => onRespondent(value)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          <div className="consent">
            <h2>{intro.consent.title}</h2>
            <ul>
              {intro.consent.items.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
            <label>
              <input type="checkbox" checked={consentChecked} onChange={(event) => onConsent(event.target.checked)} />
              {intro.consent.label}
            </label>
          </div>
        </>
      )}
    </section>
  )
}

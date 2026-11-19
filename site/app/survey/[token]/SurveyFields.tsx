'use client'

import { useState, type KeyboardEvent, type ReactNode } from 'react'
import {
  aiQuestionOptions,
  optionValue,
  treatmentNames,
  type Answers,
  type SurveyField,
  type SurveyOption,
  type SurveySchema,
} from '@/lib/doctor-survey'

/**
 * 설문 입력 부품. 모양은 마케팅팀 설문 미리보기(2026-11-19)를 그대로 따른다.
 * 모든 칸은 `scope[key]`를 읽고 `onChange(key, value)`로 바꾼다 — scope는 전체 답, 주력 진료 한 개의 답,
 * 또는 줄 목록의 한 줄이다.
 */

export interface FieldContext {
  schema: SurveySchema
  /** 보기를 끌어오는 데 쓰는 전체 답(진료 이름·동네·진료과). */
  answers: Answers
}

type Change = (key: string, value: unknown) => void

const str = (value: unknown): string => (typeof value === 'string' ? value : '')
const strList = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
const rowList = (value: unknown): Answers[] =>
  Array.isArray(value) ? value.map((row) => (row && typeof row === 'object' ? (row as Answers) : {})) : []

function Fld({ label, children, extra }: { label?: string; children: ReactNode; extra?: ReactNode }) {
  return (
    <div className="fld">
      {label ? <span className="lb">{label}</span> : null}
      {children}
      {extra ?? null}
    </div>
  )
}

// 서버(`doctor_survey_schema`)의 글자 수 한도와 같다 — 한 칸이 넘치면 저장 전체가 거절되므로 입력에서 막는다.
const TEXT_MAX = 500
const AREA_MAX = 3000
const OPTION_MAX = 200

function TextInput({
  field,
  scope,
  onChange,
  type = 'text',
  limit = TEXT_MAX,
}: {
  field: SurveyField
  scope: Answers
  onChange: Change
  type?: string
  limit?: number
}) {
  const value = str(scope[field.key!])
  return (
    <Fld
      label={field.label}
      extra={field.max ? <div className="counter">{value.length} / {field.max}자</div> : null}
    >
      <input
        className="inp"
        type={type}
        inputMode={type === 'tel' ? 'tel' : undefined}
        placeholder={field.ph ?? ''}
        maxLength={field.max ?? limit}
        value={value}
        onChange={(event) => onChange(field.key!, event.target.value)}
      />
    </Fld>
  )
}

function AreaInput({ field, scope, onChange }: { field: SurveyField; scope: Answers; onChange: Change }) {
  return (
    <Fld label={field.label}>
      <textarea
        className="inp"
        maxLength={AREA_MAX}
        placeholder={field.ph ?? ''}
        value={str(scope[field.key!])}
        onChange={(event) => onChange(field.key!, event.target.value)}
      />
    </Fld>
  )
}

function NumberInput({ field, scope, onChange }: { field: SurveyField; scope: Answers; onChange: Change }) {
  return (
    <Fld label={field.label}>
      <div className="unit">
        <input
          className="inp"
          inputMode="numeric"
          placeholder={field.ph ?? ''}
          value={str(scope[field.key!])}
          onChange={(event) => onChange(field.key!, event.target.value.replace(/[^\d]/g, '').slice(0, 12))}
        />
        {field.unit ? <span>{field.unit}</span> : null}
      </div>
    </Fld>
  )
}

const CUSTOM = '__custom'

function SelectInput({ field, scope, onChange, ctx }: { field: SurveyField; scope: Answers; onChange: Change; ctx: FieldContext }) {
  const value = str(scope[field.key!])
  const options = field.src === 'treat' ? treatmentNames(ctx.answers) : (field.opts ?? []).map(optionValue)
  const known = options.includes(value)
  const [customMode, setCustomMode] = useState(Boolean(value) && !known && Boolean(field.custom))
  const selected = customMode ? CUSTOM : known ? value : ''
  return (
    <Fld label={field.label}>
      <div>
        <select
          className={`inp${selected ? '' : ' empty'}`}
          value={selected}
          onChange={(event) => {
            if (event.target.value === CUSTOM) {
              setCustomMode(true)
              onChange(field.key!, '')
            } else {
              setCustomMode(false)
              onChange(field.key!, event.target.value)
            }
          }}
        >
          <option value="">{field.ph ?? '선택'}</option>
          {options.map((option) => (
            <option key={option} value={option}>{option}</option>
          ))}
          {field.custom ? <option value={CUSTOM}>직접 입력</option> : null}
        </select>
        {customMode ? (
          <input
            className="inp"
            style={{ marginTop: 8 }}
            placeholder="직접 입력"
            maxLength={OPTION_MAX}
            value={value}
            onChange={(event) => onChange(field.key!, event.target.value)}
          />
        ) : null}
      </div>
    </Fld>
  )
}

function Chips({
  field,
  scope,
  onChange,
  ctx,
  multi,
}: {
  field: SurveyField
  scope: Answers
  onChange: Change
  ctx: FieldContext
  multi: boolean
}) {
  const key = field.key!
  const selected: string[] = multi ? strList(scope[key]) : str(scope[key]) ? [str(scope[key])] : []
  const baseOptions: SurveyOption[] = field.src === 'aiq' ? aiQuestionOptions(ctx.schema, ctx.answers) : field.opts ?? []
  const baseValues = baseOptions.map(optionValue)
  // 직접 입력해 고른 값은 보기 뒤에 붙인다.
  const options: SurveyOption[] = [...baseOptions, ...selected.filter((item) => !baseValues.includes(item))]
  const [draft, setDraft] = useState('')

  function toggle(value: string) {
    const on = selected.includes(value)
    if (!multi) {
      onChange(key, on ? '' : value)
      return
    }
    let next: string[]
    if (field.none && value === field.none) next = on ? [] : [value]
    else {
      next = selected.filter((item) => item !== field.none)
      next = on ? next.filter((item) => item !== value) : [...next, value]
    }
    if (field.max && next.length > field.max) return
    onChange(key, next)
  }

  function addCustom() {
    const value = draft.trim()
    if (!value) return
    setDraft('')
    if (multi) {
      if (selected.includes(value) || (field.max && selected.length >= field.max)) return
      onChange(key, [...selected.filter((item) => item !== field.none), value])
    } else onChange(key, value)
  }

  return (
    <Fld label={field.label}>
      <div>
        <div className={`chips${field.col ? ' col' : ''}`}>
          {options.map((option) => {
            const value = optionValue(option)
            const on = selected.includes(value)
            const full = multi && Boolean(field.max) && selected.length >= (field.max ?? 0) && !on
            const label = field.src === 'aiq' ? `“${value}”` : value
            const content = Array.isArray(option) ? (
              <>
                {option[0]}
                <small>{option[1]}</small>
              </>
            ) : (
              label
            )
            return (
              <button
                key={value}
                type="button"
                className={`chip${on ? ' on' : ''}${full ? ' dis' : ''}`}
                aria-pressed={on}
                onClick={() => toggle(value)}
              >
                {field.col ? <span>{content}</span> : content}
              </button>
            )
          })}
        </div>
        {field.custom ? (
          <div className="inline" style={{ marginTop: 10 }}>
            <input
              className="inp"
              placeholder={field.custom_ph ?? '목록에 없으면 입력'}
              maxLength={OPTION_MAX}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event: KeyboardEvent<HTMLInputElement>) => {
                if (event.key === 'Enter' && !event.nativeEvent.isComposing) {
                  event.preventDefault()
                  addCustom()
                }
              }}
            />
            <button type="button" className="btn-s light" onClick={addCustom}>추가</button>
          </div>
        ) : null}
        {Object.entries(field.reveal ?? {}).map(([option, children]) =>
          selected.includes(option) ? (
            <div key={option} className="sub">
              <FieldList fields={children} scope={scope} onChange={onChange} ctx={ctx} />
            </div>
          ) : null,
        )}
      </div>
    </Fld>
  )
}

function Tags({ field, scope, onChange }: { field: SurveyField; scope: Answers; onChange: Change }) {
  const key = field.key!
  const values = strList(scope[key])
  const [draft, setDraft] = useState('')
  function add(raw: string) {
    const value = raw.trim().replace(/,$/, '').trim()
    setDraft('')
    if (!value || values.includes(value)) return
    if (field.max && values.length >= field.max) return
    onChange(key, [...values, value])
  }
  return (
    <Fld
      label={field.label}
      extra={<div className="help">입력 후 Enter로 추가{field.max ? ` · 최대 ${field.max}개` : ''}</div>}
    >
      <div className="tags">
        {values.map((value) => (
          <span key={value} className="tag">
            {value}
            <button type="button" aria-label={`${value} 삭제`} onClick={() => onChange(key, values.filter((item) => item !== value))}>
              ×
            </button>
          </span>
        ))}
        <input
          placeholder={values.length ? '추가 입력' : field.ph ?? ''}
          maxLength={OPTION_MAX}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.nativeEvent.isComposing) return
            if (event.key === 'Enter' || event.key === ',') {
              event.preventDefault()
              add(draft)
            }
            if (event.key === 'Backspace' && !draft && values.length) onChange(key, values.slice(0, -1))
          }}
          onBlur={() => draft.trim() && add(draft)}
        />
      </div>
    </Fld>
  )
}

function Switch({ on }: { on: boolean }) {
  return <span className={`sw${on ? ' on' : ''}`} role="switch" aria-checked={on} />
}

function ToggleRow({ label, on, onFlip, className = 'tg' }: { label: ReactNode; on: boolean; onFlip: () => void; className?: string }) {
  return (
    <div
      className={className}
      tabIndex={0}
      onClick={onFlip}
      onKeyDown={(event) => {
        if (event.key === ' ' || event.key === 'Enter') {
          event.preventDefault()
          onFlip()
        }
      }}
    >
      <span>{label}</span>
      <Switch on={on} />
    </div>
  )
}

function Toggle({ field, scope, onChange, ctx }: { field: SurveyField; scope: Answers; onChange: Change; ctx: FieldContext }) {
  const on = scope[field.key!] === true
  return (
    <div className="tgw">
      <ToggleRow label={field.label} on={on} onFlip={() => onChange(field.key!, !on)} />
      {field.children?.length ? (
        <div className={`sub${on ? ' open' : ''}`}>
          <FieldList fields={field.children} scope={scope} onChange={onChange} ctx={ctx} />
        </div>
      ) : null}
      {field.children_off?.length ? (
        <div className={`sub${on ? '' : ' open'}`}>
          <FieldList fields={field.children_off} scope={scope} onChange={onChange} ctx={ctx} />
        </div>
      ) : null}
    </div>
  )
}

function RowList({ field, scope, onChange, ctx }: { field: SurveyField; scope: Answers; onChange: Change; ctx: FieldContext }) {
  const key = field.key!
  const rows = rowList(scope[key])
  const minimum = field.rows ?? 1
  const shown = rows.length >= minimum ? rows : [...rows, ...Array.from({ length: minimum - rows.length }, () => ({}))]
  const update = (index: number, rowKey: string, value: unknown) => {
    const next = shown.map((row, i) => (i === index ? { ...row, [rowKey]: value } : row))
    onChange(key, next)
  }
  const remove = (index: number) => {
    const next = shown.filter((_row, i) => i !== index)
    onChange(key, next)
  }
  return (
    <Fld label={field.label}>
      <div className="lst">
        <div className="lst">
          {shown.map((row, index) => (
            <div className="card" key={index}>
              <div className="card-h">
                <b>{field.row_label ?? '항목'} {index + 1}</b>
                {shown.length > minimum || minimum === 0 ? (
                  <button type="button" className="del" onClick={() => remove(index)}>삭제</button>
                ) : null}
              </div>
              <FieldList fields={field.fields ?? []} scope={row} onChange={(rowKey, value) => update(index, rowKey, value)} ctx={ctx} />
            </div>
          ))}
        </div>
        <button type="button" className="add" onClick={() => onChange(key, [...shown, {}])}>
          + {field.add ?? '추가'}
        </button>
      </div>
    </Fld>
  )
}

function FilesPending({ field, ctx }: { field: SurveyField; ctx: FieldContext }) {
  return (
    <div className="file wide">
      <span className="ic">+</span>
      <span>
        <b>{field.label}</b>
        <br />
        <small>{ctx.schema.files_pending_notice}</small>
      </span>
    </div>
  )
}

/* ── 진료시간 ─────────────────────────────────────────────── */

type Day = Record<string, unknown>
const DAY_KEYS: [string, string][] = [
  ['mon', '월요일'],
  ['tue', '화요일'],
  ['wed', '수요일'],
  ['thu', '목요일'],
  ['fri', '금요일'],
]

function TimeSelect({ value, placeholder, times, onChange }: { value: string; placeholder: string; times: string[]; onChange: (value: string) => void }) {
  return (
    <select className={`inp${value ? '' : ' empty'}`} value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="">{placeholder}</option>
      {times.map((time) => (
        <option key={time} value={time}>{time}</option>
      ))}
    </select>
  )
}

function DayBlock({ label, day, defaultOpen, times, onChange }: { label: string; day: Day; defaultOpen: boolean; times: string[]; onChange: (day: Day) => void }) {
  const open = typeof day.open === 'boolean' ? day.open : defaultOpen
  const set = (name: string, value: unknown) => onChange({ ...day, open, [name]: value })
  const time = (name: string) => str(day[name])
  return (
    <div className={`day${open ? '' : ' closed'}`}>
      <ToggleRow
        className="day-h"
        label={<><b>{label}</b><em>{open ? '진료' : '휴진'}</em></>}
        on={open}
        onFlip={() => set('open', !open)}
      />
      <div className="day-b">
        <div className="mlabel">진료 시간</div>
        <div className="tr">
          <TimeSelect value={time('start')} placeholder="시작" times={times} onChange={(v) => set('start', v)} />
          <i>~</i>
          <TimeSelect value={time('end')} placeholder="끝" times={times} onChange={(v) => set('end', v)} />
        </div>
        <div className="mlabel">접수 마감</div>
        <TimeSelect value={time('last')} placeholder="접수 마감 시간" times={times} onChange={(v) => set('last', v)} />
        <ToggleRow className="mini" label="점심시간 있음" on={day.lunch === true} onFlip={() => set('lunch', day.lunch !== true)} />
        {day.lunch === true ? (
          <div className="dsub">
            <div className="tr">
              <TimeSelect value={time('lunch_start')} placeholder="점심 시작" times={times} onChange={(v) => set('lunch_start', v)} />
              <i>~</i>
              <TimeSelect value={time('lunch_end')} placeholder="점심 끝" times={times} onChange={(v) => set('lunch_end', v)} />
            </div>
          </div>
        ) : null}
        <ToggleRow className="mini" label="야간 진료 있음" on={day.night === true} onFlip={() => set('night', day.night !== true)} />
        {day.night === true ? (
          <div className="dsub">
            <TimeSelect value={time('night_end')} placeholder="야간 진료 끝나는 시간" times={times} onChange={(v) => set('night_end', v)} />
          </div>
        ) : null}
      </div>
    </div>
  )
}

function Hours({ field, scope, onChange, ctx }: { field: SurveyField; scope: Answers; onChange: Change; ctx: FieldContext }) {
  const value = (scope[field.key!] && typeof scope[field.key!] === 'object' ? scope[field.key!] : {}) as Record<string, unknown>
  const same = typeof value.same === 'boolean' ? value.same : true
  const day = (name: string): Day => (value[name] && typeof value[name] === 'object' ? (value[name] as Day) : {})
  const set = (name: string, next: unknown) => onChange(field.key!, { ...value, same, [name]: next })
  const times = ctx.schema.times
  return (
    <div className="hrs">
      <ToggleRow label="월~금 진료시간 같음" on={same} onFlip={() => set('same', !same)} />
      {same ? (
        <DayBlock label="평일 (월~금)" day={day('weekday')} defaultOpen times={times} onChange={(d) => set('weekday', d)} />
      ) : (
        <div className="hrs">
          {DAY_KEYS.map(([key, label]) => (
            <DayBlock key={key} label={label} day={day(key)} defaultOpen times={times} onChange={(d) => set(key, d)} />
          ))}
        </div>
      )}
      <DayBlock label="토요일" day={day('sat')} defaultOpen times={times} onChange={(d) => set('sat', d)} />
      <DayBlock label="일요일" day={day('sun')} defaultOpen={false} times={times} onChange={(d) => set('sun', d)} />
    </div>
  )
}

/* ── 의료진 ───────────────────────────────────────────────── */

function BioRows({ rows, kinds, first, onChange }: { rows: Answers[]; kinds: string[]; first: boolean; onChange: (rows: Answers[]) => void }) {
  const shown = rows.length ? rows : [{}, {}]
  const stars = shown.filter((row) => row.star === true).length
  const update = (index: number, patch: Answers) => onChange(shown.map((row, i) => (i === index ? { ...row, ...patch } : row)))
  return (
    <div>
      <div className="lst">
        {shown.map((row, index) => (
          <div className="brow" key={index}>
            <select className={`inp${str(row.kind) ? '' : ' empty'}`} value={str(row.kind)} onChange={(event) => update(index, { kind: event.target.value })}>
              <option value="">구분 선택</option>
              {kinds.map((kind) => (
                <option key={kind} value={kind}>{kind}</option>
              ))}
            </select>
            <input
              className="inp"
              placeholder={index === 0 && first ? '예) ○○대학교 의과대학 졸업' : '예) ○○대학교병원 임상강사'}
              maxLength={300}
              value={str(row.text)}
              onChange={(event) => update(index, { text: event.target.value })}
            />
            <button
              type="button"
              className={`star${row.star === true ? ' on' : ''}`}
              onClick={() => {
                if (row.star !== true && stars >= 3) return
                update(index, { star: row.star !== true })
              }}
            >
              {row.star === true ? '★ 대표' : '☆ 대표'}
            </button>
            <button type="button" className="x" aria-label="삭제" onClick={() => onChange(shown.filter((_r, i) => i !== index))}>×</button>
          </div>
        ))}
        <button type="button" className="add" onClick={() => onChange([...shown, {}])}>+ 약력 추가</button>
      </div>
      <div className="chip-note">대표 경력에 ☆를 눌러 주시면 됩니다. 최대 3개 ({stars}/3)</div>
    </div>
  )
}

function DoctorCard({
  index,
  doctor,
  ctx,
  onChange,
  onRemove,
}: {
  index: number
  doctor: Answers
  ctx: FieldContext
  onChange: (doctor: Answers) => void
  onRemove: () => void
}) {
  const first = index === 0
  const [expanded, setExpanded] = useState(first)
  const set = (key: string, value: unknown) => onChange({ ...doctor, [key]: value })
  const titles = ctx.schema.doctor_titles
  const title = str(doctor.title)
  const customTitle = Boolean(title) && !titles.includes(title)
  const [titleCustom, setTitleCustom] = useState(customTitle)
  const specialtyField: SurveyField = { t: 'select', key: 'specialty', label: '전문의 자격', custom: true, opts: [] }
  return (
    <div className="card">
      <div className="card-h">
        <b>의료진 {index + 1}</b>
        {first ? null : <button type="button" className="del" onClick={onRemove}>삭제</button>}
      </div>
      <TextInput field={{ t: 'text', key: 'name', label: '이름', ph: '홍길동' }} scope={doctor} onChange={set} limit={100} />
      <Fld label="직함">
        <div>
          <div className="chips">
            {[...titles, '직접 입력'].map((option) => {
              const on = option === '직접 입력' ? titleCustom : !titleCustom && title === option
              return (
                <button
                  key={option}
                  type="button"
                  className={`chip${on ? ' on' : ''}`}
                  onClick={() => {
                    if (option === '직접 입력') {
                      setTitleCustom(!titleCustom)
                      set('title', '')
                    } else {
                      setTitleCustom(false)
                      set('title', on ? '' : option)
                    }
                  }}
                >
                  {option}
                </button>
              )
            })}
          </div>
          {titleCustom ? (
            <div className="sub">
              <input className="inp" placeholder="직함" maxLength={50} value={title} onChange={(event) => set('title', event.target.value)} />
            </div>
          ) : null}
        </div>
      </Fld>
      {first ? null : (
        <button type="button" className="more" onClick={() => setExpanded(!expanded)}>
          {expanded ? '자격·약력 접기 ▴' : '자격·약력 적기 ▾'}
        </button>
      )}
      {expanded ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div className="sh">자격</div>
          <TextInput field={{ t: 'text', key: 'school', label: '졸업한 의과대학', ph: '예) ○○대학교 의과대학' }} scope={doctor} onChange={set} limit={200} />
          <SpecialtySelect field={specialtyField} doctor={doctor} ctx={ctx} onChange={set} />
          <Tags field={{ t: 'tags', key: 'societies', label: '정회원 학회', ph: '예) 대한정형외과학회' }} scope={doctor} onChange={set} />
          <div className="sh">약력</div>
          <BioRows rows={rowList(doctor.bio)} kinds={ctx.schema.bio_kinds} first={first} onChange={(rows) => set('bio', rows)} />
        </div>
      ) : null}
    </div>
  )
}

function SpecialtySelect({ field, doctor, ctx, onChange }: { field: SurveyField; doctor: Answers; ctx: FieldContext; onChange: Change }) {
  const value = str(doctor.specialty)
  const all = ctx.schema.spec_groups.flatMap(([, items]) => items)
  const [customMode, setCustomMode] = useState(Boolean(value) && !all.includes(value))
  return (
    <Fld label={field.label}>
      <div>
        <select
          className={`inp${value || customMode ? '' : ' empty'}`}
          value={customMode ? CUSTOM : value}
          onChange={(event) => {
            if (event.target.value === CUSTOM) {
              setCustomMode(true)
              onChange('specialty', '')
            } else {
              setCustomMode(false)
              onChange('specialty', event.target.value)
            }
          }}
        >
          <option value="">선택</option>
          {ctx.schema.spec_groups.map(([group, items]) => (
            <optgroup key={group} label={group}>
              {items.map((item) => (
                <option key={item} value={item}>{item}</option>
              ))}
            </optgroup>
          ))}
          <option value={CUSTOM}>직접 입력</option>
        </select>
        {customMode ? (
          <input className="inp" style={{ marginTop: 8 }} placeholder="직접 입력" maxLength={100} value={value} onChange={(event) => onChange('specialty', event.target.value)} />
        ) : null}
      </div>
    </Fld>
  )
}

function Doctors({ field, scope, onChange, ctx }: { field: SurveyField; scope: Answers; onChange: Change; ctx: FieldContext }) {
  const doctors = rowList(scope[field.key!])
  // 첫 카드는 대표원장이 미리 골라져 있다(미리보기와 같다).
  const shown = doctors.length ? doctors : [{ title: ctx.schema.doctor_titles[0] }]
  const update = (index: number, doctor: Answers) => onChange(field.key!, shown.map((row, i) => (i === index ? doctor : row)))
  return (
    <div className="lst">
      <div className="lst">
        {shown.map((doctor, index) => (
          <DoctorCard
            key={index}
            index={index}
            doctor={doctor}
            ctx={ctx}
            onChange={(next) => update(index, next)}
            onRemove={() => onChange(field.key!, shown.filter((_row, i) => i !== index))}
          />
        ))}
      </div>
      <button type="button" className="add" onClick={() => onChange(field.key!, [...shown, {}])}>+ 의료진 추가</button>
    </div>
  )
}

/* ── 묶음 ─────────────────────────────────────────────────── */

export function FieldView({ field, scope, onChange, ctx }: { field: SurveyField; scope: Answers; onChange: Change; ctx: FieldContext }) {
  switch (field.t) {
    case 'text':
    case 'address':
      return <TextInput field={field} scope={scope} onChange={onChange} />
    case 'tel':
      return <TextInput field={field} scope={scope} onChange={onChange} type="tel" />
    case 'area':
      return <AreaInput field={field} scope={scope} onChange={onChange} />
    case 'number':
      return <NumberInput field={field} scope={scope} onChange={onChange} />
    case 'select':
      return <SelectInput field={field} scope={scope} onChange={onChange} ctx={ctx} />
    case 'single':
      return <Chips field={field} scope={scope} onChange={onChange} ctx={ctx} multi={false} />
    case 'multi':
      return <Chips field={field} scope={scope} onChange={onChange} ctx={ctx} multi />
    case 'tags':
      return <Tags field={field} scope={scope} onChange={onChange} />
    case 'toggle':
      return <Toggle field={field} scope={scope} onChange={onChange} ctx={ctx} />
    case 'toggles':
      return (
        <div className="tg-list">
          {(field.items ?? []).map((item) => (
            <Toggle key={item.key} field={item} scope={scope} onChange={onChange} ctx={ctx} />
          ))}
        </div>
      )
    case 'list':
      return <RowList field={field} scope={scope} onChange={onChange} ctx={ctx} />
    case 'hours':
      return <Hours field={field} scope={scope} onChange={onChange} ctx={ctx} />
    case 'doctors':
      return <Doctors field={field} scope={scope} onChange={onChange} ctx={ctx} />
    case 'files':
      return <FilesPending field={field} ctx={ctx} />
    case 'info':
      return <div className="info">{field.text}</div>
    case 'sh':
      return <div className="sh">{field.text}</div>
    case 'sec':
      return (
        <div className="sec">
          <h2>{field.title}</h2>
          {field.d ? <p className="desc">{field.d}</p> : null}
        </div>
      )
    default:
      return null
  }
}

export function FieldList({ fields, scope, onChange, ctx }: { fields: SurveyField[]; scope: Answers; onChange: Change; ctx: FieldContext }) {
  return (
    <>
      {fields.map((field, index) => (
        <FieldView key={field.key ?? `${field.t}-${index}`} field={field} scope={scope} onChange={onChange} ctx={ctx} />
      ))}
    </>
  )
}

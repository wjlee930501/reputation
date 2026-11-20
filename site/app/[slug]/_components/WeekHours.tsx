'use client'

import { useEffect, useState } from 'react'
import { KeepTogether } from './KeepTogether'

import { HOLIDAY_LABEL, holidayHours } from '@/lib/business-hours'
import { seoulDateLabel, seoulToday } from '@/lib/clinic-layout'

import { CalendarIcon } from './icons'

const DAY_LABELS: Record<string, string> = {
  mon: '월',
  tue: '화',
  wed: '수',
  thu: '목',
  fri: '금',
  sat: '토',
  sun: '일',
}

const DAY_ORDER = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']

export interface WeekToday {
  key: string
  month: number
  day: number
}

function isClosed(time: string): boolean {
  return /휴진|휴무|closed/i.test(time)
}

/**
 * 주간 진료시간 표.
 *
 * 병원 페이지는 서버에서 그린 뒤 캐시된다(s-maxage 1800 + 긴 stale-while-revalidate). 오래 찾지
 * 않은 페이지는 첫 방문자가 며칠 전에 그린 HTML을 받을 수 있어, 서버가 정한 '오늘'은 날짜가
 * 틀린다. 서버 값으로 먼저 그리고(하이드레이션 일치), 브라우저에서 한국 시간 기준 오늘을 다시
 * 계산해 날짜와 오늘 칸 강조를 바로잡는다.
 */
export function WeekHours({
  businessHours,
  renderedToday,
}: {
  businessHours: Record<string, string> | null | undefined
  renderedToday: WeekToday
}) {
  const [today, setToday] = useState<WeekToday>(renderedToday)

  useEffect(() => {
    const now = seoulToday()
    setToday((current) =>
      current.key === now.key && current.month === now.month && current.day === now.day
        ? current
        : { key: now.key, month: now.month, day: now.day },
    )
  }, [])

  const week = DAY_ORDER.map((key) => ({
    key,
    label: DAY_LABELS[key] ?? key,
    time: businessHours?.[key] ?? null,
    isToday: key === today.key,
  }))
  // 공휴일은 요일 칸이 아니라 표 아래 한 줄이다 — 오늘이 공휴일인지는 달력이 없어 모른다.
  const holiday = holidayHours(businessHours)

  return (
    <>
      <div className="clinic-week-head">
        <CalendarIcon className="clinic-icon clinic-icon--sm" aria-hidden="true" />
        <span>주간 진료시간</span>
        <span className="clinic-week-today-hint">{seoulDateLabel(today)}</span>
      </div>
      <ol className="clinic-week-grid">
        {week.map((day) => {
          const closed = day.time ? isClosed(day.time) : false
          return (
            <li
              key={day.key}
              className={`clinic-week-day${day.isToday ? ' is-today' : ''}${closed ? ' is-closed' : ''}`}
              aria-current={day.isToday ? 'date' : undefined}
            >
              <span className="clinic-week-day-label">{day.label}</span>
              <span className="clinic-week-day-time">
                {day.time ? (closed ? '휴진' : <KeepTogether text={day.time} />) : '-'}
              </span>
            </li>
          )
        })}
      </ol>
      {holiday ? (
        <p className={`clinic-week-holiday${holiday.closed ? ' is-closed' : ''}`}>
          <span className="clinic-week-day-label">{HOLIDAY_LABEL}</span>
          <span className="clinic-week-day-time">{holiday.closed ? '휴진' : <KeepTogether text={holiday.value} />}</span>
        </p>
      ) : null}
    </>
  )
}

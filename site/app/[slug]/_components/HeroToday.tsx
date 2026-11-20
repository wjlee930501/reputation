'use client'

import { useEffect, useState } from 'react'
import { KeepTogether } from './KeepTogether'

import { nextOpenDay, weekendHoursCard, weeklyRestDayLabel } from '@/lib/business-hours'
import { seoulToday, todayHours } from '@/lib/clinic-layout'

type Hours = Record<string, string> | null | undefined

/**
 * 한국 시간 기준 오늘 요일 키. 서버가 그린 값으로 시작하고(하이드레이션 일치) 브라우저에서 다시 잰다.
 *
 * 병원 페이지 HTML은 오래 캐시된다(s-maxage 1800 + 긴 stale-while-revalidate). 며칠 전에 그린
 * HTML이 나가면 첫 화면의 '오늘 진료'·'오늘 휴진'·'다음 진료'가 지난 요일의 값이 된다.
 */
function useSeoulTodayKey(renderedKey: string): string {
  const [key, setKey] = useState(renderedKey)
  useEffect(() => {
    const now = seoulToday().key
    setKey((current) => (current === now ? current : now))
  }, [])
  return key
}

/** 첫 화면 '오늘 진료' 칸의 값. */
export function HeroTodayHours({ businessHours, renderedTodayKey }: { businessHours: Hours; renderedTodayKey: string }) {
  const key = useSeoulTodayKey(renderedTodayKey)
  const today = todayHours(businessHours, key)
  return today ? (today.closed ? <>오늘 휴진</> : <KeepTogether text={today.time} />) : <>방문 전 전화 확인</>
}

/** 전화 버튼 글자 — 오늘 휴진이면 사실을 먼저 말한다(S-8). */
export function HeroCallLabel({ businessHours, renderedTodayKey }: { businessHours: Hours; renderedTodayKey: string }) {
  const key = useSeoulTodayKey(renderedTodayKey)
  return <>{todayHours(businessHours, key)?.closed ? '오늘 휴진 · 전화 문의' : '전화 상담'}</>
}

/**
 * 첫 화면 넷째 칸. 7일 같은 시간이면 '휴무일 · 연중무휴'(공휴일 휴진이면 '휴무일 · 공휴일'),
 * 오늘 휴진이면 다음 진료, 아니면 주말 진료.
 * `dt`·`dd` 둘을 함께 그린다.
 */
export function HeroScheduleCell({ businessHours, renderedTodayKey }: { businessHours: Hours; renderedTodayKey: string }) {
  const key = useSeoulTodayKey(renderedTodayKey)
  const restDay = weeklyRestDayLabel(businessHours)
  const upcoming = todayHours(businessHours, key)?.closed ? nextOpenDay(businessHours, key) : null
  const weekend = weekendHoursCard(businessHours)
  return (
    <>
      <dt>{restDay ? '휴무일' : upcoming ? '다음 진료' : weekend.label}</dt>
      <dd>
        {restDay
          ? restDay
          : upcoming
            ? <>{upcoming.label} <KeepTogether text={upcoming.time} /></>
            : weekend.sunday
              ? <>토 <KeepTogether text={weekend.value ?? ''} /><br />일 <KeepTogether text={weekend.sunday} /></>
              : weekend.value
                ? <KeepTogether text={weekend.value} />
                : '방문 전 전화 확인'}
      </dd>
    </>
  )
}

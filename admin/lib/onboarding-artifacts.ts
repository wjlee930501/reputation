/**
 * 온보딩 단계 아코디언이 보여줄 결과물.
 *
 * 초기 진단·콘텐츠 허브·도메인·스케줄 네 단계는 아코디언을 펼쳐도 "필수 정보가 실제로
 * 저장되어 있습니다" 한 줄과 다른 화면으로 가는 링크뿐이었다. 그래서 운영자는 원장에게
 * 보고할 PDF가 실제로 만들어졌는지, 어떤 주소로 공개되는지, 어떤 요일에 몇 편이
 * 나가는지를 확인하려면 매번 다른 탭을 열어 눈으로 맞춰야 했다.
 *
 * 판정에 쓰는 사실을 그 판정을 보여주는 자리에서 함께 읽히게 한다. 없는 것은 만들지
 * 않는다 — 값이 없으면 무엇이 비었는지 말한다.
 */

export interface OnboardingArtifact {
  label: string
  value: string
  href?: string
  /** 값이 없어서 확인이 필요한 항목 */
  missing?: boolean
  state?: 'loading' | 'error' | 'empty'
}

export type OnboardingArtifactLoadState<T> =
  | { status: 'loading' }
  | { status: 'error' }
  | { status: 'loaded'; data: T }

export interface ReportArtifactLike {
  id: string
  report_type?: string | null
  period_year?: number | null
  period_month?: number | null
  has_pdf?: boolean | null
  download_url?: string | null
  created_at?: string | null
}

export interface DomainArtifactHospital {
  aeo_domain?: string | null
  slug?: string | null
  site_built?: boolean | null
  site_live?: boolean | null
  domain_last_checked_at?: string | null
  domain_last_check_ok?: boolean | null
  domain_last_check_reason?: string | null
  domain_cert_job_state?: string | null
}

export interface ScheduleArtifactLike {
  plan?: string | null
  publish_days?: number[] | null
  active_from?: string | null
}

export function resolveReportsArtifactResult(
  result: PromiseSettledResult<ReportArtifactLike[]>,
): OnboardingArtifactLoadState<ReportArtifactLike[]> {
  if (result.status === 'rejected') return { status: 'error' }
  return { status: 'loaded', data: Array.isArray(result.value) ? result.value : [] }
}

export function resolveScheduleArtifactResult(
  result: PromiseSettledResult<ScheduleArtifactLike>,
): OnboardingArtifactLoadState<ScheduleArtifactLike | null> {
  if (result.status === 'fulfilled') return { status: 'loaded', data: result.value ?? null }
  if (
    typeof result.reason === 'object'
    && result.reason !== null
    && 'status' in result.reason
    && result.reason.status === 404
  ) {
    return { status: 'loaded', data: null }
  }
  return { status: 'error' }
}

const PLAN_LABELS: Record<string, string> = {
  PLAN_20: '리더 20편/월',
  PLAN_16: '그로워 16편/월',
  PLAN_12: '스타터 12편/월',
}

const WEEKDAY_LABELS = ['월', '화', '수', '목', '금', '토', '일']

function formatDate(value: string | null | undefined): string | null {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  return date.toLocaleDateString('ko-KR', { year: 'numeric', month: '2-digit', day: '2-digit' })
}

/**
 * 초기 진단(V0) 리포트 결과물.
 *
 * 월간 리포트는 초기 진단을 대신하지 못하므로 `report_type`으로 좁힌다 — 온보딩 3단계
 * 완료 판정(`v0_report_pdf_count`)이 쓰는 것과 같은 기준이다.
 */
export function selectV0ReportArtifacts(reports: ReportArtifactLike[]): OnboardingArtifact[] {
  const rows = Array.isArray(reports) ? reports : []
  const v0 = rows.filter((report) => report.report_type === 'V0')
  if (v0.length === 0) {
    return [{
      label: '초기 진단 리포트',
      value: '아직 생성되지 않았습니다',
      missing: true,
      state: 'empty',
    }]
  }

  const withPdf = v0.find((report) => report.has_pdf && report.download_url)
  if (!withPdf) {
    return [
      {
        label: '초기 진단 리포트',
        value: `기록 ${v0.length}건 · PDF 없음 — 다시 만들어야 원장 보고 자료가 생깁니다`,
        missing: true,
        state: 'empty',
      },
    ]
  }

  const created = formatDate(withPdf.created_at)
  return [
    {
      label: '초기 진단 리포트 PDF',
      value: created ? `${created} 생성` : '생성 완료',
      href: withPdf.download_url ?? undefined,
    },
  ]
}

export function describeV0ReportArtifactState(
  state: OnboardingArtifactLoadState<ReportArtifactLike[]>,
): OnboardingArtifact[] {
  if (state.status === 'loading') {
    return [{
      label: '초기 진단 리포트',
      value: '리포트 정보를 불러오는 중입니다',
      state: 'loading',
    }]
  }
  if (state.status === 'error') {
    return [{
      label: '초기 진단 리포트',
      value: '리포트 정보를 불러오지 못했습니다. 위의 새로 고침을 눌러 다시 시도해 주세요',
      state: 'error',
    }]
  }
  return selectV0ReportArtifacts(state.data)
}

/** 콘텐츠 허브·도메인 단계가 확인해야 하는 공개 주소 사실. */
export function describeDomainArtifacts(
  hospital: DomainArtifactHospital | null,
): OnboardingArtifact[] {
  if (!hospital) {
    return [{ label: '공개 주소', value: '병원 정보를 불러오는 중입니다', missing: true }]
  }

  const artifacts: OnboardingArtifact[] = []
  const platformHost = hospital.slug ? `${hospital.slug}` : null
  artifacts.push(
    hospital.aeo_domain
      ? { label: '자기 도메인', value: hospital.aeo_domain, href: `https://${hospital.aeo_domain}` }
      : {
          label: '자기 도메인',
          value: platformHost
            ? '연결 없음 — 기본 플랫폼 주소로 운영합니다'
            : '연결 없음',
        },
  )

  const checkedAt = formatDate(hospital.domain_last_checked_at)
  if (hospital.domain_last_check_ok === true) {
    artifacts.push({
      label: '연결 확인',
      value: checkedAt ? `정상 · ${checkedAt} 확인` : '정상',
    })
  } else if (hospital.domain_last_check_ok === false) {
    artifacts.push({
      label: '연결 확인',
      value: `${hospital.domain_last_check_reason ?? '확인 실패'}${checkedAt ? ` · ${checkedAt} 확인` : ''}`,
      missing: true,
    })
  } else if (hospital.aeo_domain) {
    artifacts.push({ label: '연결 확인', value: '아직 확인하지 않았습니다', missing: true })
  }

  artifacts.push({
    label: '공개 운영',
    value: hospital.site_live ? '운영 중' : '아직 시작하지 않았습니다',
    missing: !hospital.site_live,
  })

  return artifacts
}

/** 스케줄 단계가 확인해야 하는 요금제·발행 요일. */
export function describeScheduleArtifacts(
  schedule: ScheduleArtifactLike | null,
): OnboardingArtifact[] {
  if (!schedule) {
    return [{
      label: '발행 스케줄',
      value: '아직 저장되지 않았습니다',
      missing: true,
      state: 'empty',
    }]
  }

  const artifacts: OnboardingArtifact[] = []
  const plan = schedule.plan ? PLAN_LABELS[schedule.plan] ?? schedule.plan : null
  artifacts.push(
    plan
      ? { label: '요금제', value: plan }
      : { label: '요금제', value: '확인 필요', missing: true },
  )

  const days = Array.isArray(schedule.publish_days) ? schedule.publish_days : []
  artifacts.push(
    days.length > 0
      ? {
          label: '발행 요일',
          value: days
            .filter((day) => day >= 0 && day < WEEKDAY_LABELS.length)
            .map((day) => WEEKDAY_LABELS[day])
            .join('·'),
        }
      : { label: '발행 요일', value: '확인 필요', missing: true },
  )

  const from = formatDate(schedule.active_from)
  if (from) artifacts.push({ label: '적용 시작', value: from })

  return artifacts
}

export function describeScheduleArtifactState(
  state: OnboardingArtifactLoadState<ScheduleArtifactLike | null>,
): OnboardingArtifact[] {
  if (state.status === 'loading') {
    return [{ label: '발행 스케줄', value: '스케줄 정보를 불러오는 중입니다', state: 'loading' }]
  }
  if (state.status === 'error') {
    return [{
      label: '발행 스케줄',
      value: '스케줄 정보를 불러오지 못했습니다. 위의 새로 고침을 눌러 다시 시도해 주세요',
      state: 'error',
    }]
  }
  return describeScheduleArtifacts(state.data)
}

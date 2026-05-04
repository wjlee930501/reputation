'use client'

import Link from 'next/link'
import { useParams, usePathname } from 'next/navigation'
import { useEffect, useState } from 'react'
import { fetchAPI } from '@/lib/api'
import { Hospital, PLAN_LABELS, STATUS_LABELS } from '@/types'

const TABS: Array<{ label: string; path: string; hint: string }> = [
  { label: '대시보드', path: 'dashboard', hint: 'AI 언급률과 운영 준비 상태 한눈에 보기' },
  { label: '프로파일', path: 'profile', hint: '병원 기본 정보' },
  { label: '운영 기준', path: 'essence', hint: '콘텐츠 운영 기준(진료 철학·말투·금기 표현) 승인' },
  { label: '환자 질문', path: 'query-targets', hint: 'ChatGPT·Gemini 같은 AI 답변 서비스에 노출시킬 환자 질문 정의' },
  { label: '노출 보완 작업', path: 'exposure-actions', hint: 'AI에 더 잘 노출되도록 보완할 작업과 콘텐츠 가이드 연결' },
  { label: '콘텐츠', path: 'content', hint: '초안 검수·발행' },
  { label: '스케줄', path: 'schedule', hint: '발행 캘린더' },
  { label: '리포트', path: 'reports', hint: '월간 리포트' },
]

export default function HospitalLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const pathname = usePathname()
  const params = useParams<{ id: string }>()
  const hospitalId = params.id
  const [hospital, setHospital] = useState<Hospital | null>(null)

  useEffect(() => {
    fetchAPI(`/admin/hospitals/${hospitalId}`)
      .then(setHospital)
      .catch(() => null)
  }, [hospitalId])

  const statusInfo = hospital
    ? STATUS_LABELS[hospital.status] ?? { label: hospital.status, color: 'bg-gray-100 text-gray-700' }
    : null

  const planLabel = hospital?.plan ? PLAN_LABELS[hospital.plan] ?? hospital.plan : null

  return (
    <div className="flex flex-col h-full">
      {/* Hospital header */}
      <header className="bg-white border-b border-slate-200 px-8 pt-5 pb-0">
        <div className="flex items-start justify-between gap-6 mb-4">
          <div className="min-w-0">
            <Link
              href="/hospitals"
              className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-700 transition-colors"
            >
              ← 병원 목록
            </Link>
            <div className="flex items-center gap-3 mt-2 flex-wrap">
              <h1 className="text-xl font-bold text-slate-900 truncate">
                {hospital?.name ?? '불러오는 중...'}
              </h1>
              {statusInfo && (
                <span className={`inline-flex px-2.5 py-0.5 rounded-full text-xs font-medium ${statusInfo.color}`}>
                  {statusInfo.label}
                </span>
              )}
              {planLabel && (
                <span className="inline-flex px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-700">
                  {planLabel}
                </span>
              )}
            </div>
            {hospital && (
              <div className="mt-1.5 flex items-center gap-3 text-xs text-slate-500">
                <span className="font-mono text-slate-400">{hospital.slug}</span>
                {hospital.aeo_domain && (
                  <>
                    <span aria-hidden className="text-slate-300">·</span>
                    <span>
                      병원 정보 허브 도메인 <span className="text-slate-600">{hospital.aeo_domain}</span>
                    </span>
                  </>
                )}
                {hospital.site_live && (
                  <span className="inline-flex items-center gap-1 text-emerald-600 font-medium">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                    병원 정보 허브 운영중
                  </span>
                )}
              </div>
            )}
          </div>

          {hospital && (
            <div className="hidden md:flex items-center gap-4 text-[11px] text-slate-500 shrink-0">
              <ProgressDot label="프로파일 완료" done={hospital.profile_complete} />
              <ProgressDot label="초기 진단 리포트 완료" done={hospital.v0_report_done} />
              <ProgressDot label="병원 정보 허브 운영중" done={hospital.site_live} />
              <ProgressDot label="스케줄 설정" done={hospital.schedule_set} />
            </div>
          )}
        </div>

        {/* Tab navigation */}
        <nav className="flex items-end gap-1 -mb-px overflow-x-auto" aria-label="병원 작업 탭">
          {TABS.map((tab) => {
            const href = `/hospitals/${hospitalId}/${tab.path}`
            const isActive = pathname.startsWith(href)
            return (
              <Link
                key={tab.path}
                href={href}
                className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
                  isActive
                    ? 'border-blue-600 text-blue-700'
                    : 'border-transparent text-slate-500 hover:text-slate-800 hover:border-slate-200'
                }`}
                title={tab.hint}
              >
                {tab.label}
              </Link>
            )
          })}
        </nav>
      </header>

      {/* Page content */}
      <div className="flex-1 overflow-auto">
        {children}
      </div>
    </div>
  )
}

function ProgressDot({ label, done }: { label: string; done: boolean | undefined }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className={`w-2 h-2 rounded-full ${done ? 'bg-emerald-500' : 'bg-slate-300'}`}
        aria-hidden
      />
      <span className={done ? 'text-slate-700' : 'text-slate-400'}>{label}</span>
    </span>
  )
}

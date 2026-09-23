import { readFile } from 'node:fs/promises'
import path from 'node:path'

import { ImageResponse } from 'next/og'

import { HospitalNotFoundError, fetchHospital } from '@/lib/api'
import { clinicFaviconSpec, type ClinicFaviconSpec } from '@/lib/clinic-favicon'

// 병원 모노그램 탭 아이콘. 링크는 app/[slug]/layout.tsx가 버전 쿼리와 함께 건다.
// 폰트는 standalone 번들에 실린다(next.config.mjs outputFileTracingIncludes).
export const runtime = 'nodejs'

const FONT_PATH = path.join(process.cwd(), 'assets', 'fonts', 'Pretendard-Bold.subset.otf')
const ICON_SIZE = 96
const APPLE_SIZE = 180

// 병원을 확인하지 못하면 Re:putation 심볼로 떨어지지 않게 글자 없는 중립 아이콘을 준다.
const NEUTRAL_SPEC: ClinicFaviconSpec = { letter: '', background: '#6B7280', foreground: '#FFFFFF' }

// 버전 쿼리가 모양을 대표하므로 길게 캐시한다. 중립 아이콘은 복구 뒤 곧 바뀌도록 짧게 둔다.
const CACHE_VERSIONED = 'public, max-age=86400, s-maxage=86400, stale-while-revalidate=604800'
const CACHE_NEUTRAL = 'public, max-age=300, s-maxage=300'

let fontData: Promise<Buffer> | null = null
function loadFont(): Promise<Buffer> {
  fontData ??= readFile(FONT_PATH).catch((error: unknown) => {
    fontData = null
    throw error
  })
  return fontData
}

function renderIcon(spec: ClinicFaviconSpec, apple: boolean, font: Buffer, cacheControl: string) {
  const size = apple ? APPLE_SIZE : ICON_SIZE
  // iOS는 홈 화면 아이콘을 스스로 둥글린다 — 투명 모서리는 검게 채워지므로 꽉 채운다.
  const radius = apple ? 0 : Math.round(size * 0.22)
  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: spec.background,
          borderRadius: radius,
          color: spec.foreground,
          fontFamily: 'Pretendard',
          fontWeight: 700,
          fontSize: Math.round(size * (apple ? 0.56 : 0.62)),
          lineHeight: 1,
        }}
      >
        {spec.letter}
      </div>
    ),
    {
      width: size,
      height: size,
      fonts: [{ name: 'Pretendard', data: font, weight: 700, style: 'normal' }],
      headers: { 'cache-control': cacheControl },
    },
  )
}

export async function GET(
  request: Request,
  { params }: { params: Promise<{ slug: string }> },
) {
  const { slug } = await params
  const apple = new URL(request.url).searchParams.get('variant') === 'apple'
  const font = await loadFont()
  try {
    const hospital = await fetchHospital(slug)
    return renderIcon(clinicFaviconSpec(hospital), apple, font, CACHE_VERSIONED)
  } catch (error) {
    if (!(error instanceof HospitalNotFoundError)) {
      console.error('clinic favicon: hospital lookup failed', { slug, error })
    }
    return renderIcon(NEUTRAL_SPEC, apple, font, CACHE_NEUTRAL)
  }
}

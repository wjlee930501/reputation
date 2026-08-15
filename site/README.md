# Re:putation Site

병원별 AEO(Answer Engine Optimization) 최적화 공개 홈페이지. Next.js SSG 기반.

## 구조

```
app/[slug]/            — 병원별 진료 정보 허브
app/[slug]/contents/   — 콘텐츠 라이브러리
app/[slug]/treatments/ — 진료 영역
app/[slug]/doctor/     — 의료진 소개
app/[slug]/visit/      — 진료 안내
```

## 로컬 개발

```bash
cd site
cp ../.env.example .env.local
npm install
npm run dev
```

프로덕션 빌드에는 다음 공개 환경변수가 필요합니다.

- `NEXT_PUBLIC_SITE_URL`: 공개 사이트 origin (예: `https://reputation.co.kr`)
- `NEXT_PUBLIC_API_URL`: Public API base (예: `https://reputation.co.kr/api/v1/public`)
- `NEXT_PUBLIC_BACKEND_URL`: 이미지·파일 URL을 제공하는 backend origin

`NEXT_PUBLIC_SITE_URL`이 없거나 localhost를 가리키면 프로덕션 빌드는 안전하게 실패합니다.

## 배포

Vercel을 통해 배포. `next build && next start` 또는 Vercel Git integration.

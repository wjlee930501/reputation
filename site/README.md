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

## 배포

Vercel을 통해 배포. `next build && next start` 또는 Vercel Git integration.

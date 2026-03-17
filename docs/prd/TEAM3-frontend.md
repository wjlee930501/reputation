# PRD: TEAM 3 — Frontend 개선 (PO 검토 반영 v0.3)

> 버전: v0.2 → v0.3 | 작성일: 2026-03-17 | PO 검토: 완료

---

## 목표

AE 운영 효율 2배 향상 + AEO 사이트 크롤링 최적화

---

## Backend API 의존성 (TEAM 1 구현)

Frontend 작업 전 필요한 신규 Backend API:
- `PATCH /admin/hospitals/{id}/content/{cid}` — 콘텐츠 수정 (금지표현 검사 포함)
- `GET /admin/hospitals/{id}/sov/trend` — SoV 주간 추이
- `GET /admin/hospitals/{id}/sov/queries` — 쿼리별 멘션율
- `POST /admin/hospitals/{id}/domain/verify` — DNS CNAME 검증

→ TEAM 1이 API를 먼저 구현. Frontend는 API 스텁/목업으로 병렬 개발 가능.

---

## 1. 콘텐츠 인라인 편집 (P1)

**변경 사항**:

a) **콘텐츠 상세 모달** (`[id]/content/page.tsx`):
- "편집" 모드 토글 추가
- 제목: text input, 본문: textarea, meta_description: text input
- 클라이언트 사이드 금지표현 사전 검사 (`FORBIDDEN_EXPRESSIONS` 프론트 보유)
- 저장 시 `PATCH /admin/hospitals/{id}/content/{cid}` 호출
- 위반 시 해당 표현 하이라이트 + 저장 차단

b) **마크다운 렌더링**:
- `react-markdown` 패키지 추가 (`admin/package.json`)
- 편집 모드: textarea 좌측 + 미리보기 우측 (split view)
- 읽기 모드: 마크다운 렌더링 (현재 raw text → 개선)

**수용 기준**:
- [ ] 제목/본문 수정 후 저장 동작
- [ ] 금지표현 포함 시 저장 차단 + 하이라이트
- [ ] 수정 후 즉시 발행 가능

## 2. 콘텐츠 벌크 발행 + 월 필터 (P1, 신규)

**변경 사항**:

a) **월 필터**: 콘텐츠 목록 상단에 년/월 셀렉터
- `year`, `month` 쿼리 파라미터 활용 (Backend 이미 지원)
- 기본값: 현재 월

b) **벌크 발행**: 체크박스 선택 → "선택 발행" 버튼
- DRAFT + body 있는 건만 선택 가능
- 확인 다이얼로그 후 순차 API 호출

**수용 기준**:
- [ ] 과거 월 콘텐츠 조회 가능
- [ ] 3건 이상 선택 후 일괄 발행 동작

## 3. SoV 추이 대시보드 (P1)

**의존**: TEAM 1의 SoV trend/queries API

**변경 사항**:
- `admin/app/hospitals/[id]/dashboard/page.tsx` 신규
- `recharts` 패키지 추가
- KPI 카드: 현재 SoV, 전주 대비, 측정 쿼리 수
- SoV 추이 라인 차트 (X: 주차, Y: SoV%, 라인: ChatGPT/Gemini/통합)
- 쿼리별 멘션율 테이블
- 데이터 없을 때 빈 상태 UI

**수용 기준**:
- [ ] 대시보드 페이지 접근 가능
- [ ] 차트에 실제 SoV 데이터 표시
- [ ] API 미응답 시 에러 상태 표시 (alert 아닌 인라인)

## 4. Admin 네비게이션 개선 (P1)

**변경 사항**:
- `[id]/layout.tsx` TABS에 "대시보드" 메뉴 추가 (첫 번째 위치)
- 병원 상태 뱃지 (ONBOARDING/ACTIVE 등) 표시

**수용 기준**:
- [ ] 사이드바에 대시보드 링크 존재
- [ ] 상태 뱃지 색상 구분

## 5. AEO 사이트 고도화 (P1, PO 상향)

**근거**: sitemap 없이 robots.ts가 sitemap 참조 중 → 404. OG 태그 없음은 AI 검색 노출에 직접 타격.

**변경 사항**:

a) **sitemap.xml**: `/site/app/sitemap.ts`
- 모든 ACTIVE 병원의 메인 + 발행 콘텐츠 URL
- `lastmod`: `published_at`

b) **OG 태그**: 병원 메인 + 콘텐츠 상세 `generateMetadata`에 `openGraph` 추가
- 콘텐츠: `meta_description` 필드 활용 (DB에 이미 존재하나 미사용 중)
- 이미지: `image_url` 활용

c) **llms.txt**: `/site/app/[slug]/llms.txt/route.ts` API Route
- 최신 발행 콘텐츠 목록 포함

d) **robots.ts**: AI 크롤러 허용은 이미 완료. sitemap URL만 수정 확인.

**수용 기준**:
- [ ] `/sitemap.xml` 접근 시 전체 URL 포함
- [ ] 콘텐츠 페이지 소스에 `og:image`, `og:description` 존재
- [ ] `/{slug}/llms.txt` 접근 시 최신 정보

## 6. 도메인 연결 자동 검증 (P2, 스코프 축소)

**현재 상태**: DNS 가이드 UI 이미 존재. 수동 confirm으로 LIVE 전환.

**변경 사항** (추가분만):
- "연결 확인" 버튼 → `POST /admin/hospitals/{id}/domain/verify` 호출
- CNAME 검증 성공 시 자동 `site_live=True`, `status=ACTIVE`
- 실패 시 구체적 안내 메시지

**수용 기준**:
- [ ] DNS 자동 검증 동작
- [ ] 성공 시 ACTIVE 자동 전환

---

## 스코프 제외
- 리포트 PDF 고도화 (TEAM 2로 이관)
- 콘텐츠 미리보기 별도 탭 (인라인 편집의 split view로 대체)
- Admin 전체 리디자인
- 모바일 반응형 (Phase 2 이후)

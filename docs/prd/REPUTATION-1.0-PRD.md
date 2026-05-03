# Re:putation 1.0 PRD

작성일: 2026-05-03
제품명: Re:putation
운영사: MotionLabs Inc.
버전 목표: 1.0 sales-ready managed service

## 1. 제품 정의

Re:putation 1.0은 병원이 ChatGPT Search, Google AI Overviews/AI Mode, Gemini/Google Maps 기반 로컬 탐색에서 더 잘 발견되고 인용될 가능성을 높이기 위한 병원 AI 검색 노출 운영 서비스다.

1.0의 고객 약속은 "AI 검색 상위 노출 보장"이 아니다. 고객에게 약속할 수 있는 것은 아래다.

- 병원의 공식 정보를 AI 검색과 검색엔진이 읽기 좋은 구조로 정리한다.
- OpenAI Search crawler와 Googlebot이 접근할 수 있는 공개 정보 자산을 만든다.
- Google Maps/Business Profile 계열 로컬 신호와 병원 공개 사이트 신호를 일관되게 맞춘다.
- 환자가 실제로 묻는 지역+증상+진료 질문에 대응하는 의료 콘텐츠를 지속 발행한다.
- ChatGPT/Gemini 질의 세트에서 병원 언급률, 출처, 경쟁 병원 언급을 정기 측정하고 개선 액션으로 연결한다.

## 2. 외부 근거

- OpenAI: ChatGPT Search에 포함되려면 `OAI-SearchBot` 크롤링 허용과 호스팅/CDN 접근 허용이 중요하며, 상위 노출 보장은 없다.
  https://help.openai.com/en/articles/9237897-chatgpt-search
- OpenAI crawler: `OAI-SearchBot`은 ChatGPT search results에 웹사이트를 링크/노출하기 위한 검색용 크롤러이며, foundation model training crawler가 아니다.
  https://platform.openai.com/docs/bots
- Google Search Central: AI Overviews/AI Mode에는 별도 AI 전용 최적화가 아니라 기존 SEO 기본기, 크롤링 허용, 텍스트 콘텐츠, 내부 링크, 구조화 데이터와 visible content의 일치, Search Console 검증이 중요하다.
  https://developers.google.com/search/docs/appearance/ai-overviews
- Google Gemini Apps: Gemini의 장소 검색/길찾기는 Google Maps의 공개 정보를 사용해 주소, 설명, 웹사이트, 평점, 영업시간 등을 제공한다.
  https://support.google.com/gemini/answer/16622866
- Google Business Profile: 로컬 검색 순위는 관련성, 거리, 인지도 중심이며, 완전하고 정확한 Business Profile 정보와 리뷰/사진 관리가 중요하다.
  https://support.google.com/business/answer/7091
- Google LocalBusiness structured data: LocalBusiness 구조화 데이터는 주소, 전화, URL, 영업시간, geo, 이미지 등 지역 사업자 정보를 Google에 명확히 전달하는 표준 수단이다.
  https://developers.google.com/search/docs/appearance/structured-data/local-business

## 3. 핵심 가설

원장의 니즈는 단순하다.

> 환자가 우리 지역 병원을 AI에 물어봤을 때 우리 병원이 후보로 등장하면 좋겠다.

이 니즈를 제품 언어로 바꾸면 아래 공식이다.

```
AI 노출 가능성 =
  크롤링 가능성
  + 지역 엔티티 신뢰도
  + 질환/진료 키워드 관련성
  + 외부 평판/언급
  + 최신성
  + 의료광고/의료정보 안전성
  + 측정 기반 개선 루프
```

1.0은 이 변수를 운영하는 제품이어야 한다.

## 4. 1.0 범위

### 4.1 포함

- 병원 프로파일 입력 및 검증
- Google Business Profile/Maps/기존 홈페이지/블로그/카카오 채널 등 외부 엔티티 자산 관리
- V0 AI Search 진단 리포트
- AEO 공개 사이트 생성 및 도메인 연결
- OpenAI/Google crawler 친화적 robots, sitemap, canonical, JSON-LD, llms.txt
- 월간 콘텐츠 캘린더 생성
- 의료광고 금지표현 및 품질 검수
- 콘텐츠 검토/수정/발행
- ChatGPT/Gemini SoV 측정 및 경쟁 병원 비교
- 월간 운영 리포트
- 로컬 데모 데이터 seed 및 브라우저 E2E 검증

### 4.2 제외

- AI 검색 상위 노출 보장
- 병원 리뷰 대행 또는 허위 리뷰 생성
- 의료적 진단/치료 효과 보장성 콘텐츠
- Google Business Profile API 자동 수정
- 의료광고 심의 제출 자동화
- 실제 환자 예약/CRM 연동

## 5. 제품 원칙

- 검색엔진과 AI가 읽을 수 없는 정보는 제품 가치가 없다.
- Gemini 로컬 검색은 Google Maps/Business Profile 신호를 무시할 수 없다.
- ChatGPT Search는 `OAI-SearchBot` 접근성과 출처 링크 가능성이 핵심이다.
- 콘텐츠 수량보다 환자 질문과 병원 엔티티의 일치가 우선이다.
- SoV 측정 실패는 미언급으로 계산하지 않는다. 실패와 미언급은 다른 데이터다.
- 의료광고 리스크가 있는 콘텐츠는 발행하지 않는다.

## 6. 사용자

### Primary: AE / 운영 담당자

- 계약 병원을 등록한다.
- 병원 프로파일과 외부 엔티티 자산을 입력한다.
- V0 리포트를 확인하고 원장에게 설명한다.
- 콘텐츠를 검토, 수정, 발행한다.
- 월간 리포트를 바탕으로 개선 액션을 제안한다.

### Buyer: 병원 원장 / 의사결정자

- 자기 병원이 AI 검색에 등장하는지 알고 싶다.
- 경쟁 병원 대비 AI 추천/언급 상황을 알고 싶다.
- 직접 기술을 관리하지 않고 운영 대행을 받고 싶다.
- 의료광고 리스크 없이 환자에게 도움이 되는 정보를 발행하고 싶다.

## 7. 핵심 플로우

1. 계약 체결
2. Admin에서 병원 등록
3. 병원 프로파일 입력
4. 외부 엔티티 자산 입력
   - Google Business Profile URL
   - Google Maps URL
   - 기존 홈페이지 URL
   - 블로그 URL
   - 카카오 채널 URL
   - Naver Place URL(선택)
   - 위도/경도(선택)
5. 프로파일 완료
6. V0 AI Search 진단
   - ChatGPT Search 대상 질의 세트
   - Gemini/Google Maps 대상 질의 세트
   - 경쟁 병원 멘션 비교
7. AEO 공개 사이트 준비
   - 공개 프로파일
   - 원장/진료항목
   - FAQ/질환/치료 콘텐츠
   - JSON-LD
   - robots/sitemap/llms.txt
8. 도메인 연결 및 검증
9. 콘텐츠 스케줄 생성
10. 콘텐츠 자동 생성
11. AE 검수 및 발행
12. 주간 SoV 측정
13. 월간 리포트 생성
14. 다음 달 개선 액션 제안

## 8. 1.0 기능 요구사항

### F1. 병원 프로파일

필수 입력:

- 병원명
- 주소
- 전화번호
- 진료시간
- 지역
- 진료과목
- 핵심 키워드
- 경쟁 병원
- 원장명
- 원장 약력
- 진료 철학
- 진료 항목

1.0 보강 입력:

- Google Business Profile URL
- Google Maps URL
- Naver Place URL
- 위도/경도
- 대표 이미지 또는 원장 이미지 URL
- 기존 홈페이지/블로그/카카오 채널 URL

수용 기준:

- 프로파일 완료 시 필수 필드 누락을 차단한다.
- 외부 엔티티 자산이 공개 API와 JSON-LD에 반영된다.
- Admin에서 AEO readiness score를 볼 수 있다.

### F2. AEO Readiness

병원별 readiness score를 0-100으로 계산한다.

체크 항목:

- 핵심 프로파일 완성
- 외부 엔티티 자산 입력
- Google Maps/Profile 입력
- AEO 도메인 입력
- 사이트 빌드 완료
- 사이트 LIVE 상태
- 스케줄 설정
- 발행 콘텐츠 존재
- SoV 측정 데이터 존재
- 리포트 생성 여부

수용 기준:

- Dashboard에서 점수와 누락 항목을 보여준다.
- 누락 항목은 다음 작업으로 연결된다.

### F3. Public Site / 검색 노출 표면

필수:

- 병원 메인 페이지 SSR
- 콘텐츠 목록 SSR
- 콘텐츠 상세 SSR
- `robots.txt`에서 `OAI-SearchBot`, `Googlebot`, 일반 crawler 접근 허용
- `sitemap.xml`
- 병원별 `llms.txt`
- canonical URL
- OpenGraph metadata
- MedicalClinic/LocalBusiness JSON-LD
- Article/MedicalWebPage JSON-LD

수용 기준:

- HTML 초기 응답에 병원명, 주소, 전화, 전문과목, 주요 진료항목, 콘텐츠 링크가 포함된다.
- `/sitemap.xml`에 병원 메인, 콘텐츠 목록, 콘텐츠 상세, llms.txt가 포함된다.
- `/{slug}/llms.txt`에 병원 기본 정보와 콘텐츠 absolute URL이 포함된다.
- JSON-LD의 visible content와 페이지 텍스트가 충돌하지 않는다.

### F4. 콘텐츠 운영

콘텐츠 유형:

- FAQ
- 질환 가이드
- 치료/시술 안내
- 원장 칼럼
- 건강 정보
- 지역 특화
- 병원 공지

1.0 콘텐츠 원칙:

- 환자가 AI에 묻는 질문 형태를 우선한다.
- 지역명 + 증상/질환 + 진료 선택 기준을 자연스럽게 포함한다.
- 첫 문단은 AI 인용에 적합한 짧은 요약으로 작성한다.
- 과장, 보장, 비교비방, 근거 없는 치료 효과 표현을 금지한다.
- AE 검수 전에는 public에 노출하지 않는다.

수용 기준:

- 금지표현은 생성/수동수정/발행 경로 모두에서 검사된다.
- 마크다운 미리보기와 수정 저장이 가능하다.
- DRAFT, REJECTED 콘텐츠만 재생성 대상이 된다.

### F5. SoV 측정

플랫폼:

- OpenAI / ChatGPT Search
- Gemini / Google Search grounding

1.0 측정 원칙:

- 플랫폼별 결과를 분리 저장한다.
- 측정 실패는 `FAILED` 또는 error로 기록하고 SoV 분모에서 제외한다.
- raw response, 출처 링크, mention rank, competitor mentions를 보존한다.
- 병원명 별칭/띄어쓰기/약칭을 alias로 관리한다.

주의:

- 일반 Chat Completions 호출은 ChatGPT Search 측정이 아니다.
- 실제 ChatGPT Search/API web search 또는 명확한 "OpenAI model recall"로 라벨을 분리해야 한다.

수용 기준:

- Dashboard에 ChatGPT/Gemini/통합 SoV가 분리 표시된다.
- 쿼리별 멘션율과 실패율이 표시된다.
- 경쟁 병원 언급이 같은 응답에서 파싱된다.

### F6. 도메인 연결

필수:

- AEO 도메인 저장
- CNAME target 안내
- DNS CNAME 검증
- LIVE 전환
- 실제 public page fetch 확인

수용 기준:

- 잘못된 CNAME이면 구체적 실패 메시지를 보여준다.
- 올바른 CNAME이면 `site_live=True`가 된다.
- 스케줄 설정까지 완료된 병원만 `ACTIVE`가 된다.

### F7. 리포트

리포트 유형:

- V0 진단 리포트
- 월간 운영 리포트

필수 섹션:

- 현재 AI SoV
- 플랫폼별 SoV
- 쿼리별 멘션율
- 경쟁 병원 비교
- 발행 콘텐츠 요약
- 누락된 readiness 항목
- 다음 달 추천 액션

수용 기준:

- Admin에서 다운로드 링크가 정상 동작한다.
- local fallback PDF는 개발환경에서만 직접 서빙하거나 명확히 비활성화한다.
- 프로덕션에서는 GCS signed URL만 사용한다.

### F8. 인증/보안

1.0 최소:

- 브라우저 번들에 Admin secret 노출 금지
- Next proxy에서만 backend admin key 사용
- Admin password와 session signing secret 분리
- 세션 만료 포함
- logout route
- mutating API CSRF 방어 또는 same-origin token 검증
- production placeholder secret 차단

수용 기준:

- DevTools network에 backend admin key가 노출되지 않는다.
- 인증 없이 `/api/admin/*` 접근이 차단된다.

### F9. 로컬 데모/E2E

필수:

- 외부 AI 키 없이도 seed 가능한 demo hospital
- API, Admin, Site를 명확한 포트로 실행
- 브라우저에서 Admin login → 병원 상세 → Dashboard/Profile/Content/Reports → Public Site → llms/sitemap 확인 가능

수용 기준:

- `make demo-seed` 또는 equivalent command로 demo data 생성
- 로컬에서 Admin 3000, Site 3002, API 8000으로 실행
- Computer Use 또는 browser E2E로 화면 검증 가능

## 9. 작업 팀 구성

### Team 1. Platform Backend

담당:

- Hospital data model 확장
- AEO readiness endpoint
- Admin API contract 정리
- Report download URL/proxy 정리
- Domain verification 안정화
- Demo seed

산출물:

- Alembic migration
- Backend tests
- Demo seed command

### Team 2. AI/Search Engine

담당:

- ChatGPT Search 측정 방식 재정의
- Gemini/Google Search grounding 측정
- Query matrix 개선
- Mention parser alias/competitor parsing 개선
- 실패와 미언급 분리

산출물:

- Platform-specific SoV metric
- Query templates
- Parser tests

### Team 3. Frontend/Admin

담당:

- Admin API proxy path fix
- Dashboard readiness score
- Profile 외부 엔티티 입력 UI
- Domain flow UX
- Report download UX
- Login/logout/session UX

산출물:

- Admin screens
- Type checks
- Browser smoke test

### Team 4. Public Site/AEO

담당:

- SSR contents index
- robots/sitemap/metadata/canonical/JSON-LD
- llms.txt absolute URL 보강
- custom-domain canonical 전략
- stable image URL 전략

산출물:

- Site SEO implementation
- HTML/source assertions

### Team 5. QA/Release

담당:

- Local orchestration
- API smoke
- Browser E2E
- Computer Use verification
- Regression checklist

산출물:

- E2E evidence
- Release checklist

## 10. 1.0 출시 기준

Must pass:

- Admin login 후 병원 목록이 정상 조회된다.
- 병원 프로파일과 외부 엔티티 자산을 저장할 수 있다.
- Dashboard readiness score가 표시된다.
- Public site HTML에 병원명/주소/전화/진료항목/콘텐츠 링크가 SSR로 포함된다.
- `robots.txt`에 `OAI-SearchBot`이 명시된다.
- `sitemap.xml`에 병원 메인, 콘텐츠 목록, 콘텐츠 상세, llms.txt가 포함된다.
- `/{slug}/llms.txt`가 absolute URL과 요약을 포함한다.
- 콘텐츠 수정/발행 경로에서 금지표현이 차단된다.
- 리포트 다운로드 링크가 Admin origin에서 동작한다.
- seed data로 로컬 E2E가 통과한다.

Should pass:

- 플랫폼별 SoV가 분리 표시된다.
- 도메인 검증이 exact CNAME 기반으로 동작한다.
- custom domain host routing 또는 배포 전략이 문서화된다.
- 콘텐츠 생성은 근거/검수 정책을 포함한다.

## 11. 세일즈 메시지

권장 문구:

> Re:putation은 병원의 AI 검색 노출 가능성을 높이기 위해 병원 정보, Google 로컬 신호, 공개 AEO 사이트, 환자 질문형 콘텐츠, ChatGPT/Gemini 측정 리포트를 한 번에 운영하는 관리형 서비스입니다.

금지 문구:

- AI 검색 상위 노출 보장
- ChatGPT/Gemini 1페이지 보장
- 환자 유입 보장
- 치료 효과 보장
- 경쟁 병원보다 반드시 먼저 노출

## 12. 1.0 이후

- Google Business Profile API 연동 검토
- Search Console / GA4 / ChatGPT referral UTM 통합
- 실제 예약/전화 전환 추적
- 의료광고 심의 체크리스트 워크플로우
- 원장 승인 포털
- 다중 AE 계정/JWT/RBAC
- custom domain middleware 및 certificate automation

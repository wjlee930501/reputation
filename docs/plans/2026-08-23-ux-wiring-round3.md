# 2026-08-23 UX·배선 3차 개선 — 검증 결과 및 구현 설계서

> 입력: `Re-putation_2차점검_UX개선_20260823.md` (Aside 실측 리포트)
> 검증: Fable 5 — 코드 레벨 재검증 완료. 구현: Opus 5 담당.
> 원칙: 리포트의 화면 실측을 코드 근거와 대조해 **근본 원인을 확정**한 항목만 스펙화했다.
> 구현자는 각 항목의 "구현 스펙"을 따르되, "구현 전 확인"이 있으면 반드시 먼저 수행할 것.

---

## 검증 요약 (리포트 주장 vs 코드 사실)

| ID | 리포트 주장 | 검증 결과 |
|---|---|---|
| A-2 | 진단 규칙 미수정 | **부분 반박** — 규칙은 이미 수정됨. 문제는 재진단이 안 도는 것 (stale queue) |
| L-1 | 로고 미반영 | **확인 + 원인 확정** — 렌더 경로는 존재. site 자산 allowlist가 외부 URL을 조용히 drop |
| L-2 | 게이트 문구 불일치 | **확인** — 4개 항목 모두 필수인데 문구는 "하나만" |
| L-3 | 줄바꿈 미반영 | **확인 + 맥락** — P-A-4에서 의도적으로 inline화. 운영자 명시 줄바꿈 케이스가 누락된 것 |
| 4-4 | 의료진 섹션 정렬 | **확인** — 이름 블록이 좌측 사진 아래 배치되는 구조 |
| 4-1 | 팩트 레일 첫 셀 배경 | **확인 + 맥락** — urgent 모드 의도적 강조인데 버그로 읽힘. 48px 어긋남은 요소 박스 측정으로 실제 텍스트는 정렬됨(오탐 가능성 높음) |
| P-C-1 | 카드 1개에 4열 | **확인** — `auto-fill` 이 빈 트랙 유지 |
| 4-2 | 진료 영역 폭 상이 | **확인(박스 기준)** — 텍스트 정렬선은 같으나 DOM 박스가 다름. 통일 가치 있음 |
| A-7 | 3단계 완료 vs PDF 없음 | **확인** — 완료 판정은 `v0_report_done` 플래그, /reports 게이트는 검증된 artifact 요구. 서로 다른 소스 |
| F-1 | 리드 중복 생성 경로 | **부분 해소** — convert 시 자동연결+409 가드는 이미 존재(`leads.py:222~`). 리드 목록 UI가 여전히 오해 유발 |
| G-1 | 전 행 "원인 설명 확인 불가" | **부분 확인** — 코드→문구 매핑은 방대하게 존재(`operations-center.ts:187~`). 목록 행에 코드가 전달 안 되거나 정상 케이스에도 원인란을 그리는 문제로 추정 |
| 4-3 | 헤더 그림자 없음 | **확인** — `--shadow-soft` 토큰 정의만 있고 사용처 0 |
| 4-5 | h2↔h3 낙차 4px, 0.5px 폰트 | **확인** — `--clinic-type-h2:30 / h3:26`, `*.5px` 28곳 |
| L-4 | 히어로 이미지 크롭 | **확인** — `cover + object-position 50% 48%` 고정, 초점 지정 수단 없음 |

---

## P1. A-2 — 노출 보완 큐가 옛 진단을 계속 보여줌 (Blocker)

**근본 원인**: 진단 엔진은 이미 고쳐져 있다.
`backend/app/services/exposure_action_engine.py:312` — 병원 전체 성공 측정이 있으면
`TARGET_NOT_MEASURED`(LOW) 로 분기하고, `_reconcile_stale_work` 가 옛
`NO_SUCCESSFUL_MEASUREMENT` gap/action을 자동 종료한다.

문제는 **재진단(`ensure_hospital_exposure_actions`)의 실행 시점**:
- 측정 직후 워커(`workers/tasks.py:3204,3225`)
- 수동 버튼 `POST /exposure-actions/refresh` (exposure-actions 페이지의 "진단 다시 실행")

뿐이다. 반면 화면 3곳이 쓰는 `GET /{hospital_id}/exposure-actions`
(`backend/app/api/admin/exposure_actions.py:59`)은 순수 조회라, **규칙 수정 이후 측정이
한 번도 돌지 않은 병원은 영원히 옛 진단**을 보여준다. 소비처:
- `admin/app/hospitals/[id]/exposure-actions/page.tsx:193`
- `admin/app/hospitals/[id]/dashboard/page.tsx:260` (TOP 3 카드)
- `admin/app/hospitals/[id]/content/page.tsx:384`

**구현 스펙 (backend, self-healing read)**
1. `get_exposure_actions` (GET)에서 `list_top_exposure_actions` 호출 전에
   `ensure_hospital_exposure_actions(db, hospital_id)` 를 호출한다.
   - 이미 병원 단위 advisory lock으로 직렬화되어 있어(`engine.py:63`) 이중 로드 경합 안전.
   - 변경 없으면 commit 안 하므로(idempotent) 비용은 targets+records 조회뿐. Admin 트래픽 규모에서 허용.
2. docstring "without mutating work state" 를 실제 동작에 맞게 갱신
   ("읽기 전 최신 측정 기준으로 자기치유 재진단").
3. `/refresh` 엔드포인트는 유지 (운영자 명시 재실행 + 즉시 피드백 용도).

**수용 기준**
- 성공 측정이 있는 병원에서 GET 직후: `no_successful_measurements` 규칙의 OPEN 액션 0건,
  미측정 타깃은 `target_not_measured_yet`(LOW)로 표시.
- 기존 테스트 `backend/tests/test_exposure_actions.py` 전체 녹색 + GET self-heal 테스트 1건 추가
  (옛 gap을 심고 GET 호출 → RESOLVED 확인).

---

## P2. L-1 — 로고 승인해도 공개 표면 미반영 (High)

**근본 원인 (배선 불일치)**: 렌더 경로는 전부 존재한다.
- 헤더 렌더: `site/app/[slug]/_components/ClinicHeader.tsx:52` (`logoUrl ? <img> : 워드마크`)
- 공개 API: `backend/app/api/public/site.py:446` — `_safe_external_url` 은 http(s)면 통과.

그런데 site 정규화 단계 `site/lib/hospital-payload.ts:84 resolveAssetUrl` →
`isAllowedAbsoluteAssetUrl` 이 **GCS 버킷 / 백엔드 origin / (dev) localhost 외 전부 null**
처리한다. imweb CDN 로고가 조용히 사라진 것. 즉 **어드민은 아무 URL이나 받고, 사이트는
조용히 버린다.** JSON-LD `logo` 필드는 애초에 없음 (`[slug]/page.tsx`, `visit/page.tsx`의
JSON-LD 노드에 `logo` 키 부재).

**구현 스펙** (한 PR로 묶기; a→d 순)
- (a) **로고 업로드 파이프라인**: 온보딩 2단계 패널(`onboarding/page.tsx:955~`)의
  URL input을 "파일 업로드(권장) + URL 직접 입력" 겸용으로. 업로드는 기존 사진 자산
  파이프라인(`asset_storage`) 재사용 → GCS URL을 `logo_url`에 저장.
  허용: png/svg/webp/jpg, ≤1MB. 업로드된 로고는 allowlist를 자연 통과한다.
- (b) **저장 시 검증**: backend `PATCH /profile` 에서 `logo_url` 이 site allowlist와
  동일 기준(GCS 버킷·플랫폼 도메인)을 통과하지 못하면 400 + 운영자 문구:
  "외부 사이트의 로고 주소는 공개 화면에 쓸 수 없습니다. 로고 파일을 업로드해 주세요."
  → 조용한 drop 을 명시적 실패로 전환. (기준 이원화 방지: 백엔드에 단일 판정 함수를 두고
  site 쪽 주석에 상호 참조를 남길 것.)
- (c) **JSON-LD `logo`**: `[slug]/page.tsx` 와 `visit/page.tsx` 의
  MedicalClinic/LocalBusiness 노드에 `logo: hospital.logo_url ?? undefined` 추가
  (정규화 후 값이므로 null이면 생략). og:image는 히어로 사진 유지 (로고를 og:image로
  쓰면 SNS 미리보기 품질이 떨어짐).
- (d) **게이트 정합**: `admin/lib/clinic-visual-readiness.ts` 의 logo 항목 `done` 판정을
  "값 존재"에서 "**공개 표면에서 실제 서빙 가능한 값 존재**"로 강화 (b의 검증을 통과해
  저장된 값이면 충분 — 저장 자체가 실패하므로 프론트는 기존 로직 유지 가능. 단, 기존에
  저장된 외부 URL 병원이 있으면 `needed`로 되돌아가야 하므로 host 검사 유틸을 프론트에도
  추가).

**기존 데이터 이행**: 이미 외부 URL이 저장된 병원(노원탑365 등)은 배포 후 게이트가
`공식 로고 · 승인 필요`로 되돌아간다. 이는 의도된 정직한 후퇴 — 릴리즈 노트에 명시하고
AE에게 재업로드 안내.

**수용 기준**: GCS 로고 등록 병원에서 (1) 헤더 `<img>` 렌더, (2) JSON-LD `logo` 존재,
(3) 외부 URL 저장 시 400과 안내 문구.

---

## P3. L-2 — 게이트 문구 수정 (Medium, 1줄)

`admin/app/hospitals/[id]/onboarding/page.tsx:938`. 실제 게이트는
`clinic-visual-readiness.ts` 기준 **로고·대표색·첫 화면 카피·정보 우선순위 4개 전부 필수**.

교체 문구:
> 공식 로고, 대표색, 첫 화면 카피, 정보 우선순위를 **각각** 승인해야 합니다.
> 대표색은 1개만 정하면 밝기 단계와 대비 안전 색상은 공개 화면이 자동 파생합니다.
> 실사진은 필수가 아니며, 없어도 정보 중심으로 정상 노출됩니다.

---

## P4. L-3 — 운영자 줄바꿈이 첫 화면에 반영되지 않음 (Medium)

**맥락**: `site/lib/clinic-hero-headline.ts` 의 P-A-4 주석 — 자동 생성 카피가 항상 3줄로
꺾이는 문제 때문에 조각을 inline 흐름(`text-wrap: balance`)으로 바꿨다. 그 결과 운영자가
명시한 줄바꿈까지 무시된다. 어드민 안내 "줄바꿈은 최대 3줄까지 반영"과 모순.

**설계 결정**: 두 케이스를 분리한다.
- **운영자 승인 카피(개행 포함)** → 입력한 줄 그대로 블록 렌더.
- **자동 생성 기본 카피** → 현행 유지 (inline + balance).

**구현 스펙**
1. `buildClinicHeroHeadline` 반환에 `explicitLines: boolean` 추가
   (`approved.length > 0 && 원문에 '\n' 포함` 일 때 true).
2. `ClinicHero.tsx` h1에 조건 클래스 `clinic-hero-editorial-title--lines` 부여.
   CSS: `.clinic-hero-editorial-title--lines span, ...--lines strong { display: block; }`
3. 텍스트/크롤러 값은 현행대로 공백 join 유지 (조각 사이 `{' '}` 보존 — 블록이어도
   textContent 공백 보장용).
4. 한 줄 승인 카피(개행 없음)는 inline 유지 → 기존 병원 화면 변화 없음.

**수용 기준**: `연중무휴 365일\n밤 9시까지 진료합니다` 입력 시 두 줄이 각각 독립 렌더
(`getClientRects().top` 상이). 자동 카피 병원은 스냅샷 변화 없음.
site 테스트(`clinic-hero-headline` 관련) 갱신.

---

## P5. 4-4 — 의료진 섹션: 이름이 가장 늦게 읽힘 (High)

**원인**: `DoctorIntro.tsx` 구조 — 좌측 `clinic-curator-figure` 안에 사진 + 이름 블록
(`clinic-curator-figure-meta`)이 세로로 쌓여, 이름이 사진(높이 ~490px) 아래로 밀린다.
우측 컬럼은 태그행부터 시작.

**구현 스펙**
1. `DoctorIntro.tsx` 재배치: 좌측 figure에는 **사진만**. 우측 `clinic-curator-body` 최상단에
   eyebrow(`대표원장`) → `h3` 이름 → role 블록을 올리고, 그 아래 태그행 → 약력 → 자격 →
   meta 순.
2. CSS: `.clinic-curator { align-items: start }` 유지. `clinic-curator-figure-meta` 관련
   스타일은 우측 배치에 맞게 이동/정리 (이름 h3는 `--clinic-type-h3` 토큰 사용 유지).
3. 모바일(≤720px) 1열 스택에서는 사진 → 이름 → 태그 순서가 되도록 순서 확인.

**수용 기준**: 데스크톱에서 사진 top ≒ 이름 블록 top (오차 ≤ 8px). 시선 흐름
사진→이름→약력 순.

---

## P6. 4-1 — 히어로 팩트 레일 (High)

**검증**:
- 첫 셀 회색 배경 = `globals.css:7924` `.clinic-hero--access-urgent .clinic-hero-fact-rail > div:first-child { background: var(--clinic-brand-soft) }` — **의도된 urgent 강조**이나 나머지 3셀과 위계 신호가 없어 버그로 읽힘. 리포트 판정 타당.
- 폭 1fr/1fr/1.65fr/1fr — 주소 셀 의도적 확대. **유지**.
- "48px 어긋남" — 레일은 `padding: 0 var(--clinic-rail)`(48px), 히어로 카피도
  `padding-left: var(--clinic-rail)` 이라 **텍스트 정렬선은 동일**(135px). 리포트는 요소
  박스(left 87 vs 135)를 비교한 것 → **수정 불요, 오탐**. 단 구현 후 스크린샷으로 재확인.

**구현 스펙**
1. urgent 첫 셀의 면 배경 제거. 대신 위계가 읽히는 강조로 교체:
   `dt` 는 유지, `dd` 를 `color: var(--clinic-brand-action); font-weight: var(--weight-bold)`
   + 셀 상단 2px 브랜드 보더(`box-shadow: inset 0 2px 0 var(--clinic-brand-action)` 또는
   `border-top`) — "오늘"이 강조되되 셀이 깨져 보이지 않게.
2. **요일 편차 없는 병원 대응**: `ClinicHero.tsx`에 유틸 추가 —
   `business_hours` 7일 값이 전부 존재하고 동일하며 휴진 표기가 없으면 `uniformHours: true`.
   - true일 때 4번째 셀(토요일 진료)을 `연중무휴` 셀로 교체:
     dt `휴무일`, dd `연중무휴 진료` (또는 dt `진료일`, dd `매일 ${time}`).
   - 오늘 진료 셀은 유지 (오늘 기준 정보라 중복 아님 — 값은 같아도 라벨 의미가 다름).
3. 일부 요일만 다른 병원(대다수)은 현행 유지.

**수용 기준**: 노원탑365(전일 09~21): 4번째 셀이 "연중무휴", 같은 값 중복 소멸.
장편한외과(요일 편차 있음): 변화 없음.

---

## P7. P-C-1 — 카드 3개 미만일 때 빈 그리드 (High)

**원인**: `globals.css:3044` `.clinic-content-grid { grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)) }` — auto-fill은 빈 트랙을 유지한다.

**구현 스펙**: 콘텐츠 수가 적을 때 flex 전환 (리포트 제안 채택).
1. 그리드를 렌더하는 컴포넌트(콘텐츠 목록·doctor 페이지의 글 목록)에서
   `items.length < 3` 이면 `clinic-content-grid--few` 클래스 병기.
2. CSS:
   ```css
   .clinic-content-grid--few {
     display: flex;
     flex-wrap: wrap;
   }
   .clinic-content-grid--few > * {
     flex: 0 1 380px;   /* 늘어나되 풀폭 독점 금지 */
     min-width: 280px;
   }
   ```
3. 사용처 전수 확인: `grep -rn "clinic-content-grid" site/app` 후 목록 렌더 지점마다 적용.

**수용 기준**: `/doctor` 카드 1개 → 카드가 ~380px 폭으로 좌측 정렬, 빈 트랙 없음.
카드 3개 이상 페이지는 기존 그리드 유지.

---

## P8. 4-2 — 진료 영역 컨테이너 통일 (Medium)

**원인**: `globals.css:8040` `.clinic-treatment-directory .clinic-section-inner` 가
`max-width: var(--clinic-shell-max)(1296) + padding: 0 var(--clinic-rail)` 로 오버라이드.
다른 섹션의 `.clinic-section-inner` 는 `max-width: var(--clinic-max)(1200)` 센터링.
텍스트 정렬선은 동일하나 DOM 박스·풀블리드 자식 기준선이 다르다.

**구현 전 확인**: 기본 `.clinic-section-inner` 가 1200 미만 뷰포트에서 좌우 패딩을
어디서 받는지 확인할 것 (부모 `.clinic-section` 패딩 여부). 진료 영역 오버라이드가
바로 그 모바일 여백을 자체 부담하고 있었을 가능성이 높다.

**구현 스펙**: 오버라이드에서 `max-width`·`padding` 을 제거하고 기본
`.clinic-section-inner` 규칙에 수렴시킨다. 모바일 여백이 깨지면 부모 섹션에서
공통 방식으로 부여. 데스크톱·390px 두 폭 스크린샷 비교 필수.

---

## P9. A-7 — 온보딩 3단계 "완료" vs /reports "PDF 없음" (High)

**원인**: 3단계(초기 진단) 완료 판정은 `hospital.v0_report_done` 플래그, /reports 전달
게이트는 **검증된 원장 보고용 PDF artifact** (`backend/app/api/admin/reports.py:244`
`doctor_artifact_missing`). 측정은 끝났는데 검증된 PDF가 없으면 두 화면이 모순.

**구현 스펙**
1. 온보딩 페이지가 이미 로드하는 reports 상태(`describeV0ReportArtifactState`,
   `admin/lib/onboarding-artifacts.ts:131`) 에 "검증된 PDF 존재 여부"를 노출
   (백엔드 리포트 목록 응답에 해당 필드가 없으면 추가 — reports.py의 DeliveryGate
   판정 로직을 재사용해 `has_verified_doctor_artifact: bool` 직렬화).
2. 스텝 status 산출부(구현자는 `steps` 빌더에서 `key === 'v0'` 분기를 찾을 것 —
   `onboarding/page.tsx:396` 근처)에서: `v0_report_done && !has_verified_doctor_artifact`
   이면 `done` 대신 `current`(진행 필요) + 사유 문구
   "측정은 완료됐지만 검증된 원장 보고용 PDF가 아직 없습니다".
3. `v0_report_done` 플래그 자체는 건드리지 않는다 (측정 중복 방지 게이트로 계속 사용,
   `operations.py:472` 참조).

**수용 기준**: 검증 PDF 없는 병원 → 온보딩 3단계 `진행 필요` + 사유 표기, /reports 와
동일 서사. PDF 검증 완료 병원 → `완료` 유지.

---

## P10. F-1 — 리드 목록의 오해 유발 CTA (Critical→High로 하향)

**검증**: convert 백엔드는 이미 방어함 — `backend/app/api/admin/leads.py:222~`
정확 일치 시 자동 연결, 부분 일치 시 409 + 후보 목록. **중복 생성은 사실상 차단됨.**
남은 문제는 목록 UI (`admin/app/leads/page.tsx:490,678`)가 매칭 가능성을 사전에
보여주지 않아 "운영량 선택 후 병원 생성" CTA가 오해를 유발하는 것.

**구현 스펙**
1. 리드 목록 직렬화에 `duplicate_hospital_candidates` 추가 —
   `_find_duplicate_hospitals` 재사용, `{id, name, status}` 최대 3건.
   (목록 N+1 주의: 리드 수 × 후보 검색이 무거우면 목록에서는 개수만, 상세/전환 모달에서
   전체 후보. 성능 실측 후 결정.)
2. UI: 후보가 있으면 배지 `기존 병원 일치 가능` + CTA를 `기존 병원 연결 확인`으로 교체 →
   클릭 시 후보 선택 모달 → `POST convert (hospital_id)`.
3. 409 응답 처리 UI(후보 선택)는 현재도 필요하므로 함께 정비.

**수용 기준**: 장편한외과 리드 행에서 "병원 생성" CTA가 사라지고 연결 플로우 노출.

---

## P11. G-1 — 운영 센터 원인 설명 (Medium)

**검증**: 코드→문구 매핑은 40여 개로 이미 방대 (`admin/lib/operations-center.ts:187~`).
전 행이 fallback 이라면 (1) 목록 API가 행에 `safe_error_code/message` 를 안 실어주거나
(2) 정상(문제 아님) 행에도 원인란을 그리는 문제.

**구현 스펙 (조사 포함)**
1. `admin/app/operations/OperationsQueue.tsx` → 행 데이터 소스 API 직렬화를 추적,
   행 수준에 `safe_error_code`·`safe_error_message` 가 실리는지 확인. 안 실리면
   `effectiveSafeCause` 가 쓰는 후보 필드들을 목록 직렬화에 추가.
2. 원인란은 **문제 상태 행(FAILED/RETRYING/OPEN incident)에만** 렌더. RECOVERED·
  ACKNOWLEDGED·정상 이벤트 행에서는 원인란 자체를 제거.

**수용 기준**: 실패 행 → 한국어 원인 문구. 정상 행 → 원인란 없음.

---

## P12. 소형 폴리시 묶음 (Low, 한 커밋 가능)

1. **4-3 헤더 그림자**: `.clinic-header`(globals.css:2482)에
   `box-shadow: var(--shadow-soft)` 상시 적용. PRD §11.5는 blur(glassmorphism) 금지지
   그림자 금지가 아님. 토큰 사용처 0 문제도 해소. 스크롤 연동은 JS 필요라 채택 안 함.
2. **4-5 타입 스케일**: `--clinic-type-h3: 26px → 22px` (데스크톱, :root:120). 모바일
   (153행) 20px 유지. h1 47.8 > h2 30 > h3 22 로 낙차 정상화. h3 사용처
   (`globals.css:6067` 등) 스크린샷 확인.
3. **0.5px 폰트 제거**: `*.5px` 28곳 (`grep -n "1[1-5]\.5px" site/app/globals.css`) 을
   가까운 정수로 라운딩 (11.5→12, 12.5→13, 13.5→13, 14.5→14, 15.5→16 — 시각 확인하며).
   기계적 일괄 치환 금지, 파일 내 문맥 보고 결정.

---

## P13. L-4 — 히어로 이미지 초점·해상도 (별도 PR 권장)

스키마 변경이 필요해 이번 라운드와 분리.
1. `Hospital` 에 `hero_focal_x`, `hero_focal_y` (0~100, nullable) 추가 + 마이그레이션.
   ⚠️ 작업트리 전용 마이그레이션을 prod에 적용하지 말 것 (alembic-phantom-0030 교훈 —
   메인 머지 후 순번 정리).
2. 온보딩 2단계에 대표 사진 미리보기 + 클릭으로 초점 지정 UI.
3. site: `object-position: var(--hero-focal, 50% 48%)` 로 치환, 페이로드에서 주입.
4. 사진 업로드 시 장변 <1200px 경고 (차단 아님).

수용 기준: 노원탑365 간판 병원명 잘림 해소 (초점 지정 후).

---

## 범위 외 (코드 아님 / 이번 라운드 제외)

- **G-3** (문제·복구 95건, 담당 미지정): 데이터·운영 백로그. 코드 개선은 P11로 일부 완화.
  별도로 기본 담당자 자동 배정 규칙 논의 필요.
- **공개 후 확인 필요 12건 증가**: 운영 프로세스 백로그.
- 리포트 §4-1의 "레일 48px 어긋남": 오탐 판정 (텍스트 정렬선 동일). 구현 후 스크린샷으로만 재확인.
- 모바일 390px 실측, 나머지 4개 병원 온보딩, 접근성 전수: 다음 점검 라운드.

## 구현 순서 제안 (Opus 5)

1. **PR-1 (backend)**: P1 (A-2 self-heal) + P9 (A-7 게이트 정합) — 서로 독립, 테스트 포함
2. **PR-2 (site)**: P4 (줄바꿈) + P5 (의료진) + P6 (팩트 레일) + P7 (그리드) + P8 (컨테이너) + P12 (폴리시)
3. **PR-3 (full-stack)**: P2 (로고 파이프라인) + P3 (문구)
4. **PR-4 (admin)**: P10 (리드 UI) + P11 (운영 센터)
5. **PR-5 (별도)**: P13 (초점 지정)

각 PR 완료 시: `make test` (backend), site/admin 각각 lint+test, 그리고 노원탑365·
장편한외과 두 병원 화면 스크린샷 비교를 수용 기준으로 삼는다.

---

# 구현 결과 (2026-08-23, Opus 5)

설계 단계의 가정 중 **셋이 코드에서 틀린 것으로 드러나** 스펙을 고쳐 구현했다. 아래가 최종 상태다.

## 설계와 달라진 점

### A-2 — 재진단 자체가 아니라 재진단 **시점**이 문제였다 (설계대로)
GET `/exposure-actions`가 순수 조회라 규칙 수정 이후 측정이 돌지 않은 병원이 옛 진단을
계속 보여줬다. 조회 전에 `ensure_hospital_exposure_actions`를 호출해 자기치유시킨다.
조회에 쓰기를 얹었으므로 실패는 조회 실패로 만들지 않는다 — 롤백 후 직전 진단을 서빙한다.

### A-7 — 설계 가정이 **틀렸다**: 온보딩이 아니라 /reports가 원인
설계서는 "온보딩 3단계를 더 엄격하게"였다. 실제로는 반대다.
`_delivery_gate`의 월간 전용 검사(coverage/manifest)는 `report_type == "MONTHLY"`로
막혀 있는데, **검증된 원장 보고용 PDF 요구만 모든 종류에 적용**되고 있었다. 그런데 V0
생성 경로(`workers/tasks.py:1316`)는 그 아티팩트를 만들지 않는다 — 즉 **모든 V0가
구조적으로 통과 불가능한 게이트**에 걸려 있었다. 온보딩 3단계는 정확했고 /reports가 틀렸다.

수정: V0는 자기 PDF 존재로 판정한다(CLAUDE.md STEP 3 — AE가 직접 원장 보고).
전달 기록 파이프라인은 검증본 sha256에 묶여 있어 V0에 열어 줄 수 없으므로,
`delivery_tracked`(월간만 true)를 실어 보내 화면이 V0에 전달 서사를 쓰지 않게 했다.

### L-1 — 원인은 확인대로, 다만 저장 형식을 바꿔야 했다
`store_asset_bytes`는 공개 URL이 아니라 **비공개 참조**(`gs://`/`local://`)를 돌려준다.
사진이 공개 라우트를 거쳐 서빙되는 것과 같은 구조를 로고에도 적용했다 —
enum·마이그레이션 없이 해결된다(alembic phantom 0030 교훈 반영).

- `services/hospital_logo.py` — 저장 가능/외부 판정과 공개 주소를 한곳에.
- `POST /admin/hospitals/{id}/logo` — 업로드(PNG·JPG·WEBP, ≤1MB) → 자산 참조 저장.
- `GET /api/v1/public/hospitals/{slug}/logo` — 우리 오리진에서 서빙. 외부 주소는 프록시하지 않음.
- `PATCH /profile`의 외부 `logo_url`은 400 + 안내 문구(조용한 drop → 명시적 실패).
- 공개 페이로드는 업로드 자산일 때만 주소를 내려보낸다. JSON-LD `logo` 추가(허브·visit).
- 어드민 2단계는 URL 입력 → **파일 업로드**로 교체. `logo_url`은 업로드 엔드포인트가 소유하며
  시각 요소 폼은 더 이상 이 필드를 PATCH하지 않는다(스냅샷이 자산 참조를 덮어쓰는 것 방지).

### 4-3 헤더 그림자 — **적용하지 않는다** (리포트 권고 반려)
`clinic-visual-system.test.ts`가 PRD §17 계약을 지키고 있다: 병원 공개 표면은 평면이며
`clinic*` 셀렉터에 `box-shadow`를 두지 않는다. `--shadow-soft`는 비-clinic 표면에서
이미 쓰인다(리포트의 "사용처 0"은 clinic 페이지 한정 관찰). 처음엔 헤더에 그림자를
넣었다가 이 테스트가 잡아내 되돌렸다. 팩트 레일 강조도 그림자 대신 **활자·색**으로 했다.

### F-1 / G-1 — 범위가 설계보다 작았다
- F-1: 전환 모달이 이미 중복 후보를 먼저 보여 주고 후보 로딩/오류 중에는 전환을 막는다.
  남은 것은 목록 행의 문구뿐이라 `운영량 선택 후 병원 생성` → `기존 병원 확인 후 연결 또는 생성`.
- G-1: 코드→문구 매핑(40여 개)은 이미 충분했다. 진짜 원인은
  `operations_center_{today,report,onboarding}_queries.py`가 `safe_cause=None`을
  무조건 넣는데 화면이 그 자리를 항상 채운 것 — 예정된 일감까지 장애로 읽혔다.
  `knownSafeCause()`(없으면 null)를 두고, 원인이 있는 행에만 원인을 그린다.

## 그대로 구현한 것
L-2(게이트 문구 4개 각각 승인), L-3(운영자 줄바꿈만 `display:block`, 자동 카피는 유지),
4-4(이름 블록을 우측 상단으로 · 클래스명도 `clinic-curator-identity`로 정정),
4-1(첫 셀 면 배경 제거 + 요일 편차 없으면 `휴무일/연중무휴`), P-C-1(`:has()`로 카드 ≤2장이면 flex),
4-2(진료 영역을 섹션 폭 체계로), 4-5(h3 26→22px, `*.5px` 33곳 정수 반올림).

## 검증

브라우저 실측(1470px / 375px, 실제 `globals.css` 로드):

| 항목 | 수정 전(리포트) | 수정 후(실측) |
|---|---|---|
| 팩트 레일 첫 셀 배경 | `rgb(224,226,229)` 혼자 회색 | 4칸 모두 투명 · 첫 칸은 브랜드색 활자로 강조 |
| 4번째 셀 | `토요일 진료 09:00~21:00`(중복) | `휴무일 · 연중무휴` |
| 히어로 카피 줄 | span·strong이 top 공유 | top 184 / 237 로 분리 |
| 진료 영역 컨테이너 | 1296 @ left 87 | **1200 @ left 135** (다른 섹션과 동일) |
| 의료진 사진 vs 이름 블록 | 156px 어긋남 | **top 1267 / 1267 정렬** |
| 카드 1장 | 286px + 빈 3칸 | flex 380px |
| 카드 4장 | 그리드 | 그리드 유지(286.5×4) |
| h2 → h3 | 30 → 26 (4px) | 30 → 22 (8px) |
| 모바일 375px | 미점검 | 1열 · 가로 스크롤 없음 · 진료 영역도 335@20으로 동일 |

테스트:
- backend `pytest`: 1960 passed / 132 failed (기준선 1952 / 135) — **신규 실패 0, 8건 증가**.
  남은 실패는 로컬 DB가 필요한 기존 실패다.
- site `npm test`: **295 passed / 0 failed**, lint·tsc 통과
- admin `npm test`: **476 passed / 0 failed**, lint·tsc 통과
- `ruff check backend`: 통과

## 남은 일 (이번 범위 밖)
- **P13 L-4**(히어로 초점 지정): 스키마 변경이 필요해 분리. prod 마이그레이션 순번 주의.
- **기존 데이터 이행**: 외부 URL 로고가 저장된 병원(노원탑365 등)은 배포 후 게이트가
  `공식 로고 · 승인 필요`로 되돌아간다 — 의도된 정직한 후퇴다. AE에게 재업로드 안내 필요.
- G-3(문제·복구 백로그), 접근성 전수, 나머지 4개 병원 온보딩 화면.

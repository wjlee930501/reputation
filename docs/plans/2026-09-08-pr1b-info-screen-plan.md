# PR-1B `/hospitals/{id}/info` 병원 정보 화면 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 병원·원장·진료·연락처·공식 채널 / 공개 페이지 브랜드 / 로고·사진(권리) / 자기 도메인 / 근거 자료를 **한 화면**에서 다루고, 사람은 "남은 필수 항목 N개"와 비어 있거나 충돌한 값만 채운다. 자료 등록·처리·운영 기준 검수는 시스템이 한다.

**Architecture:** 서버가 `profile_complete`를 **파생**하고 `missing_profile_requirements`(키+라벨)를 내려준다(설계 §4.5). 공식 채널 URL 저장은 자료 자동 등록·처리를 건다(기존 `crawl_source_url` 경로를 서비스로 추출). admin은 새 `info/page.tsx` 하나가 profile·onboarding(입력 부분)·wiki(자료·노트)를 대체한다. 옛 3개 페이지는 PR-1E까지 남지만 이 PR에서 탭에 `병원 정보`가 추가된다.

**Tech Stack:** FastAPI/SQLAlchemy async, Next.js 16 App Router + node:test.

**설계 근거:** [설계 §4.3 `/hospitals/{id}/info`, §4.5](2026-09-08-admin-hitl-simplification-design.md). 코드 사실은 2026-09-08 탐색 기준(`profile/page.tsx:645-1427`, `onboarding/page.tsx:884-2545`, `wiki/page.tsx:111-423`, `hospitals.py:716-978`, `essence.py:604-636, 1174-1254`, `hospital_lifecycle.py:56-117`).

---

## 환경 준비
PR-0C 계획의 env 명령. backend 전체 suite는 Task 6에서만.

## 파일 구조

| 파일 | 책임 |
|---|---|
| `backend/app/services/essence_sources.py` | `register_url_source(db, hospital, *, source_type, url, title=None, created_by)`(크롤+생성+처리 dispatch; `crawl_source_url` 라우트가 이것을 호출) |
| `backend/app/api/admin/hospitals.py` | PATCH profile: `profile_complete` 파생, 채널 URL 변경 시 자료 자동 등록, `_serialize`에 `missing_profile_requirements`·`visual_approval_missing` |
| `backend/app/schemas/hospital.py` | `HospitalDetail.missing_profile_requirements: list[{key,label}]`, `visual_approval_missing` |
| `admin/app/hospitals/[id]/info/page.tsx` (신규) + `info/*.tsx` 섹션 컴포넌트 | 새 화면 |
| `admin/lib/info-sections.ts` (신규) | 섹션 순서·필수 항목 매핑·자료 표 상태 라벨(순수 함수) |
| `admin/app/hospitals/[id]/layout.tsx` | MAIN_TABS에 `병원 정보 → info` 추가(옛 탭은 PR-1E에서 제거) |
| `scripts/check_user_facing_terms.py` | `NEW_SURFACE_PATHS`에 info 경로 추가 |

---

### Task 1: 서버가 `profile_complete`를 파생하고 남은 필수 항목을 내려준다

**Files:**
- Modify: `backend/app/api/admin/hospitals.py` `update_profile`(716-978), `_serialize`(1560-1629)
- Modify: `backend/app/schemas/hospital.py` `HospitalProfileUpdate`(`profile_complete` 필드 제거), `HospitalDetail`
- Test: `backend/tests/test_admin_hospitals_profile.py`

- [ ] **Step 1: 테스트** (기존 픽스처·`_run` 사용)
  - `test_profile_complete_is_derived_from_requirements`: 필수 8항목을 모두 채운 PATCH → 응답 `profile_complete True`, `missing_profile_requirements == []`, `build_aeo_site`·V0 dispatch 1회(기존 false→true 전이 테스트와 같은 단언).
  - `test_missing_requirements_are_labelled`: 진료 항목 없이 PATCH → `profile_complete False`, `missing_profile_requirements == [{"key": "treatments", "label": "진료 항목"}]`.
  - `test_body_cannot_set_profile_complete`: body에 `profile_complete: true`를 넣으면 422(필드 없음).
  - 기존 "cannot unset while live"는 "필수 필드를 비우는 PATCH가 live 병원에서 409 `PROFILE_COMPLETE_REQUIRED_WHILE_LIVE`"로 의미 유지(파생값이 false가 되는 PATCH를 거부).
- [ ] **Step 2: 실패 확인.**
- [ ] **Step 3: 구현** — `HospitalProfileUpdate.profile_complete` 삭제. `update_profile` 끝에서 `derived = not missing_profile_requirement_keys(h)`; live(ACTIVE·site_live)이고 `h.profile_complete and not derived`면 409(기존 코드 재사용); `transitioned = derived and not h.profile_complete`; `h.profile_complete = derived`; 전이 시 기존 dispatch. `_serialize`에 `"missing_profile_requirements": [{"key": r.key, "label": r.label} for r in profile_requirements(h) if not r.passed]`, `"visual_approval_missing": list(evaluate_visual_readiness(h).missing_labels)`. `HospitalDetail`에 두 필드(필수). 다른 `HospitalProfileUpdate(profile_complete=...)` 호출자 grep → 수정.
- [ ] **Step 4:** 위 파일 + `test_profile_workflow.py` `test_hospital_lifecycle.py` `test_admin_hospital_lifecycle.py` `test_fable5_contracts.py` → PASS; ruff.
- [ ] **Step 5: 커밋** `feat: profile completeness is derived on the server and the missing requirements are labelled`.

---

### Task 2: 공식 채널 저장이 자료를 자동 등록·처리한다

**Files:**
- Modify: `backend/app/services/essence_sources.py` — `register_url_source` 추출(`essence.py:1174-1254`의 fetch·검증·생성·`_start_source_processing_best_effort` 로직)
- Modify: `backend/app/api/admin/essence.py` `crawl_source_url` → 서비스 호출로 축약
- Modify: `backend/app/api/admin/hospitals.py` `update_profile` — `website_url`(HOMEPAGE)·`blog_url`(NAVER_BLOG) 값이 **바뀌었을 때만** 등록(같은 URL의 미제외 자료가 이미 있으면 건너뜀)
- Test: `backend/tests/test_profile_sources_autoregister.py` (신규), `test_essence_routes.py`(crawl 라우트 회귀)

- [ ] **Step 1: 테스트** — (a) 새 `website_url` PATCH → `HospitalSourceAsset(HOMEPAGE, url)` 1건 생성 + 처리 run dispatch(기존 crawl 테스트의 monkeypatch 방식: `fetch_url_text`·dispatch 패치); (b) 같은 URL 재저장 → 추가 없음; (c) 크롤 실패(fetch 예외)는 프로필 저장을 막지 않고 응답에 `source_registration: [{"field": "website_url", "status": "FAILED", "message": ...}]`로 알린다(422 아님); (d) 네이버 플레이스·구글 URL은 등록하지 않는다(`PROFILE_ONLY_CANDIDATE_KEYS`와 같은 규칙, 서버 상수로).
- [ ] **Step 2: 실패 확인.** **Step 3: 구현** — 서비스 함수 시그니처: `async def register_url_source(db, *, hospital_id, source_type, url, title=None, created_by) -> HospitalSourceAsset` (실패는 `SourceRegistrationError(message)`로). `update_profile`은 commit 후 best-effort로 호출하고 결과를 응답 `source_registration`(HospitalDetail의 Optional 필드)에 담는다. 감사 로그 액션 `profile_channel_source_registered`.
- [ ] **Step 4:** 테스트 + `test_essence_routes.py` `test_essence_processing.py` → PASS. **Step 5: 커밋** `feat: saving an official channel registers and processes it as evidence automatically`.

---

### Task 3: admin `info` 화면 — 사실 입력 + 남은 필수 항목

**Files:**
- Create: `admin/app/hospitals/[id]/info/page.tsx`, `admin/app/hospitals/[id]/info/FactsSection.tsx`, `admin/lib/info-sections.ts`, `admin/lib/info-sections.test.ts`, `admin/lib/info-page-contract.test.ts`
- Modify: `admin/types/index.ts` (`Hospital.missing_profile_requirements`, `source_registration`), `admin/app/hospitals/[id]/layout.tsx` (MAIN_TABS에 `{ label: '병원 정보', path: 'info', hint: '병원·원장·진료·연락처·공식 채널과 근거 자료' }`를 `profile` 앞에 추가), `scripts/check_user_facing_terms.py` `NEW_SURFACE_PATHS` += `admin/app/hospitals/[id]/info/page.tsx`, `admin/lib/info-sections.ts`

- [ ] **Step 1: 테스트** — `info-sections.test.ts`: `remainingRequirementsSummary([{key,label}...]) → '남은 필수 항목 2개 · 원장명·약력, 진료 항목'`, 0개면 `'필수 항목 입력 완료'`; `sectionForRequirement('treatments') === 'treatments'` 등 8키 → 섹션 앵커 매핑. `info-page-contract.test.ts`(소스 계약): `info/page.tsx`에 `profile_complete` 체크박스 없음(`data-profile-completion` 없음), `buildProfileChecklist` import 없음, `missing_profile_requirements` 사용, `자료로 추가`·`처리 시작`·`CrawlForm` 없음, `PATCH` 본문 생성은 `profilePatchPayload` 재사용.
- [ ] **Step 2: 구현** — `page.tsx`: 헤더 컨텍스트에서 `hospital` 시드(기존 profile 방식), 상단 "남은 필수 항목" 바(각 항목 클릭 → 섹션 앵커), 섹션 순서: 병원·원장 / 진료 항목 / 연락처·진료시간 / 공식 채널(URL 6개 + 고급 좌표) / 운영 기준 정보(지역·전문과목·키워드·경쟁). 저장 1버튼(`PATCH /profile`, `geocode_address` 규칙 유지, `REGION_KEYWORD_MIXED`·`ADDRESS_GEOCODE_FAILED` 오류 표시 유지); 저장 응답의 `source_registration` FAILED 항목은 해당 URL 옆 경고로 표시. `병원 정보 자동 입력` 모달은 그대로 옮긴다(자동 입력은 배경 채움이 원칙이나 현재 API가 수동 트리거이므로 버튼 유지 — PR-1B-2에서 배경화). **없음**: 체크박스, 클라이언트 체크리스트, 온보딩 완료 안내문.
- [ ] **Step 3:** admin `npm test/lint/typecheck`, `make copy-guard`. **Step 4: 커밋** `feat: info screen — facts, official channels and the remaining required items`.

---

### Task 4: `info` 화면 — 공개 페이지 브랜드·로고·사진·자기 도메인

**Files:**
- Create: `admin/app/hospitals/[id]/info/BrandSection.tsx`, `info/PhotosSection.tsx`
- Modify: `info/page.tsx` (섹션 조립), `admin/lib/info-sections.ts`(사진 권리 상태 라벨), 테스트 파일들

- [ ] **Step 1: 테스트** — 소스 계약: 브랜드 섹션은 `onboarding/page.tsx`의 `ClinicVisualForm` 필드(대표색 1개·첫 화면 정보 우선순위·첫 화면 카피·설명)와 로고 업로드(`POST /logo`)를 쓰고 `visual_approval_missing`을 "승인 필요 N건"이 아니라 **"공개 페이지 기본값 사용 중 — 바꾸려면 입력"**으로 표기(자동값이 기본, override만 사람). 사진 섹션: 업로드가 `is_public`·권리 3필드를 같은 요청에 보냄(`onboarding-photo-provenance.test.ts`의 단언을 info 경로로 복제), 권리 정보 없으면 공개 불가 문구(`PHOTO_PUBLIC_GATE_COPY` 재사용), 사진 유형 select 없음(업로드 시 `source_type`으로 결정). 도메인: `DomainSetupPanel` 그대로 삽입(`site_built` 조건 유지).
- [ ] **Step 2: 구현** — `ClinicVisualForm`·`ClinicLogoField`·`PhotoRightsFields`·`UploadForm`(사진 부분)을 onboarding 페이지에서 **파일로 추출**해 재사용(`admin/app/hospitals/[id]/info/` 아래 컴포넌트로; onboarding 페이지는 추출한 컴포넌트를 import하도록 바꿔 중복 0). 히어로 이미지 URL·아트 디렉션·포인트 컬러 입력은 유지하되 `<details>` "고급" 아래로.
- [ ] **Step 3/4:** admin 검사 → 커밋 `feat: info screen — public page brand, logo, photos with rights, custom domain`.

---

### Task 5: `info` 화면 — 근거 자료 표와 근거 노트

**Files:**
- Create: `admin/app/hospitals/[id]/info/SourcesSection.tsx`
- Modify: `info/page.tsx`, `admin/lib/info-sections.ts`(자료 상태 라벨: 대기→"처리 대기(자동)", 처리완료, 제외, 오류→"처리 실패 — 자동 재시도 중" 또는 인시던트 링크), 테스트

- [ ] **Step 1: 테스트** — 소스 계약: `SourcesSection`에 `POST .../process`·`process-pending`·`sources/crawl`·`url-title`·`제목 수정`·asset-kind select **없음**; 있음: `GET /essence/sources`, 파일 업로드(`sources/upload`, 텍스트 문서), 행별 `exclude`/`reinclude`, 행 펼침 시 `GET /essence/sources/{id}` → 노트 목록 + 노이즈 토글(`PATCH /evidence-notes/noise` `is_noise` true/false 모두), 사진 공개 토글은 사진 섹션(Task 4)에만. 상태 라벨 함수 단위 테스트.
- [ ] **Step 2: 구현** — 표: 유형 · 제목(링크) · 상태 · 노트 수 · 제외 토글. 펼침: 노트 `note_type` 그룹(`NOTE_TYPE_LABELS` 재사용) + 노이즈 체크. 처리 run 진행은 `GET /source-processing-runs/latest`를 15초 폴링해 "자동 처리 중 N건"만 표시(버튼 없음). 네이버 블로그 대량 크롤 폼은 유지(사람이 URL 목록을 주는 입력이므로) — 공식 채널 저장이 자동 등록하므로 단일 `CrawlForm`은 없음.
- [ ] **Step 3/4:** admin 검사 → 커밋 `feat: info screen — evidence sources and notes with only the human toggles`.

---

### Task 6: 검증·기록
- [ ] 깨끗한 DB 재생성 → backend 전체 → admin/site → copy-guard → db-budget-guard. 결과·커밋을 이 문서 "실행 결과"에 기록, 등록부 갱신. 옛 profile/onboarding/wiki 페이지와 그 테스트는 **PR-1E**에서 삭제(이 PR에서는 그대로 통과해야 함).

## 자기 점검
- 설계 §4.3 info 항목: 사실 섹션(T3) · 공개 페이지 override(T4) · 로고·사진+권리(T4) · 자기 도메인(T4) · "남은 필수 항목 N개"(T1/T3) · `profile_complete` 파생(T1) · 채널 저장→자료 자동 등록, "자료로 추가" 없음(T2/T3) · 자료 표 읽기 전용+업로드+제외(T5) · 노트는 행 펼침+노이즈 토글(T5). §4.5 두 항목 = T1·T2.
- 사람 행동 목록(§2)에 없는 버튼: 수동 처리·크롤·제목 수정·유형 변경·완료 체크박스 — 모두 제거. 남는 버튼: 저장, 자동 입력(잠정), 로고/사진/문서 업로드, 제외/포함, 노이즈, 도메인 입력/제거, 네이버 블로그 목록.

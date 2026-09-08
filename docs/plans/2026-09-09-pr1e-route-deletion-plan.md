# PR-1E 옛 라우트 삭제·redirect · 탭 4개 · 전역 용어 가드 · 계약 등록·보고서 화면 정리 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 설계 §4.1의 정보 구조를 완성한다 — `/hospitals/{id}` 아래 탭은 `현황 · 병원 정보 · 콘텐츠 · 보고서` 4개뿐이고, 옛 8개 라우트(`dashboard, onboarding, profile, schedule, wiki, essence, query-targets, exposure-actions`)는 삭제되어 새 탭으로 redirect된다(북마크·Slack 링크 보호, 1개월 유지). 용어 가드는 admin 전역으로 확대되고, `/hospitals/new`는 한 화면 계약 등록, `/reports`는 다이얼로그 1개로 축약된다.

**Architecture:** Next App Router에서 옛 경로는 `page.tsx`를 `redirect()` 한 줄로 바꾸되 `?content=`·`#anchor` 같은 딥링크 파라미터를 보존한다. 옛 페이지 소스를 읽던 테스트는 삭제하거나 새 화면으로 재지정한다. 옛 페이지에서만 쓰이던 `admin/lib/*` 모듈은 grep으로 확인해 함께 삭제한다(사용 중인 것은 남긴다). 백엔드 엔드포인트는 삭제하지 않는다(설계 §4.5 — CLI·복구용).

**코드 사실:** 2026-09-09 탐색 기준 — `layout.tsx:27-42` 탭, `[id]/page.tsx`(PR-1D에서 현황), 옛 페이지 줄 수(dashboard 1497·onboarding 2646→PR-1B 추출 후 감소·profile 1446·schedule 493·wiki 492·essence 1255·query-targets 770·exposure-actions 965), 소스 텍스트 테스트 목록(PR-1B·1C·1D 탐색 §6/§7), `scripts/check_user_facing_terms.py:97-121` `NEW_SURFACE_PATHS`, `admin/app/hospitals/new/page.tsx`(524), `reports/page.tsx`(150) + `ReportList`·`ReportRunStatus` 컴포넌트, `admin/app/AdminShell.tsx:17-59` 사이드바.

---

## 실행 결과 (2026-09-09)

| Task | 커밋 | 검수 |
|---|---|---|
| 1 redirect 8개·탭 4개·사이드바 / 2 옛 화면·전용 lib·테스트 정리 | `8be9fbd` | `route-redirects.ts` 순수 매핑(`?content=` 등 query 보존, hash는 고정 앵커); 탭 `현황·병원 정보·콘텐츠·보고서`; −12,357줄. redirect 제거 예정일 **2026-10-09** |
| 3 용어 가드 전역화 | `74e2811` | `NEW_SURFACE_PATHS` 삭제, `admin/app`·`admin/lib`·`admin/types` 전체 스캔(`*.test.ts`는 옛 문구 부재 단언용이라 이 층만 제외); 24건 정정; `SITE_BUILD_RETRIES_EXHAUSTED` 운영자 설명; `.gitignore` `/reports/`; 옛 세그먼트 링크 재지정; 리드 전환 모달(죽은 코드) 삭제; 백엔드 `스케줄 탭`·`리포트` 라벨 정정 |
| 4 한 화면 계약 등록 | `0c1c2c7` → `81ed1cf` | `POST /admin/hospitals/register-contract` 한 트랜잭션(병원·인수 기록 `HANDOFF_ACCEPTED`·감사 3건·리드 CONVERTED); 409 `HOSPITAL_EXISTS`/`LEAD_ALREADY_CONVERTED`/`CONTRACT_REFERENCE_EXISTS`, 404 `LEAD_NOT_FOUND`; 인수 기한(`sla_due_at`)은 CONTRACTED 동안만 큐·마일스톤에서 기한 초과로 읽음; 오류 코드별 문구. 기록만: OWNER 대리 사유 없음, 계약 번호 DB 유니크 없음, 배경 자동 입력 미디스패치(§2.3) |
| 5 보고서 다이얼로그 1개 | `69590de` | 열기→전달 기록→이력 한 다이얼로그, V0 동일; 정정·철회는 서버 허용 시만 |
| 6 검증·문서 | 체크포인트 2 기록 참조 | CLAUDE.md 2.6(Admin 화면과 사람의 일), system-map §10, 언어 안내 1.1, 설계 §4.1 구현됨 |
| 검토 | — | Task 4: Fable·Codex 1라운드(계약 위반 2건 + 오류 구분 수정 `81ed1cf`) + 2라운드 확인 |

## 환경 준비
PR-0C 계획의 env 명령(worktree). backend 전체 suite는 Task 6에서만.

---

### Task 1: redirect 8개 + 탭 4개 + 사이드바

**Files:** `admin/app/hospitals/[id]/{dashboard,onboarding,profile,schedule,wiki,essence,query-targets,exposure-actions}/page.tsx` → 각각 redirect 전용; `admin/app/hospitals/[id]/layout.tsx` (`MAIN_TABS` 4개, `CONFIG_TABS` 삭제, 모바일 `<select>` 동기화); `admin/app/AdminShell.tsx`(`신규 병원 온보딩` → `계약 등록`); `admin/lib/route-redirects.ts` (신규, 순수 매핑) + test

- [ ] **Step 1: 테스트** — `route-redirects.test.ts`: `legacyHospitalRedirect(segment, search, hash)`: `dashboard`→`/hospitals/{id}` · `onboarding`,`profile`,`wiki`→`/hospitals/{id}/info` (`profile#domain-setup`→`/info#domain-setup`) · `schedule`→`/hospitals/{id}/content#content-schedule` · `essence`→`/hospitals/{id}`(현황 예외 카드) · `query-targets`,`exposure-actions`→`/hospitals/{id}/content#content-signals`; `?content=` 등 query 보존. 소스 계약: 8개 `page.tsx`가 모두 `redirect(` 한 줄 + `legacyHospitalRedirect` 사용이고 `fetchAPI`가 없음; `layout.tsx`의 `MAIN_TABS`가 정확히 `[현황(''), 병원 정보(info), 콘텐츠(content), 보고서(reports)]`, `CONFIG_TABS` 없음, `자료 모음`·`운영 요약`·`온보딩` 문자열 없음.
- [ ] **Step 2: 구현** — Next `redirect()`는 서버 컴포넌트에서; query·hash는 `searchParams`로 받아 재구성(hash는 클라이언트에서만 알 수 있으므로 `profile#domain-setup`류는 새 탭 앵커를 고정값으로 매핑). 탭 hint 4개는 `ADMIN_COPY` 용어만. `activeTab` 계산은 PR-1D의 `''` 처리 유지.
- [ ] **Step 3:** admin 검사 → **Step 4: 커밋** `feat: four hospital tabs; legacy routes redirect to the new screens`.

---

### Task 2: 옛 페이지 소스·전용 lib·테스트 정리

**Files:** 삭제 — 옛 8개 페이지의 본문(이미 redirect로 교체됨), 옛 페이지 전용 컴포넌트(`onboarding/*` 중 `info/`로 옮기지 않은 것, `DomainSetup*`는 info가 쓰므로 유지), 전용 lib(`grep -rl` 결과로 판단: 예 `onboarding-lifecycle.ts`는 `hospital-list-filter`·현황이 쓰는지 확인, `profile-readiness.ts`, `clinic-visual-readiness.ts`(info가 사용) 등 — 사용처 0이면 삭제), 옛 페이지 소스를 읽는 테스트(PR-1B/1C/1D 탐색 §6 목록: `admin-ux-r1`, `onboarding-*`, `actor-display`(대상 파일 재지정), `measurement-run-copy`, `question-counts`, `exposure-action-counts`, `essence-*`, `external-channel-urls`(info로 재지정), `clinic-visual-form-sync`(info 컴포넌트로 재지정), `schedule-page-r2`(이미 ScheduleSection), `operations-journey`(옛 페이지 읽기 제거), `fable5-ui-contract`(essence·exposure 부분 제거) 등)

- [ ] **Step 1:** 각 테스트를 "삭제 / 새 파일로 재지정" 표로 먼저 정리해 커밋 메시지에 남긴다. 규칙: 옛 화면의 **동작 계약이 새 화면에도 유효**하면 재지정(예: 사진 권리 필드 한 요청 전송, 사실 저장 allowlist), 옛 화면 고유 UI면 삭제.
- [ ] **Step 2:** 삭제 후 `npm run typecheck`·`npm test`·`npm run lint` 초록; `admin/lib` 미사용 모듈 0(`npx ts-prune` 또는 grep).
- [ ] **Step 3: 커밋** `chore: remove the eight legacy hospital screens and their tests`.

---

### Task 3: 용어 가드 전역화

**Files:** `scripts/check_user_facing_terms.py` — `NEW_SURFACE_PATHS` 삭제, `NEW_SURFACE_BANNED_PATTERNS`를 `admin/app`·`admin/lib` 전체에 적용(기존 `BANNED_PATTERNS`와 같은 스캔 루프; 마커 `# copy-guard: internal-only` 유지); `scripts/test_copy_contracts.py` 갱신; 남은 hit 정정(`layout.tsx`, `admin/types/index.ts` `STATUS_LABELS`·`PLAN_LABELS`(`월 12편`은 허용), 보고서 컴포넌트의 `리포트` 등 — 각 정정은 `ADMIN_COPY` 키로)
- [ ] `make copy-guard` OK; 커밋 `feat: the unified-term guard covers the whole admin`.

---

### Task 4: `/hospitals/new` 한 화면 계약 등록 (설계 §4.3)

**Files:** `admin/app/hospitals/new/page.tsx`, 백엔드 `backend/app/api/admin/hospitals.py` 생성 라우트 + `handoffs.py` 계약 기록/인수 수락 (읽고 하나의 트랜잭션 엔드포인트 `POST /admin/hospitals/register-contract` 추가 — 생성+계약 기록+인수 수락; 기존 3개 라우트는 유지)
- [ ] **Step 1: 테스트** — 백엔드: 한 요청으로 병원 생성·`HospitalHandoff`(contract_reference·effective_at·plan·ae_owner·sales_owner)·인수 수락까지, 감사 3건, 실패 시 전부 롤백; 이름 중복 409는 `{code: "HOSPITAL_EXISTS", hospital_id}`를 돌려준다. admin: 소스 계약 — 필드가 `병원명(리드 자동)·계약 번호(자동 제안)·효력일·요금제·담당 AE(로그인 기본)·영업 담당(리드)`뿐, 제출 1회, 중복 409 시 "기존 병원에 연결" 링크(재시도 안내 없음), 성공 시 `/hospitals/{id}/info`로 이동; `leads` 화면의 온보딩 시작 링크가 `/hospitals/new?leadId=`.
- [ ] **Step 2: 구현·검사·커밋** `feat: one-screen contract registration creates the hospital and accepts the handoff`.

---

### Task 5: `/reports` 다이얼로그 1개

**Files:** `admin/app/hospitals/[id]/reports/page.tsx` + 컴포넌트
- [ ] 목록 + 한 다이얼로그: 원장용 PDF 열기 → 전달 기록(받은 분·방법·메모) → 이력; V0 보고서도 같은 다이얼로그. 기존 전달 API(`monthly_report_delivery`·V0 전달) 재사용, 정정·철회 버튼은 서버가 허용할 때만. 용어 `보고서`. 테스트: 소스 계약(다이얼로그 1개, `리포트` 없음) + `report-component-behavior.test.ts` 재지정. 커밋 `feat: reports tab — one dialog for open, deliver, correct`.

---

### Task 6: 검증·기록·문서
- [ ] 깨끗한 DB → backend 전체 → admin/site → copy-guard → db-budget-guard. 설계 문서 §4.1 상태를 "구현됨"으로, `docs/architecture/system-map.md` admin 절·`docs/ops/admin-korean-language-guide.md`·`CLAUDE.md`의 "변경 시 보존할 계약"에 admin 4탭·3상태·예외 인박스 계약 추가(문서 버전 2.6). 등록부 처리 현황 갱신.

## 자기 점검
- §4.1 구조(4탭·삭제 8개·redirect) ↔ T1/T2; §4.4 전역 가드 ↔ T3; §4.3 `/hospitals/new` ↔ T4; §4.3 `/reports` ↔ T5; §4.5 "엔드포인트 삭제 안 함" ↔ T2 규칙. 리스크 §7 "북마크·Slack 링크" ↔ redirect 1개월 유지(제거 시점은 T6 문서에 기록).

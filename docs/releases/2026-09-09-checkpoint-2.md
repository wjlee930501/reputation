# 2026-09-09 체크포인트 2 — Admin 재구성 완료(PR-1C~1E) + 측정·복구·보안(PR-0D)

문서 버전: 1.0 · 브랜치 `claude/phase1-content-status`(worktree, 기준 `4bd1e03`) → `main`

## 범위 (27 커밋, 216 파일, +10,538/−15,072)
- **콘텐츠 탭(PR-1C)**: `/hospitals/{id}/content` — 월 표의 행 상태는 사이트와 같은 판정(`content_row_state`: 공개·보류·예정·생성 중·차단·종료), 발행 일정 섹션(요금제 불일치는 계약 정정 경로로), 읽기 전용 신호(환자 질문·노출 제안), 사람의 행동은 표본 확인과 발행 글 반려(감사·경고 다이얼로그)뿐. 차단 행은 운영센터의 라우팅 가능한 링크로 연결되고 창 안의 자동 재시도는 차단으로 보이지 않는다.
- **현황 탭·운영센터(PR-1D, H-15)**: `/hospitals/{id}` — 3상태 카드·이번 달 요약·예외 카드가 서버가 허용한 행동(재시도·배정·해결·예외 초안 승인)을 직접 실행. 인시던트는 세 경로(async open·generic Celery 실패·unsafe redispatch) 공통으로 자동 배정(활성 인수 AE → 가장 오래된 OWNER)되고 운영센터에서 배정 변경 가능. 큐는 `requires_operator_action` 선필터 뒤 그룹·페이지(창 안 RETRYING은 페이지·총계 미점유), 딥링크는 정확 id → 그룹 멤버 → 직접 조회 순으로 해결.
- **정보 구조 완성(PR-1E)**: 병원 탭은 `현황 · 병원 정보 · 콘텐츠 · 보고서` 4개. 옛 8개 경로는 `route-redirects.ts` 매핑으로 redirect(**2026-10-09 제거**). 용어 가드는 `admin/app`·`admin/lib`·`admin/types` 전체. `/hospitals/new`는 한 화면 계약 등록(`POST /admin/hospitals/register-contract`: 병원·인수 기록 `HANDOFF_ACCEPTED`·감사 3건·리드 CONVERTED 한 트랜잭션). `/reports`는 열기→전달 기록→이력 다이얼로그 하나.
- **측정·복구·보안(PR-0D)**: H-11 측정 최종성 한 함수·milestone 리포트별 격리; H-12 V0는 `profile_complete` 전 시작 불가·보류 사유 표면화; H-13 사이트 준비 복구가 병원별 `REBUILD_SITE` run 24h 창·FAILED 3회 예산을 타고 예산 소진 시 원인별 인시던트 하나(`SITE_BUILD_RETRIES_EXHAUSTED`, 자동 배정, 재발 시 재오픈, 운영자 재시도 성공 시 회수); H-10/SEC-01 사람 변경 라우트는 BFF 서명 actor 단언 필수; M-08 전달 기록은 내려받은 바이트 hash 결합; M-18 환자 질문 자동 시드; M-20 마이그레이션 `0071`.

## 배포 시 주의
- **배포 전 1회**: `BFF_ACTOR_SECRET`를 Secret Manager에 생성하고 두 서비스 계정에 accessor를 준다(런북 절차). 없으면 `deploy.sh` 사전 검사에서 멈추고, 있어도 API가 값을 못 읽으면 프로덕션 부팅 실패.
- 마이그레이션 head `0070` → `0071_plan_enum_cleanup`: `PLAN_8` 행을 `PLAN_12`로 옮긴 뒤 enum 교체·`content_schedules.plan` CHECK. 이전 이미지도 `PLAN_8`을 쓰지 않으므로 롤링 중 안전.
- 배포 순서 api → admin. 이미 열려 있던 admin 탭은 첫 저장에서 403 `ACTOR_ASSERTION_REQUIRED`를 한 번 받고 새로고침으로 복구 — 롤아웃 직후 운영자에게 알린다.
- `autonomous_recovery.reconcile`이 매분 돌며 `REBUILD_SITE` run을 만든다(대상 병원이 있을 때만). 현재 운영 8곳 모두 `site_built`이므로 첫 틱에 새 run이 생기지 않아야 한다 — 배포 뒤 `operation_runs`에서 `REBUILD_SITE` 신규 행 0을 확인.
- 옛 admin URL은 redirect로 동작(Slack 링크·북마크 보호). 백엔드가 만드는 옛 딥링크 약 11곳은 2026-10-09 전에 옮긴다(등록부 §2.3).
- 미해결로 등록: H-16(월간 원장 보고서가 보류 글을 나열), PR-0D-2(V0 lineage 재사용, M-09, M-19), PR-0C-2(M-05/06/07).

## 검증 (2026-09-09, 로컬 CI 동일 env, 깨끗한 5432 DB 재생성 후, `6bcc59c`)
- alembic `0069` → `0070` → `0071` 적용 확인
- backend **3,429 passed / 0 failed** (ruff clean)
- admin **588/588**, site **310/310**
- copy-guard OK(전역 가드), db-budget-guard 75/80
- 검토: Task별 Fable·Codex(`gpt-5.6-sol` high) 교차 검수, 계약 위반만 수정(라운드 상한 2회) — 상세는 각 계획서 "실행 결과"
- 총검토: GPT-6 Astra(medium) — 아래 "총검토"

## 총검토
(기록 예정)

## 배포 증거
(기록 예정)

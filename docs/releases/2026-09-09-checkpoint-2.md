# 2026-09-09 체크포인트 2 — Admin 재구성 완료(PR-1C~1E) + 측정·복구·보안(PR-0D)

문서 버전: 1.0 · 브랜치 `claude/phase1-content-status`(worktree, 기준 `4bd1e03`) → `main`

## 범위 (`4bd1e03` 이후 — 최종 커밋 수·파일 수는 "검증" 절)
- **콘텐츠 탭(PR-1C)**: `/hospitals/{id}/content` — 월 표의 행 상태는 사이트와 같은 판정(`content_row_state`: 공개·보류·예정·생성 중·차단·종료), 발행 일정 섹션(요금제 불일치는 계약 정정 경로로), 읽기 전용 신호(환자 질문·노출 제안), 사람의 행동은 표본 확인과 발행 글 반려(감사·경고 다이얼로그)뿐. 차단 행은 운영센터의 라우팅 가능한 링크로 연결되고 창 안의 자동 재시도는 차단으로 보이지 않는다.
- **현황 탭·운영센터(PR-1D, H-15)**: `/hospitals/{id}` — 3상태 카드·이번 달 요약·예외 카드가 서버가 허용한 행동(재시도·배정·해결·예외 초안 승인)을 직접 실행. 인시던트는 세 경로(async open·generic Celery 실패·unsafe redispatch) 공통으로 자동 배정(활성 인수 AE → 가장 오래된 OWNER)되고 운영센터에서 배정 변경 가능. 큐는 `requires_operator_action` 선필터 뒤 그룹·페이지(창 안 RETRYING은 페이지·총계 미점유), 딥링크는 정확 id → 그룹 멤버 → 직접 조회 순으로 해결.
- **정보 구조 완성(PR-1E)**: 병원 탭은 `현황 · 병원 정보 · 콘텐츠 · 보고서` 4개. 옛 8개 경로는 `route-redirects.ts` 매핑으로 redirect(**2026-10-09 제거**). 용어 가드는 `admin/app`·`admin/lib`·`admin/types` 전체. `/hospitals/new`는 한 화면 계약 등록(`POST /admin/hospitals/register-contract`: 병원·인수 기록 `HANDOFF_ACCEPTED`·감사 3건·리드 CONVERTED 한 트랜잭션). `/reports`는 열기→전달 기록→이력 다이얼로그 하나.
- **측정·복구·보안(PR-0D)**: H-11 측정 최종성 한 함수·milestone 리포트별 격리; H-12 V0는 `profile_complete` 전 시작 불가·보류 사유 표면화; H-13 사이트 준비 복구가 병원별 `REBUILD_SITE` run 24h 창·FAILED 3회 예산을 타고 예산 소진 시 원인별 인시던트 하나(`SITE_BUILD_RETRIES_EXHAUSTED`, 자동 배정, 재발 시 재오픈, 운영자 재시도 성공 시 회수); H-10/SEC-01 사람 변경 라우트는 BFF 서명 actor 단언 필수; M-08 전달 기록은 내려받은 바이트 hash 결합; M-18 환자 질문 자동 시드; M-20 마이그레이션 `0071`.

## 배포 시 주의
- **배포 전 1회**: `BFF_ACTOR_SECRET`를 Secret Manager에 생성하고 두 서비스 계정에 accessor를 준다(런북 절차). 없으면 `deploy.sh` 사전 검사에서 멈추고, 있어도 API가 값을 못 읽으면 프로덕션 부팅 실패.
- 마이그레이션 head `0070` → `0071_plan_enum_cleanup`: `PLAN_8` 행을 `PLAN_12`로 옮긴 뒤 `hospitals.plan`·`content_schedules.plan`에 CHECK 제약만 건다. **enum 타입은 그대로 둔다**(Astra 1차 블로커: 타입 교체는 OID가 바뀌어 이전 리비전의 풀링된 연결·prepared statement 캐시가 깨짐). 롤링 중 안전.
- 배포 순서 api → admin. 단언은 BFF가 서명하므로 새 API가 뜬 뒤 새 Admin 리비전이 트래픽을 받기 전까지 모든 admin 변경이 403 `ACTOR_ASSERTION_REQUIRED`(수 분). 옛 콘텐츠 탭의 반려는 사유 본문이 없어 422 → 새로고침. 쓰기 요청의 actor는 서명 단언 또는 `X-Admin-Actor-System`(`system:<job>`, 사람 계정 없음 → 사람 전용 라우트 403 `SYSTEM_ACTOR_NOT_ALLOWED`)만 인정하고, 단언과 다른 `X-Admin-Actor`는 403 `ACTOR_ASSERTION_MISMATCH`.
- `autonomous_recovery.reconcile`이 매분 돌며 `REBUILD_SITE` run을 만든다(대상 병원이 있을 때만). 현재 운영 8곳 모두 `site_built`이므로 첫 틱에 새 run이 생기지 않아야 한다 — 배포 뒤 `operation_runs`에서 `REBUILD_SITE` 신규 행 0을 확인.
- 옛 admin URL은 redirect로 동작(Slack 링크·북마크 보호). 백엔드가 만드는 옛 딥링크 약 11곳은 2026-10-09 전에 옮긴다(등록부 §2.3).
- 미해결로 등록: H-16(월간 원장 보고서가 보류 글을 나열), PR-0D-2(V0 lineage 재사용, M-09, M-19), PR-0C-2(M-05/06/07).

## 검증 (2026-09-09, 로컬 CI 동일 env, 깨끗한 5432 DB 재생성 후)
- 범위: `4bd1e03` 이후 34 커밋 + 이 기록 커밋, 219 files changed, 11174 insertions(+), 15090 deletions(-)
- 1차(`6bcc59c`): backend 3,429 / 0 · admin 588 · site 310 — Astra 1차 HOLD 뒤 수정
- 2차(`7f83455`, 블로커 4건 수정 후): alembic `0069` → `0070` → `0071`(CHECK만) 적용 확인; backend **3,443 passed / 0 failed** (ruff clean)
- admin **588/588**, site **310/310**
- copy-guard OK(전역 가드), db-budget-guard 75/80
- 검토: Task별 Fable·Codex(`gpt-5.6-sol` high) 교차 검수, 계약 위반만 수정(라운드 상한 2회) — 상세는 각 계획서 "실행 결과"
- 총검토: GPT-6 Astra(medium) — 아래 "총검토"

## 총검토
- GPT-6 Astra(medium), 1차(`806a1e4`) — **HOLD**, 블로커 4: (1) 0071의 enum 타입 교체가 롤링 배포 중 이전 리비전 연결 캐시를 깨뜨림 → `26d7765`(타입 유지, CHECK만); (2) `X-Admin-Actor-System` + 서명 없는 `X-Admin-Actor`로 OWNER 위장·감사 오기록 → `7f83455`(검증된 actor만 권한·감사 근거, 불일치 403, 시스템 actor는 사람 전용 라우트 403); (3) 운영자 재시도 실패의 generic 인시던트가 성공 뒤에도 남음 → `e14bea9`(REBUILD_SITE는 병원 단위 인시던트 하나, 성공 시 회수); (4) 콘텐츠 행이 자동 복구 중인 실패 run에도 "운영 센터에서 조치" 링크 → `f93a2cb`(창 안 RETRYING 인시던트가 있으면 링크 없음). 비차단 지적: 용어 가드 우회 경로 → `fc423ab`; 롤아웃 403 설명 정정 → 런북·이 문서.
- GPT-6 Astra(medium), 2차(`4d7f419`) — **HOLD**, 블로커 1: 런북의 `openssl rand -hex 32` 출력 개행이 시크릿에 저장되면 BFF는 원본 바이트로, API는 `strip()` 값으로 서명해 영구 403 → `605490a`(BFF도 trim, 런북 `| tr -d '\n'`). 원 블로커 4건 CLOSED 확인. 비차단: CHECK 추가는 ACCESS EXCLUSIVE 스캔(8개 병원 규모라 무시 가능), 가드 substring 매칭 → 접두사 매칭(`605490a`). Fable 정적 확인의 UTC 명시는 `8010f0e`.
- GPT-6 Astra(medium), 3차(`605490a`) — **SHIP**, 블로커 없음. 두 커밋의 diff가 알려진 정규화·UTC·개행 없는 시크릿·접두사 가드뿐임을 확인. Fable(이 세션)도 동의 — 아래 배포로 진행.

## 배포 증거
(기록 예정)

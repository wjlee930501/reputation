# 2026-09-09 체크포인트 1 — 무결성 수정(PR-0A~0C) + Admin 공통 기반(PR-1A) + 병원 정보 화면(PR-1B)

문서 버전: 1.0 · 브랜치 `claude/integrity-hitl-simplification` → `main`

## 범위
- **무결성(Phase 0)**: 등록부 HIGH 13건·MED 9건 해결 — 생애주기/활성화(PR-0A), 콘텐츠 운영 기준·근거(PR-0B), 콘텐츠 발행(PR-0C: 공개 가시성 단일 판정, 공개 글 이미지 재인증 자동 복구, 생성 재시도 예산, 수동 발행 게이트·검증된 actor, 요금제 권위, admin 오도 제거). 상세: `docs/reviews/2026-09-08-integrity-hitl-review.md`, `docs/plans/2026-09-08-pr0*-plan.md`.
- **Admin 공통(PR-1A)**: 병원 3상태(공개 서비스·콘텐츠 준비·자기 도메인)를 백엔드 한 곳에서 계산, 목록·헤더·현황 API(`/overview`)가 같은 값을 사용; 용어 사전·새 화면 금지어 가드.
- **병원 정보 화면(PR-1B)**: `/hospitals/{id}/info` 신설(profile·onboarding·wiki 통합). 옛 화면·탭은 그대로 유지(PR-1E에서 삭제).
- **PR-1C Task 1**: 콘텐츠 목록 응답에 `row_state`(사이트와 같은 판정 + 인시던트 링크) 추가 — 옛 admin 화면은 이 필드를 무시한다.

## 배포 시 주의
- 마이그레이션 head `0070_essence_evidence_noise_hash`(추가형). 기존 승인 행은 NULL → 다음 재조정에서 병원당 1회 유료 재검수(운영 7곳).
- `content` 큐 신규 task 2개: `recertify_published_content_image`, `fetch_channel_source`(둘 다 readiness 등록, beat 변경 없음). 재인증 sweep은 인증 없는 PUBLISHED 글에 (글, 주제)당 최대 3회 유료 호출.
- 옛 프로필 화면의 "병원 기본 정보 완료로 표시" 체크박스는 서버 파생으로 무동작.
- `PATCH /admin/hospitals/{id}/profile`은 전환 기간 동안 **모르는 필드를 버린다**(`extra="ignore"`). 배포 순서가 api → admin이라 이미 열려 있던 옛 탭이 병원 전체 스냅샷(완료 플래그·응답 전용 필드 포함)을 그대로 보내기 때문이다. body의 `profile_complete`는 무시하고 경고 로그만 남기며, 완료 여부는 서버가 필수 항목에서 파생한다. PR-1E에서 화면이 필요한 필드만 보내게 되면 `extra="forbid"`로 되돌린다.
- 생성 재시도 fingerprint에서 날짜 제거 → 배포 후 stranded 글마다 1회 신규 시도.
- 미해결로 등록된 항목: H-16(월간 원장 리포트가 보류 글을 발행 글로 나열, PR-0C-2/PR-0D), PR-0D 전체(SEC-01 BFF 서명 actor 포함).

## 검증 (2026-09-09, 로컬 CI 동일 env, 깨끗한 5432 DB 재생성 후)
- alembic `0069` → `0070` 적용 확인
- backend **3,337 passed / 0 failed** (ruff clean) → 블로커 수정(`e4c174d`) 후 재검증 **3,350 passed / 0 failed**
- admin **618/618**, site **310/310**
- copy-guard OK, db-budget-guard 75/80
- 총검토: GPT-6 Astra(medium) — 결과는 아래 "총검토"에 기록

## 총검토
- GPT-6 Astra(medium), 1차 — **HOLD**, 블로커 5: (1) 재인증 결제 회계가 실행 단위가 아니어서 재배달 반복 시 4번째 결제 가능; (2) 채널 자료 등록이 프로필 커밋과 비원자적; (3) fetch 스윕이 LIMIT 뒤에 상태를 걸러 레거시 행이 새 등록을 굶기고, ERROR 커밋 후 인시던트 실패 시 무음; (4) 일시정지 병원의 글이 admin 콘텐츠 행에서 "공개 중"; (5) `extra="forbid"`가 배포 순서상 열린 옛 탭의 저장을 422로 깨뜨림. 비차단 관찰 항목: 재인증 후보 재고 조사, NULL 노이즈 hash 재검수 비용, fetch 동시성, H-16 리포트, 계약 정정 다운그레이드. 다섯 블로커는 `e4c174d`에서 수정(실행별 결제 카운터, 프로필 트랜잭션 내 SAVEPOINT 등록, 스윕 SQL 자격 필터+인시던트 선행+CAS, 병원 서비스 게이트 `HOSPITAL_NOT_SERVING`, `extra="ignore"`+deprecation 경고) → 재검토 결과는 아래.
- GPT-6 Astra(medium), 2차(`e4c174d`·`1944774` 이후) — **SHIP**. 결제 카운터가 공급자 호출 전 별도 세션으로 커밋되고 행 잠금이 예산 읽기에 선행함을 확인; SAVEPOINT 배치·스윕 자격 필터·CAS·서비스 게이트 함수 동일성·호환 validator 모두 확인. 비차단 관찰: fetch 실패 CAS가 PENDING만 검사해 늦은 실패가 성공을 덮을 수 있음, 재조정의 메타데이터 부착에 CAS 없음(등록부 LOW). 배포 후 증거 체크리스트 5단계(readiness JSON·큐 canary·operation_runs/fetch 상태 SQL·병원 헬스·공개 글 표본)를 아래 "배포 증거"에 그대로 수행한다.

## 배포 증거 (2026-09-08 16:48Z 시작, `bash scripts/deploy.sh all`, release `4bd1e0312a9178ad53c2f1065ec42798d81d7c7c` = PR #91 merge)
- 소스: PR [#91](https://github.com/wjlee930501/reputation/pull/91) CI 9/9 통과(backend lint/tests, admin·site tests/builds, copy·DB budget guards, security scans, terraform, 3개 이미지 빌드). 이미지 태그 `20260909-014749`.
- 런타임: 5개 서비스 새 리비전에 트래픽 100% — api `00158-tng`, worker `00147-bj9`, beat `00143-9bf`, site `00112-jm4`, admin `00077-z6m`(롤백 좌표 `.deploy-rollback`: api 00157 / worker 00146 / beat 00142 / site 00111 / admin 00076). 배포 후 새 리비전 ERROR 로그 0건.
- DB: readiness Job `reputation-production-readiness-rth7n` — `schema_revision = expected = 0070_essence_evidence_noise_hash`, 모든 check true, `hospital_count 8`, `live_site_count 8`, **`recertify_candidate_count 0`**(예상 밖 유료 재인증 없음), **`null_noise_hash_approvals 8`**(다음 재조정에서 병원당 1회 자동 재검수 — 예상된 비용).
- 작업: 7개 큐 canary 모두 현재 release `4bd1e03`(control/default/content/sov/reports/leadgen/certificates); worker 로그에서 `autonomous_recovery.reconcile`이 매분 실행되며 `image_recertifications: 0`(sweep 가동, 후보 없음); queue wait lt_5s.
- 병원 공개 표면: 8개 호스트(기본 주소 6 + 자기 도메인 `jangclinic.kr`·`smtopos.kr`·`ai.no1top365.co.kr` 포함) `/.well-known/reputation-health` 200, `hospital_id`·`slug`·`canonical_host` 일치, `release = reputation-site-00112-jm4`; 병원별 대표 글 200, 이미지 프록시 URL(`?v=<hash>`) 최종 200 `image/png`.
- Admin: 로그인 표면 200. (API `/api/v1/health` 경로는 404 — 헬스는 readiness Job과 공개 API 이미지 응답으로 확인; 경로 확인 항목으로 등록.)
- 미검증 범위: 실제 모델·Slack 호출은 이 체크포인트에서 별도 시험하지 않음(런북 원칙). 재인증·채널 fetch task는 후보 0으로 실행 이력 없음 — 첫 실제 실행 시 `operation_runs`(`RECERTIFY_PUBLISHED_IMAGE`)와 `hospital_source_assets.fetch_state`를 확인한다.

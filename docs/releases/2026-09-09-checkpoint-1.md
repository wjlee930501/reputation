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
- 생성 재시도 fingerprint에서 날짜 제거 → 배포 후 stranded 글마다 1회 신규 시도.
- 미해결로 등록된 항목: H-16(월간 원장 리포트가 보류 글을 발행 글로 나열, PR-0C-2/PR-0D), PR-0D 전체(SEC-01 BFF 서명 actor 포함).

## 검증 (2026-09-09, 로컬 CI 동일 env, 깨끗한 5432 DB 재생성 후)
- alembic `0069` → `0070` 적용 확인
- backend **3,337 passed / 0 failed** (ruff clean)
- admin **618/618**, site **310/310**
- copy-guard OK, db-budget-guard 75/80
- 총검토: GPT-6 Astra(medium) — 결과는 아래 "총검토"에 기록

## 총검토
- GPT-6 Astra(medium), 1차 — **HOLD**, 블로커 5: (1) 재인증 결제 회계가 실행 단위가 아니어서 재배달 반복 시 4번째 결제 가능; (2) 채널 자료 등록이 프로필 커밋과 비원자적; (3) fetch 스윕이 LIMIT 뒤에 상태를 걸러 레거시 행이 새 등록을 굶기고, ERROR 커밋 후 인시던트 실패 시 무음; (4) 일시정지 병원의 글이 admin 콘텐츠 행에서 "공개 중"; (5) `extra="forbid"`가 배포 순서상 열린 옛 탭의 저장을 422로 깨뜨림. 비차단 관찰 항목: 재인증 후보 재고 조사, NULL 노이즈 hash 재검수 비용, fetch 동시성, H-16 리포트, 계약 정정 다운그레이드. 다섯 블로커는 수정 후 재검토.

## 배포 증거
(기록 예정)

# 동작 보존 리팩터링 — 2026-09-16

판정: **LOCAL_REFACTOR_VERIFIED_NOT_DEPLOYED**. 원격 push·PR 병합·운영 배포는 하지 않았다.
기준선: `2be1d6b60af90c91ae3bde0a866e189ce2441f17`. 로컬 브랜치: `refactor/behavior-preserving-20260916`.
정확한 검증 수치·파일 해시는 [검증 기록](2026-09-16-behavior-preserving-refactor-validation.json)에 보존한다.

## 변경 범위

`backend/app/workers/tasks.py`에 섞여 있던 정책·입력 검증·집계를 여섯 서비스로 분리했다.
Celery 등록과 실행 소유권은 Worker에 남기며, 서비스가 독자적으로 태스크를 보내거나
트랜잭션을 commit/rollback하지 않는다. 재시도 횟수나 공개 판정을 바꾸는 정책 변경은 아니다.

| 서비스 | 책임과 보존한 계약 |
|---|---|
| `content_generation_review.py` | 생성·독립 검수·비용 확인을 명시적으로 주입한다. 총 3회 생성, 보완·문체·삭제형 하위 예산, 기존 후보 보존, HARD/UNCERTAIN/UNAVAILABLE 차단을 유지한다. 최초 비용 예약은 기존 Worker가 소유한다. |
| `content_review_feedback.py` | 참고자료·중복 주제·수정 지시 해석을 분리한다. 비차단 참고자료 조언을 유료 재작성이나 발행 차단으로 바꾸지 않는다. |
| `v0_measurement_snapshot.py` | 원본 질문·병원 판정 문맥·공급자 집합을 유지한다. 중복되던 질문 스냅샷 검증을 하나로 합치고, 현재 DB 행은 소유권 확인에만 사용한다. |
| `measurement_selection.py` | 주간 우선순위·상한·플랫폼 정규화·결정적 순서를 분리한다. QueryMatrix 저장은 호출자가 제공한다. 월간 target의 기존 무상한 분기와 fallback 동작을 그대로 보존한다. |
| `measurement_manifest_policy.py` | 고정된 측정 셀·반복 슬롯의 완료/미완료 판정을 분리한다. 재개 과정이 선택된 코호트 밖으로 넓어지지 않는다. |
| `monthly_publication_facts.py` | 최초 발행 사실, 현재 공개 상태, 원 계약 월, 보고서 생성 시점의 관측 범위를 구분한다. 철회·재발행으로 최초 이행 사실을 잃지 않는다. |

기존 `_generate_with_auto_review`와 `_build_measurement_specs`는 호환 adapter로 유지한다.
옮긴 35개 helper의 기존 Worker import 이름도 alias로 유지한다. 별도 구현 복제나 런타임
전역 변수 복사 방식은 사용하지 않는다. 서비스의 공개 함수는 역할을 드러내는 이름을 쓴다.

Worker는 10,966줄에서 10,197줄로 줄었다. 남겨진 함수 185개의 AST는
기준선과 동일하며, 분리한 helper 33개의 본문은 이름 변경을 정규화하면 동일하다.
스냅샷 소비 함수 2개는 공유 parser로 변경했으므로 별도 회귀 테스트로 검증했다.
태스크 27개의 decorator·인자 계약은 변경 전후 동일하다.

## 검증 결과

| 검사 | 최종 결과 |
|---|---:|
| 수정 전 Backend 기준선 | 4,103 passed / 0 failed / 0 skipped |
| 수정 후 Backend 전체 | 4,197 passed / 0 failed / 0 skipped |
| 추가한 직접 서비스·경계 회귀 테스트 | 94개 |
| Backend line coverage | 84.85% (기존 gate 60% 통과) |
| Site 테스트 | 332 passed / 0 skipped |
| Admin 테스트 | 627 passed / 0 skipped |
| 배포·인프라 정책 스크립트 테스트 | 85 passed / 0 skipped |
| Backend Ruff / Site·Admin lint·typecheck | 모두 통과 |
| Site·Admin production build | 모두 통과 |
| 사용자 문구 / DB 연결 예산 / diff 검사 | 모두 통과 |

위 전체 검사 집합의 테스트 합계는 **5,241개**다. 선별 실행 377개는 위 테스트와 중복이므로
더하지 않았다. 재전달 PostgreSQL 21개도 실제 실행했으며 skip으로 대체하지 않았다.
실제 PDF 렌더 검사는 `REQUIRE_PDF_RENDER=1`로 강제했다.
기존 Alembic path separator와 ORM FK 순환 정렬 warning 2개는 이번 변경 전후 동일하다.

독립 테스트 환경에 PostgreSQL 16·Redis를 만들고 `.env` 로딩을 끈 상태에서 검증했다.
검증 프로세스는 외부 네트워크 연결을 막고 loopback만 사용했으며 공급자 키는 테스트 값이다.
프런트엔드는 dotenv 파일을 제외한 복사본에서 빌드·검사했다. Admin의 Backend 오류 코드
계약 검사를 위해 현재 Backend 소스도 함께 복사했다.
검증 초기의 DB 미준비·보조 DB URL·복사본 입력 누락은 코드 실패와 구분했고,
표의 결과는 이를 바로잡은 최종 실행이다. 테스트나 assertion을 삭제·완화하지 않았다.

실행 명령과 상세 로그는 `/private/tmp/reputation-refactor-20260916-2oz6gi9v/verification`에 있다. 이 경로는 해당 Mac의 임시 검증 자료이며
정본 결과와 소스 해시는 위 JSON이다. 테스트용 서비스는 완료 후 종료한다.
검증에는 다음 명령을 사용했다(DSN·loopback guard는 검증 launcher가 주입):

```bash
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib backend/.venv/bin/python \
  /private/tmp/reputation-refactor-20260916-2oz6gi9v/verification/run_backend.py \
  --cov=app --cov-fail-under=60

backend/.venv/bin/python -m ruff check backend
make copy-guard db-budget-guard
# scripts 테스트: 같은 launcher에 ../scripts 전달
# Site/Admin: 격리 복사본에서 npm run test, lint, typecheck, build
```

## 보존 및 미실행 범위

`aside-test.js`의 내용은 SHA-256으로 불변을 확인했고 커밋에 포함하지 않았다.
기존 `.env`, 의존성 lock, DB 모델·migration, API router, frontend 소스, 배포 설정은 수정하지 않았다.
운영 DB·고객 원고·실제 Slack·유료 AI 호출을 수동 실행하지 않았다.

이는 운영 전체 E2E나 새 Docker 이미지 검증·운영 배포 완료를 의미하지 않는다.
인증 후 브라우저 E2E를 이번 리팩터링에서 다시 수행하지 않았고 원격 CI도 실행하지 않았다.
로컬 Redis 8.10.1·Node 26.3.0은 CI의 Redis 7·Node 24와 달라 최종 배포 전 CI 재확인이 필요하다.
Homebrew 테스트 의존성은 설치했지만 로그인 시 자동 실행되는 서비스로 등록하지 않았다.

## 남은 구조 개선

전체 코드베이스가 완전히 분해된 상태는 아니다. `tasks.py`에는 여전히 큰 V0 실행·SoV 실행·
월간 보고서 orchestration이 남아 있다. 후속 분리는 이번에 확보한 서비스 경계와 테스트를
사용하되 OperationRun·lease·CAS·outbox 트랜잭션 소유권을 옮기는 작업과 섞지 않는다.
대형 Admin API·보고서 view·콘텐츠 UI 역시 이번 변경 범위 밖이다.

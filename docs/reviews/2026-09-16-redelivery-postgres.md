# 재전달 결함 수정 및 실제 PostgreSQL 검증

기준: 3da76bd. 작업 브랜치: codex/geo-autonomy-hardening-20260915.
사용자 명시적 허용에 따라 재전달 코드와 격리된 PostgreSQL 검증을 수행했다.
운영 DB, 고객 데이터, 실제 공급자, 실제 Slack, Cloud Run 배포는 이번 검증 대상이 아니다.

## 결론

이전 G01 재전달 결함은 수정됐으며 기존 실패 테스트가 그대로 통과한다.
중복 제외 선택 테스트 720개 통과, 실패·오류·skip 0개다.
그중 실제 PostgreSQL 세션을 사용하는 검증은 38개(신규 21, 기존 outbox 17)다.
전체 저장소 전수 테스트나 마이그레이션 검증까지 통과한 것으로 해석하지 않는다.

## 수정

backend/app/workers/generation_execution_claim.py:
기존 OperationRun.request_payload에 generation_execution을 기록한다.
예약 token, 현재 실행 token, claim version, worker ID, 마지막 사람 편집 시각을 함께 저장한다.
이 기록과 ContentItem의 실행 token·시각은 같은 transaction으로 commit된다.
DB의 현재 run 상태·worker·version·lease·병원·콘텐츠 범위 검사는 그대로 유지한다.
첫 실행은 기존 예약을 소유해야 하며, 재전달은 더 높은 유효 claim version과 저장된 소유권 관계를 모두 만족해야 한다.
매 인수마다 실행 token을 다시 회전하므로 옛 실행의 결과 저장과 claim 해제가 새 실행에 영향을 주지 않는다.
같은 version 중복, 다른 worker·예약·실행 token, 취소·발행·일시정지, 이후 사람 편집은 인수할 수 없다.
저장된 본문·이미지 및 생성 시도 예산을 삭제하거나 초기화하지 않는다. 신규 컬럼·마이그레이션은 추가하지 않았다.

실제 task의 설명을 새 계약에 맞췄고, 기존 실패 재현 테스트의 assert는 완화하지 않았다.

## 실제 DB에서 확인한 것

- 독립 세션 두 개가 동시에 같은 실행을 시작해도 하나만 성공한다.
- 실제 Celery OperationRun claim 함수를 통해 더 높은 version을 얻은 재전달은 정상 인수한다.
- 테스트용 자식 프로세스를 token commit 직후 os._exit로 종료한 뒤 다른 세션에서 복구한다.
- 이전 실행의 늦은 본문 UPDATE·claim 해제·완료 기록은 새 실행을 변경하지 못한다.
- 세 차례 연속 재전달에서도 원래 예약 관계를 유지하고 같은 version 중복은 거절한다.
- 저장된 provider 결과 본문은 인수 과정에서 그대로 유지된다.
- 사람 편집·취소·발행·일시정지·다른 예약 및 잘못된 소유권 기록은 거절된다.
- commit 전에 실패하면 token과 소유권 기록이 함께 rollback된다.
- 근거 철회와 공개 반영 intent가 함께 commit/rollback되며 최초 발행 시각은 보존된다.
- 병원 advisory lock은 실제 별도 transaction을 차단하고 commit 후 해제된다.
- 기존 outbox의 동시 claim, 중복 억제, 만료 lease, retry/HOLD 처리 관련 17개 DB 테스트가 통과한다.

외부 공급자가 응답했지만 DB에 저장되기 전에 연결이 끊긴 경우까지 API 과금 exactly-once를 보장하지 않는다.
이번 검증은 DB 소유권·저장 결과 보존·중복 저장 차단의 증거다. 실제 Celery broker 전체를 기동한 E2E는 아니다.

## 테스트 결과

expanded-postgres: 104 통과. 신규 PG 21 + outbox 35(17 DB/18 비DB) + history 15 + autonomy 33.
regressions: 664 통과. 앞 suite와 48개가 중복이므로 고유 합계는 720이다.
Ruff, 운영 문구 가드, DB 연결 예산 가드, git diff --check 통과.
Frontend runtime은 변경하지 않았고 이번에는 브라우저 E2E나 Frontend 전체 suite를 재실행하지 않았다.

DB는 /tmp/reputation-redelivery-qos2aoem/pgdata, 127.0.0.1:55481의 일회성 PostgreSQL이다.
테스트 runner는 환경변수를 정리하고 dotenv를 차단하며 Python socket과 native psycopg2 연결을 전용 DB로 제한한다.

## 미완료 범위와 초기 탐색 실행

Alembic upgrade 명령은 사용자 허용 이후에도 도구 검사에서 차단되어 실행하지 못했다.
따라서 테스트 테이블은 ORM metadata로 생성했다. 마이그레이션이 추가하는 server default·trigger·기존 데이터 전환의 검증은 아니다.
초기 runner가 junit 옵션만 받은 경우 기본 파일 선택을 하지 않아 전체 suite를 탐색 실행했다.
그 결과 3,609 통과 / 44 실패 / 18 오류 / 406 skip이었다. 선택 파일 판정은 이후 수정했다.
실패 중 raw SQL fixture가 기대하는 Hospital server default와 ORM-only 테이블의 차이, 준비되지 않은 별도 DB fixture 주소 등을 확인했다.
전체 실패를 모두 제품 결함이 아니라고 단정하거나 전부 해결했다고 처리하지 않았다. 최종 PASS는 명시한 720개에 한정한다.

출시 전체 판정은 별도다. 기존 마이그레이션 기반 통합 시험, stage의 broker·API·공개 사이트·Slack E2E, 이전에 남긴 IAM 전환 확인은 완료되지 않았다.
이전 문서의 G01 항목은 본 기록으로 해결됐지만, 과거 실패 기록과 미완료 범위는 감사 이력으로 보존한다.

## 재현

임시 PostgreSQL 서버가 해당 포트에 실행되고 ORM 테스트 스키마가 준비된 상태에서:

```sh
cd /Users/woojinlee/projects/Reputation-geo-hardening
backend/.venv/bin/python scripts/verify_redelivery_postgres.py --port 55481 \
  tests/test_generation_redelivery_postgres.py tests/test_notification_outbox.py \
  tests/test_history_consistency.py tests/test_geo_autonomy_hardening.py
```

빈 전용 DB에 최초 테이블을 만들 때만 --prepare-schema를 사용한다. 운영 DSN이나 운영 스키마에는 사용하지 않는다.
원시 로그·JUnit은 /tmp/reputation-redelivery-qos2aoem에 보존했다. 운영 배포나 GitHub push는 수행하지 않았다.

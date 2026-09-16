# 리팩터링 배포 후보 재검토 — 2026-09-16

소스 기준: `bb87d921fe83b186e2fc0744451461be90d59d5a`.
판정 시점: **로컬 재검토 통과, push 후 PR CI 확인 예정**. 운영 배포 완료 기록이 아니다.
이 후속 커밋은 검증·CI 보강이며 앞선 리팩터링의 runtime 코드를 변경하지 않는다.
검사 수치와 증거 hash는 [검증 JSON](2026-09-16-refactor-deployment-review-validation.json)을 본다.

## 재검토 결과

최신 origin/main 위에 충돌 없이 적용되며, 원래 Worker 태스크 27개의 decorator·인자 계약은
동일하다. DB 모델·migration·설정·큐 라우팅·Beat 일정·dispatch 서명·의존성 lock·Dockerfile·
Admin/Site 코드는 main과 같다. 따라서 새 스키마 변경이나 신규 secret/IAM 설정을 필요로
하지 않는다. 기존 API/Worker와 롤링 공존할 때의 메시지 계약도 유지한다.

Backend 전체를 실제 격리 PostgreSQL·Redis에서 다시 실행해 **4,197 passed / 0 failed /
0 skipped**, coverage **84.85%**를 확인했다. 재전달 21개와 실제 PDF 렌더 검사를 포함한다.
기존 Alembic path separator·ORM FK 순환 정렬 경고 2개는 그대로이며 새 오류는 없다.
배포·정책 스크립트 테스트는 추가 검사 포함 **94 passed / 0 skipped**다.
Backend Ruff, 문구 검사, DB 연결 예산 검사, YAML parsing 및 diff 검사도 통과했다.

## 이번에 보강한 배포 gate

1. 기존 CI에는 `REDELIVERY_TEST_SYNC_DATABASE_URL`이 없어 소유권·중복 전달 테스트 21개가
   skip됐다. 전용 PostgreSQL 서비스(`reputation_redelivery_test`, loopback 55432)를 추가하고
   테스트 전에 해당 DB를 Alembic head로 올린다. 테스트의 격리 요구를 완화하지 않는다.
2. Docker job은 이미지 빌드만 확인했다. 최종 non-root 이미지에서 새 서비스 6개를 직접
   import한 뒤 필수 Celery task/route/Beat 선언, migration bundle, API liveness, 개발용 docs
   비노출, WeasyPrint native import를 검사한다. `--network none`, 테스트 설정, 읽기 전용
   스크립트 mount만 사용하며 DB·공급자·Slack·broker에 연결하지 않는다.
3. gate의 환경 거부·실패 exit·import 무부수효과·CI 연결을 보호하는 회귀 테스트 9개를 추가했다.

## 승인 경계

소스의 로컬 검토에서 확인된 배포 차단 결함은 없다. 커밋·push 후 Draft PR을 통해 원격
전체 CI를 확인하며, PR 병합이나 운영 배포는 별도 실행이다. CI 최종 결과는 해당 PR의
최신 head checks가 정본이다. 이 문서는 CI 시작 전 시점의 기록이다.

로컬 Docker daemon이 응답하지 않아 이미지 검사를 실제 실행했다고 주장하지 않는다.
Node 24 / Redis 7 및 Linux 최종 이미지 검증은 원격 CI로 확인한다. Cloud Run 현황에 대한
읽기 전용 조회는 도구 단계에서 차단되어 실행되지 않았으므로, 현재 운영 리비전의 건강을
이번 검토에서 새로 확인했다고 주장하지 않는다.

배포 시에는 기존 runbook대로 운영 설정·secret 참조를 보존하고 Backend 계열의 Worker →
RedBeat 재조정 → Beat → readiness → API 순서를 지킨다. 스키마 변경은 없지만 배포 이미지
및 실제 DB head 일치와 새 release의 7개 큐 canary는 rollout 시점에 다시 확인해야 한다.
이번 diff에 없는 병원 원고 재생성, 일회성 backfill, DB downgrade, IAM 회수는 하지 않는다.
기존 frontend 소스는 그대로이므로 불필요한 변경과 환경 재주입을 피한다.

대형 Worker 잔여 구조와 기존 경고는 후속 개선 대상이며 이번 diff의 기능 회귀로 판정하지
않는다. `aside-test.js`와 dotenv 파일은 그대로 보존하고 push 대상에 포함하지 않는다.

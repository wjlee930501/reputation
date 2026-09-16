# GEO 자율 운영 — 2026-09-16 운영 배포 완료

판정: **DEPLOYED_VERIFIED**. 사용자 명시적 병합·운영 배포 승인에 따라 실행했다.
배포 소스: `012aeeb68e1965ea2194300989140ab9b8c4f155` (PR #114 merge commit).
프로젝트/리전: `mso-platform-481505` / `asia-northeast3`.
서비스 전환: 2026-09-16 12:54:51~13:01:11 KST. 사후 오류 조회: 13:08 KST.
이 문서가 추가되는 후속 커밋은 문서·증거 커밋이며 배포 이미지의 소스 SHA가 아니다.

## 병합과 검증

검증 후보 `93d5408`에 최신 main의 서울 날짜 기준 fixture와 도입문의 전달 문서를 보존했다.
Slack 문서의 충돌은 일일 정상 요약과 도입문의 전달 경계를 모두 유지해 해결했다.
PR CI와 merge commit의 main CI가 각각 9/9 성공한 뒤 서비스 교체를 시작했다.
PR: https://github.com/wjlee930501/reputation/pull/114
Main CI: https://github.com/wjlee930501/reputation/actions/runs/35053204403

원격 CI에서 발견한 배포 도구의 실제 결함도 병합 전에 고쳤다:
- Terraform 속성 정렬 (`d71c70c`): 선언 의미 변경 없음.
- offline pytest launcher의 import 부수효과와 env 템플릿 누락 (`6a99a28`).
  launcher를 import해도 cwd/env/socket을 변경하지 않는 회귀 테스트를 추가했다.
  `FLEET_HEARTBEAT_HOUR_KST=18`을 두 env 예제에 선언했다. 정책 테스트 85개 통과.

Main Backend CI는 4,082 passed / 21 skipped / 2 warnings다. 21개는 별도 DSN을 요구하는
재전달 PostgreSQL 파일이며, 이전 격리 전수 실행의 4,103 passed / 0 skipped에 포함됐다.
원격 CI가 skip 없이 전부 수행했다고 해석하지 않는다. 배포 runtime 소스는 검증 후보와 같다.

## 실제 배포 순서와 범위

기존 서비스 5개/Job 3개의 정의·환경·secret 참조·리비전을 먼저 보관했다.
운영 migration Job의 실행 인자만 일시 변경해 read-only transaction으로 스키마를 확인했다.
이미 `0079_topic_swap_fallback`이었고, 정식 migration 실행도 같은 head에서 성공했다.
새 migration/backfill이나 고객 원고 수정은 없었다. Cloud SQL 백업/PITR 설정과 최근 성공
백업, 두 GCS 버킷, DNS, 17개 참조 secret의 ENABLED 메타데이터를 확인했다.

`deploy.sh`와 runbook의 단계 순서를 따르되 환경 전체를 덮어쓰지 않도록 incremental
`gcloud run services update` / `jobs update`로 기존 설정을 보존했다. 모든 명령은
명시적 project/region을 사용했다. 원본 폴더의 오래된 dotenv를 운영에 재주입하지 않았다.

운영 도메인으로 새 Linux/amd64 이미지 3개를 빌드·push하고 registry digest를 고정했다.
Migration → Worker → RedBeat 재조정 → Beat → readiness → API → Site → Admin 순서다.
Migration `reputation-migrate-lbfff`, 재조정 `reputation-redbeat-reconcile-h74km`,
readiness `reputation-production-readiness-z6p2t`의 task가 각각 1개씩 성공했다.

| 서비스 | 실제 Ready 리비전 | 트래픽 |
|---|---|---:|
| API | `reputation-api-geo-125451-012aeeb` | 100% |
| Worker | `reputation-worker-geo-125451-012aeeb` | 100% |
| Beat | `reputation-beat-geo-125451-012aeeb` | 100% |
| Site | `reputation-site-geo-125451-012aeeb` | 100% |
| Admin | `reputation-admin-geo-125451-012aeeb` | 100% |

제출한 OCI index digest와 Cloud Run이 해석한 linux/amd64 child digest는 서로 다르다.
Registry manifest의 부모-자식 관계를 확인했으며 두 값을 evidence에 분리해 기록했다.

## 사후 실행 증거

Readiness는 모든 check가 true였다. 실제 schema head와 DB/Redis 연결, 등록 task/route,
필수 스케줄, 공급자/보고서/운영 설정, 현재 릴리스 7개 큐 canary를 확인했다.
12:58 무렵의 일회성 진단 Job은 canary가 아직 구버전이라 실패했고, 본 배포 gate는
그동안 계속 대기했다. 13:00 정상 Beat tick이 새 Worker에서 canary를 실행한 뒤 gate가
통과했다. 이 중간 진단 실패를 지우거나 전체 배포 실패로 합산하지 않는다.
`null_noise_hash_approvals=7`은 관측값으로 유지했고, 재인증 후보는 0건이었다.

공개 GET 및 실제 Chrome 사후 검증:
- 병원 9곳의 정확한 ID·canonical host·새 Site revision 일치.
- 공개 글 128개 ID 집합이 배포 전과 동일. 128개 실제 HTML HTTP 200과 canonical 일치.
- 병원별 홈/콘텐츠/사이트맵/robots/llms HTTP 200. 모든 공개 글의 sitemap 포함.
- 대표 이미지 9개의 실제 GCS 응답 바이트 SHA-256이 인증된 URL 버전과 일치.
- Admin 로그인 폼 desktop/mobile 정상, 미인증 API 401, 브라우저 오류와 HTTP 5xx 없음.

로그 조회 시각 13:08 KST 기준, 배포 시작 이후 새 서비스 리비전 5개의 ERROR 이상은 0건.
이는 명시된 구간의 관측이며 이후 무장애를 보장하지 않는다. 로그 수집 지연도 가능하다.
Production 로그인은 화면/익명 경계만 검증했다. 실제 운영자 계정으로 고객 데이터를
변경하지 않았다. 인증 후 보고서 전달 전체 경로는 앞선 격리 production-image E2E 증거다.

공급자 실호출·고객 원고 재생성·보고서 전달·테스트 Slack을 수동으로 실행하지 않았다.
배포된 Worker/Beat는 실제 정상 운영을 계속한다. 일일 요약은 18시 KST부터 대상이며
오늘 첫 정상 요약·23시 야간 생성의 미래 결과까지 완료했다고 주장하지 않는다.

## IAM·설정 보존·롤백

Site/Admin 전용 SA를 Terraform 선언대로 생성하고 logging/monitoring 권한과 서비스별
필수 secretAccessor만 추가했다. 실제 새 frontend revision은 각각 전용 SA로 실행된다.
기존 frontend SA의 grants는 롤백 창을 위해 유지했다. API의 기존 public-invoker/LB 경계는
변경하지 않았다. secret 값·버전은 교체하지 않았다.

명시된 변경은 source release env, backend 일일 요약 시각 18, frontend SA뿐이다.
나머지 env/secret 참조, memory/CPU, probes, command/args, concurrency, scaling, VPC 및
Cloud SQL 설정의 보존을 실제 배포 후 정의와 비교했다.

| 서비스 | 롤백 리비전 |
|---|---|
| API | `reputation-api-00175-x8k` |
| Worker | `reputation-worker-00163-njr` |
| Beat | `reputation-beat-00159-zrg` |
| Site | `reputation-site-00123-27t` |
| Admin | `reputation-admin-00084-xh9` |

좌표 파일: `/private/tmp/reputation-deploy-20260916-5pz78kls/.deploy-rollback`.
문제 발생 시 runbook에 따라 이 파일을 `DEPLOY_ROLLBACK_STATE_FILE`로 지정해 traffic을
복귀한다. Worker/Beat를 롤백하면 영속 RedBeat 스케줄도 구 이미지와 재조정해야 한다.
DB downgrade는 자동 실행하지 않는다. 이번 배포의 전후 schema head는 동일하다.

원본 Reputation은 HEAD·Git 상태·diff·미커밋 110개 파일 해시 모두 불변이었다.
릴리스는 별도 detached worktree에서 진행했고, 그 worktree의 후속 문서 branch에 이 기록을
남겼다. 실제 운영 서비스는 중지하지 않았다. 비밀값 없는 선택 증거와 각 SHA-256은
같은 이름의 `-validation.json` 및 `evidence/2026-09-16-geo-autonomy-production/`에 보존했다.

배포 후 readiness를 다시 실행한 `reputation-production-readiness-mz7jk`도 성공했다.
7개 secret 권한을 재조회해 새 SA 접근 권한과 이전 grants 보존을 다시 확인했다.

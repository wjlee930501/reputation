# 동작 보존 리팩터링 — 2026-09-16 운영 배포 완료

판정: **DEPLOYED_VERIFIED**. 사용자의 명시적 main 병합·운영 배포 승인으로 실행했다.
배포 소스: `18531d3452287afa012e28ec256d920708c90a57` (PR #116 merge commit).
Project/region: `mso-platform-481505` / `asia-northeast3`.
서비스 전환: 2026-09-16 15:16~15:21 KST. 문서 후속 커밋은 런타임 소스 SHA가 아니다.

## 병합·이미지·실제 전환

PR 후보 `934c024`의 CI 9/9와 병합 커밋 main CI 9/9를 각각 확인했다.
Main CI: https://github.com/wjlee930501/reputation/actions/runs/35062402033
검토한 후보와 병합 커밋의 소스 tree는 동일했다. Mac mini 작업 폴더는 main으로
fast-forward했고 기존 `aside-test.js`는 수정·커밋하지 않았다.

Mac mini의 GCP 재인증이 만료되어 인증이 유효한 MacBook Air에서 별도 detached worktree로
배포했다. 기존 Air 작업 폴더의 HEAD·Git 상태·diff와 미커밋 110개 파일 SHA-256은 불변이다.
기존 dotenv를 빌드나 운영 설정에 주입하지 않았다.

배포 전 서비스 5개·Job 3개 정의와 롤백 리비전을 보관했다. 실제 현재 readiness와 DB/Redis,
7개 큐 canary, Cloud SQL PostgreSQL 16의 백업·PITR 및 성공 백업, GCS 두 버킷을 확인했다.
Linux/amd64 이미지 3개를 병합 소스로 빌드·push하고 OCI digest를 고정했다. 최종 non-root
Backend 이미지에서 외부 네트워크를 끈 import·ASGI liveness·migration bundle·native PDF
의존성 smoke도 통과했다. Registry index와 Cloud Run의 amd64 child digest를 구분해 확인했다.

배포는 기존 runbook 순서로 migration → Worker → RedBeat 재조정 → Beat → 새 release
readiness → API → Site → Admin이다. 환경·secret·기존 서비스 계정·자원·스케일·VPC·Cloud SQL
연결·probe·command를 보존하는 incremental update를 사용했다. 변경은 이미지와
`REPUTATION_RELEASE_REVISION`뿐이다. 새 migration, backfill, IAM 또는 secret 변경은 없다.

| 서비스 | Ready 리비전 | 트래픽 |
|---|---|---:|
| API | reputation-api-ref-151613-18531d3 | 100% |
| Worker | reputation-worker-ref-151613-18531d3 | 100% |
| Beat | reputation-beat-ref-151613-18531d3 | 100% |
| Site | reputation-site-ref-151613-18531d3 | 100% |
| Admin | reputation-admin-ref-151613-18531d3 | 100% |

## 사후 검증

Migration `reputation-migrate-wmt9g`, RedBeat `reputation-redbeat-reconcile-vwtjr`, rollout readiness
`reputation-production-readiness-7gsv9`는 각 1 task 성공이다. 새 canary tick까지 readiness가
기다린 뒤 API 전환을 허용했다. 모든 서비스 전환 후 `reputation-production-readiness-q28vb`도
성공했고 모든 check가 true였다. DB head는 전후 동일한 `0079_topic_swap_fallback`이다.

병원 9곳의 ID·canonical host·새 Site revision 일치, 공개 글 128개 ID 집합 보존과 각 HTML
HTTP 200·canonical, 홈/콘텐츠/sitemap/robots/llms 및 sitemap의 모든 글 포함을 확인했다.
대표 이미지 9개의 실제 응답 바이트 SHA-256이 인증된 URL 버전과 일치했다.

실제 Chrome에서 Admin 로그인 desktop/mobile을 확인했다. 폼·버튼 노출, mobile 가로 넘침 없음,
브라우저 예외·HTTP 5xx 없음, 미인증 Admin API 401이다. 인증 후 고객 데이터 변경이나
보고서 전달을 실행한 검증이 아니다. 실제 유료 AI 호출·수동 테스트 Slack·원고 재생성도 하지 않았다.

15:21 KST 조회 기준, 배포 시작 이후 새 리비전 5개의 ERROR 이상 로그는 0건이다.
이 수치는 해당 관측 구간만 의미하며 로그 수집 지연·이후 장애 가능성을 배제하지 않는다.

## 롤백·증거

5개 서비스 모두 이전 suffix `geo-125451-012aeeb` 리비전을 유지했다. 롤백 상태 파일:
`/private/tmp/reputation-refactor-deploy-20260916-yv7a6u2l/.deploy-rollback`.
`DEPLOY_ROLLBACK_STATE_FILE`로 지정해 runbook의 traffic rollback을 사용한다. Worker/Beat를
복귀하면 해당 이미지와 RedBeat 재조정이 필요하다. DB downgrade를 자동 실행하지 않는다.

선택 증거·각 SHA-256·실제 이미지 index/child digest와 조회 시각은
[검증 JSON](2026-09-16-refactor-production-validation.json)에 있다. 원본 private 스냅샷·로그는
배포 작업 폴더에 남기고 비밀값·고객 비공개 payload는 저장소에 커밋하지 않았다.

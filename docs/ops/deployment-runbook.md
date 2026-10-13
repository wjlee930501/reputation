# 현재 배포와 헬스체크

## 최신 운영 배포 — 2026-10-13 Phase 0 자율성 보완

main `31751a88`(api `00241-lhd`, worker `00231-4rx`, beat `00226-gpw`, admin `00105-x4r`, Site 그대로, DB head
`0087_add_operation_run_redispatch_count`). 본 체크아웃의 untracked 파일이 release revision 검사에 걸리므로 깨끗한 worktree에서
`deploy.sh backend`를 실행했고, `deploy.sh admin`은 `DEPLOY_ROLLBACK_STATE_FILE`로 별도 롤백 파일을 써 backend 좌표를 덮지 않았다.
readiness 17/17, 병원 11곳 헬스 200, 새 리비전 ERROR 0건, `daily-fleet-heartbeat` 새 워커에서 `before_summary_window`. 35일 RUNNING
잔재 1건이 `OPERATION_RUN_ABANDONED`로 종료되며 예상한 사고·Slack 1건. 배포 뒤 `ops_sql_report`는 migrate Job으로 쓸 수 있다.
**이후 Worker를 `00223` 이하로 롤백하지 않는다.** 상세는 [배포 기록](../releases/2026-10-13-autonomy-phase0-production.md).

## 이전 운영 배포 — 2026-10-10 반복 알림 근본 수정

main `2cc60247`(api `00240-c9n`, worker `00230-s6m`, beat `00225-npd`, admin `00104-gkq`, DB 0086 그대로). Backend 배포의
legacy publish 사전 확인은 로컬 `DATABASE_URL` 없이 VPC 안 migrate Job으로 돈다. **배포 전 서비스 트래픽이 최신을 따라가는지
확인한다** — 특정 리비전에 고정돼 있으면 `gcloud run deploy`가 새 리비전을 바로 은퇴시키고 옛 코드가 남는다(이번 API).
`deploy.sh`는 이제 고정을 배포 전에 막고(`update-traffic --to-latest` 안내), 배포 뒤 트래픽을 받는 리비전이 최신 Ready인지 확인한다.
상세는 [배포 기록](../releases/2026-10-10-alert-noise-production.md).

## 이전 운영 배포 — 2026-10-08 근본 원인 수정

main `c5ada5a1`을 API·Worker·Beat에 배포했다(api `00235-db4`, worker `00224-xnt`, beat `00219-dfh`, DB head
`0083_add_operation_run_not_before`). 절차: `.env.production` 대조 → `gcloud secrets versions disable 1 --secret=SLACK_WEBHOOK_URL_DEV`
→ `deploy.sh backend` → readiness → `run_beat_entry daily-fleet-heartbeat`(18시 전이면 `before_summary_window`) → V0 재실행 →
`python -m app.utils.notification_channel_cleanup` dry-run → `--confirm`. 롤백은 비활성화한 시크릿 버전을 먼저 enable한다.
상세는 [근본 원인 수정 배포 기록](../releases/2026-10-08-root-cause-fixes-production.md).

## 이전 운영 배포 — 2026-10-06 원장 보고서 마케팅 검토 반영

main `dc4e3803`(#211)·`c2adafa5`(#212)·`61725afd`(#213)을 차례로 API·Worker·Beat에 배포했다. 현재 리비전은
api `00228-wr6`, worker `00217-nfj`, beat `00212-fbx`, site `00143-mvd`, admin `00100-nvl`, DB head는
`0082_add_content_reference_checks` 그대로다. 9월 보고서 9곳 템플릿 갱신과 운영 질문·글 제목 수정은
[배포 기록](../releases/2026-10-06-report-marketing-feedback-production.md)을 본다. `deploy.sh`는 작업 PC의
Docker로 이미지를 빌드한다 — Docker Desktop이 응답하지 않으면 빌드 단계에서 멈추므로 `docker info`로 먼저 확인한다.

## 예약 작업 즉시 실행

배포로 고친 예약 작업(Beat 항목)은 다음 예약 시각을 기다리지 않고 바로 한 번 돌릴 수 있다. Beat와 같은
작업·큐·서명 헤더로 큐에 넣기만 하므로 실행 경로·서명 검증·작업의 상한과 비용 가드는 예약 실행과 같다.

```bash
gcloud run jobs execute reputation-migrate --project mso-platform-481505 --region asia-northeast3 --wait \
  --args=python,-m,app.utils.run_beat_entry,post-publish-ai-review
```

항목 이름은 `backend/app/core/celery_app.py`의 `beat_schedule` 키다. 결과는 Worker 로그에서 반환 task id로 확인한다.

## 운영 DB 읽기 전용 조회

운영 DB는 사설 IP라 작업 PC에서 직접 붙지 않는다. 자율 운영 지표(Q1~Q6)와 배포 전 확인(Q7, 쿼리 원문은 `backend/app/utils/ops_sql_report.py`)은
VPC 안의 migrate Job으로 돌린다. CLI는 첫 문장으로 `SET TRANSACTION READ ONLY`를 실행하고 끝나면 롤백하며, 쿼리마다 JSON 한 줄을
Job 로그에 남긴다. gcloud `--args`는 쉼표로 인자를 나누므로 `--only`는 쿼리마다 따로 넘긴다(`--only=q1,q3`은 한 토큰이 아니다).

```bash
gcloud run jobs execute reputation-migrate --project mso-platform-481505 --region asia-northeast3 --wait \
  --args=python,-m,app.utils.ops_sql_report,--only=q1,--only=q3
```

## 이전 운영 배포 — 2026-10-01 미결 PR 정리·원장 보고서 개편

main `a8a86b57`을 5개 서비스에, #194·#196·#197(최종 `ce731db7`)을 API·Worker·Beat에 배포했다. 현재 리비전은
api `00215-mtl`, worker `00204-q9m`, beat `00199-gq7`, site `00143-mvd`, admin `00099-fc4`,
DB head는 `0082_add_content_reference_checks`.
`deploy.sh`는 `PUBLIC_DOMAIN=reputation.motionlabs.kr ADMIN_DOMAIN=admin.reputation.motionlabs.kr` 셸 변수가 필요하다.
9월 보고서 9곳 템플릿 갱신 결과는 [배포 기록](../releases/2026-10-01-report-plain-language-production.md)을 본다.
`reputation-migrate` Job의 기본 task timeout은 300초라 여러 병원의 `execute`는 `--task-timeout=1800s`로 돌리고,
`--args` 안에서 같은 플래그를 반복할 수 없으므로 병원은 `--hospital-id=ID` 꼴로 넘긴다.

## 이전 운영 배포 — 2026-09-28 도입문의 Slack 채널 분리

main `7266674`(PR #164·#165)를 API·Worker·Beat에 배포했다. 현재 리비전은 api `00206-8hf`,
worker `00195-7dt`, beat `00190-bqz`이고 Site·Admin과 DB head `0080_lead_diagnosis_supersede`는 그대로다.
공개 도입문의 접수 알림은 선택 secret `SLACK_WEBHOOK_URL_INQUIRY`(`#noti-도입문의-뉴비짓`)로 가며,
비어 있으면 `SLACK_WEBHOOK_URL`로 간다. Aside로 실제 문의 2건을 제출해 새 채널 수신을 확인했다.
상세는 [도입문의 Slack 채널 분리 배포 기록](../releases/2026-09-28-inquiry-slack-channel-production.md)을 본다.

## 최신 운영 배포 — 2026-09-23~24 통합 정합성 보완·파비콘

main `38cff0e`(PR #153 런타임, #154 문서)를 5개 서비스에 배포했고, 이어 Site만 두 번
(`a649cc1` PR #155 플랫폼 파비콘, `fad966d` PR #156 병원 모노그램 파비콘) 배포했다.
현재 리비전은 api `00201-g5w`, worker `00190-f9b`, beat `00186-8sg`, site `00142-xh2`,
admin `00096-rrh`이고 DB head는 `0080_lead_diagnosis_supersede` 그대로다(migration 없음).
readiness Job, 공개 9개 병원 헬스 200·새 site 리비전, 새 리비전 오류 0건을 확인했다.

**배포 전 `.env.production`을 운영 env와 대조한다.** `deploy.sh`는 서비스 env를 파일 내용으로
통째로 바꾼다. 이번에는 작업 PC 파일이 오래돼 운영 API 리비전의 평문 env로 다시 만들었고,
배포 후 env가 `REPUTATION_RELEASE_REVISION`만 바뀐 것을 확인했다. 배포 전 실제 운영은 준비
문서의 `300a663`이 아니라 `62caa56`이었다(기록 없는 배포 2건). 리비전·검증·롤백 좌표는
[통합 정합성 보완 배포 기록](../releases/2026-09-23-integration-hardening-production.md)을 따른다.

## 최신 운영 배포 — 2026-09-22 노출 진단 수동 생성

PR #143 기능 코드를 담은 main tip `300a6636aee6d5fb73141848f34189194a86c9e5`를 5개 서비스에 배포했다.
현재 리비전은 api `00198-r2k`, worker `00187-wsn`, beat `00183-ks8`, site `00134-x65`, admin `00092-zln`이다.
**DB head가 `0079_topic_swap_fallback`에서 `0080_lead_diagnosis_supersede`로 올라갔다**(additive only).
PR CI 10/10과 readiness 7개 큐를 통과했고, 공개 9개 병원 헬스 200·새 리비전 오류 0건·
새 admin API 401 라우팅·랜딩 회귀 없음을 사후 확인했다. 새 화면을 로그인 상태로 눌러본
검증과 교정 후 새 질의로 측정이 도는지는 남아 있다. 실제 digest·롤백 좌표는
[노출 진단 수동 생성 배포 기록](../releases/2026-09-22-manual-diagnosis-production.md)을 따른다.

## 최신 운영 배포 — 2026-09-22 도입문의 CTA 전환

PR #141의 main 소스 `8f40f49590345ccd333ff85d786d9fcfe41848f2`를 5개 서비스에 배포했다.
현재 리비전은 api `00197-8vm`, worker `00186-6f9`, beat `00182-t6q`, site `00133-j7l`,
admin `00091-wbc`이고 DB head는 `0079_topic_swap_fallback`(신규 schema 변경 없음)이다.
PR CI 10/10과 readiness 7개 큐 canary를 통과했고, 공개 9개 병원 헬스 200·새 리비전
오류 0건·랜딩 CTA 전환을 사후 확인했다. 도입문의 성공 경로(진단 자동 생성·안내 SMS)의
실제 1건 검증은 남아 있다. 실제 digest·롤백 좌표·검증 범위는
[도입문의 CTA 배포 기록](../releases/2026-09-22-inquiry-cta-production.md)을 따른다.

## 최신 운영 배포 — 2026-09-22 통합 안정성 보완

PR #138의 main 소스 `a35b7f2a0c8d7ee568abbd9a7abdda75a231576f`를 5개 서비스에 배포했다.
현재 suffix는 `integrity-a35b7f2`, DB head는 `0079_topic_swap_fallback`이다.
main CI 9/9와 별도 큐 리허설을 통과했고, 운영에서 5개 서비스의 digest·트래픽·설정 보존,
16개 readiness 검사·새 release 7개 큐, 공개 9개 병원·기존 글 보존을 확인했다.
2026-09-22 10:05 KST까지 새 리비전 오류 조회 0건이다. 전체 사용자 E2E와 향후 주기 관측은
별도 범위이며, 이 문서의 갱신 커밋은 런타임 이미지 소스가 아니다.
실제 digest·롤백 좌표·검증 범위는 [통합 안정성 배포 기록](../releases/2026-09-22-integrity-production.md)을 따른다.
아래 날짜별 ‘최신/현재’ 표시는 각 배포 당시의 이력이다.

## 최신 운영 배포 — 2026-09-17 통합 보완

PR #120의 main 소스 `2381566883cfcfcf87d317a2c9671b96817499a8`를 API·Worker·Beat에 배포했다.
Backend suffix는 `ops-235758-2381566`, DB head는 `0079_topic_swap_fallback`, RedBeat는 `2026-09-17.1`이다.
계약 이행·월간 PDF·도메인 감시 보완이 포함됐다. Site 이미지 최적화와 기존 Admin 리비전은 유지했다.
main CI 9/9, 4,326개 Backend 테스트, 운영 readiness와 공개 9개 병원·151개 글 검증을 통과했다.
개발 Slack 미설정과 기존 NHN SMS 설정 경고는 별도 후속 사항으로 남았다.
아래 날짜별 상태는 당시 이력이며 현재 실행 정본은 [통합 운영 배포 기록](../releases/2026-09-17-integrated-production.md)이다.

## 최신 실행 기록 — 2026-09-16 리팩터링

PR #116의 병합 소스 `18531d3452287afa012e28ec256d920708c90a57`를 운영에 배포했다.
현재 서비스 5개의 suffix는 `ref-151613-18531d3`, DB head는 `0079_topic_swap_fallback`이다.
main CI 9/9, 새 release 7개 큐 readiness, 병원 9곳·공개 글 128개·대표 이미지·Admin 로그인
검사를 통과했다. 환경·secret·IAM·자원은 보존했으며 신규 migration/backfill은 없다.
[리팩터링 배포 기록](../releases/2026-09-16-refactor-production.md)의 실제 digest와 롤백 좌표를 사용한다.
아래 실행 기록의 ‘현재’는 각 배포 시점의 이력이다.

## 이전 실행 기록 — 2026-09-16 GEO

GEO 자율 운영 후보 `012aeeb68e1965ea2194300989140ab9b8c4f155`의 전체 운영 배포를 완료했다.
현재 5개 서비스의 suffix는 `geo-125451-012aeeb`, schema head는 `0079_topic_swap_fallback`이다.
환경·secret 참조 보존을 위해 digest-pinned incremental update를 사용했고, 단계 순서와
readiness gate는 아래 절차를 따랐다. Site/Admin SA는 분리됐으며 legacy grants는 유지 중이다.
검사 결과·이미지 index/child digest·롤백 좌표는 [2026-09-16 배포 기록](../releases/2026-09-16-geo-autonomy-production.md)에 있다.
아래 9월 7~9일의 수치와 head는 역사적 실행 기록이며 최신 현재값으로 사용하지 않는다.

문서 버전: **2.5** · 갱신일: **2026-09-08 (Asia/Seoul)**
소스 기준선: **`31129d9911910b82c1161829d922a9760fac13a1`**
구현 상태: **기준선 위 V0 공개 게이트 분리·자동 이어가기 구현 및 검증 중. 운영 배포 전**

이 문서는 현재 작업 코드의 배포 진입점과 운영 구성을 설명한다. `docs/plans`의 Vercel/Supabase 구성은 과거 대체 배포안이다. 마지막으로 완료가 확인된 전체 운영 전환은 [2026-09-07~08 릴리스 기록](../releases/2026-09-07-eb55518-partial.md)에 있다. 현재 기준선 위 V0 공개 게이트 분리·자동 이어가기 변경은 아직 운영에 배포하지 않았다.

## 운영 구성

## 2026-10-09 purpose-first refactor 배포 계약

이 절의 **PROPOSED**는 현재 승인된 purpose-first refactor 구현의 단일 기준선이다. 이 문서 변경 자체는 배포 완료·운영 데이터 변경·기존 보고서 재생성을 주장하지 않는다. 실제 배포 여부는 이 runbook의 revision, migration, readiness, 공개 표면, artifact 검증 기록으로만 판정한다.

- 공개 활성화 소유자는 시스템이다. `profile_complete && site_built`가 충족되면 기본 주소를 자동 활성화하고, 자체 도메인은 DNS·TLS 확인 뒤 활성화한다. V0, `schedule_set`, Essence는 공개 활성화 게이트가 아니다. PAUSED, 명시적 비공개, 공개 권한 회수는 자동 복구·활성화로 되돌리지 않는다.
- 콘텐츠 발행 소유자는 시스템이다. 승인된 운영 기준, 일정, tenant·공개 권한, 의료·사실 안전, 금지 표현, 근거와 artifact 검사를 통과한 글은 **자동 발행**한다. AE는 재시도로 해결되지 않은 terminal exception과 명시적 정정·철회만 처리하며, 정상 발행을 사람 승인 단계로 만들지 않는다.
- 이미지 정책의 단일 PROPOSED 순서는 검증된 글 전용 이미지, 검증된 병원 공용 이미지, **이미지 없음** 또는 현재 장식 모티프다. 이미지 부재와 일시적 이미지 생성 장애는 안전한 본문 공개를 막지 않는다. 검증되지 않은 이미지·합성 provenance/hash·실제 원장으로 오인될 생성 인물은 계속 차단한다.
- 근거 수정은 보수적인 정정/철회가 기본이다. 과거 승인 snapshot을 유지하는 **future-only** addendum/version만 명시적으로 선택할 수 있으며, 이는 hidden 상태·`authority_change`·미해결 의료 또는 사실 위험을 자동 해제하거나 과거 콘텐츠를 자동 재공개하지 않는다.
- 월간 보고서 소유자는 시스템 생성과 AE의 **사람 전달** 기록으로 나뉜다. 닫힌 기간은 COMPLETE/LIMITED/**UNAVAILABLE** 중 하나로 표현한다. UNAVAILABLE도 `sov_pct=null`, 플랫폼별 confirmed/failed/ambiguous/pending 수, 콘텐츠 실적과 다음 조치를 갖춘 정직한 운영 보고서이며, hospital/period/audience/path/bytes/digest가 검증된 artifact/evidence identity와 맞으면 전달할 수 있다. null은 0이 아니고 비교 근거가 없으면 delta를 숨긴다. 시스템은 원장에게 자동 전달하지 않으며 전달 이력은 append-only다.

이 계약을 구현하는 배포는 영구 policy flag/config나 별도 rejection branch를 추가하지 않는다. 배포 전후에는 위 안전 경계와 공개 콘텐츠·전달 artifact의 기존 정체성을 회귀 검증한다.

| 항목 | 확인된 구성 |
|---|---|
| GCP project / region | `mso-platform-481505` / `asia-northeast3` |
| 플랫폼 / Admin | `reputation.motionlabs.kr` / `admin.reputation.motionlabs.kr` |
| HTTPS Load Balancer IP | `34.117.192.90` |
| Cloud Run 서비스 | `reputation-api`, `reputation-worker`, `reputation-beat`, `reputation-site`, `reputation-admin` |
| Backend 이미지 | API/Worker/Beat 공통 이미지, `SERVICE`로 실행 역할 선택 |
| Frontend 이미지 | Site/Admin 각각 Next standalone 이미지 |
| DB / broker | Cloud SQL PostgreSQL 16 / Memorystore Redis |
| 저장소 / 인증서 | GCS, Load Balancer·Certificate Manager 관련 Terraform 구성 |
| 배포 검사 Job | `reputation-migrate`, `reputation-redbeat-reconcile`, `reputation-production-readiness` |

구성 정본은 [Terraform](../../terraform/cloudrun.tf), [DB](../../terraform/cloudsql.tf), [배포 스크립트](../../scripts/deploy.sh)다. 인프라 선언과 실서비스의 차이가 있을 수 있으므로 다음 배포에서는 다시 조회한다. Secret Manager 값·환경변수 전체를 출력하지 말고 필요한 비밀이 아닌 설정과 secret 참조의 보존 여부만 확인한다.

## 배포 진입점과 실제 순서

저장소 루트의 `.env.production`과 접근 가능한 Secret Manager 설정을 준비한다. 비밀 값을 추적 파일에 넣지 않는다. `scripts/deploy.sh`는 Git 소스 버전을 고정하고 필수 환경·secret·DB 연결·공개 도메인 등을 검사한다.

**`deploy.sh`가 스스로 지키는 규칙(2026-10 자율 운영 P2).** 사람이 배포 전후에 하던 확인 몇 가지를 스크립트가 맡는다.

- **env 정본은 운영 서비스다.** backend 대상(`backend·api·worker·beat·all·migrate`)은 운영 `reputation-api`의 현재 env(비밀 아닌 값)를 배포 입력으로 쓴다. 로컬 `.env.production`이 다르면 바뀌는 키 이름만(값은 출력하지 않음) 보여 주고 멈춘다. 로컬 값으로 바꾸려는 것이 맞으면 `--accept-env-diff`를 붙인다. 서비스가 없는 신규 환경은 `.env.production`으로 시작한다. 운영 env를 읽지 못하면(권한 등) 로컬 파일로 대신 배포하지 않는다.
- **시간대와 하루 잠금.** Worker·Beat를 바꾸는 대상(`backend·api·worker·beat·all`)은 콘텐츠 배치 시간대 — KST 22:15~02:00 전체(23:00 야간 생성이 몇 시간 이어지고 01시 복구 스윕이 뒤따른다)와 04·07·08·12·18·22시 앞뒤 45분(08시는 07:45·08:00 발행 게이트와 08:30 외부 감시) — 이면 멈춘다. 배치 시간대 밖이면 하루 여러 번 배포할 수 있다(2026-10-13 대표 결정). 하루 잠금(`gs://reputation-reports-<project>/ops/deploy-lock/<KST 날짜>.json`)은 권고다 — 같은 날 다른 릴리스가 이미 나갔으면 경고하고 `--override-reason="사유"`로 사유를 남겨야 진행한다(같은 릴리스의 재시도는 그대로 허용). 사유는 배치 시간대 규칙도 넘으므로, 배치 시간대 안에서 사유를 쓰면 따로 경고하고 기록(`batch_window`)에 남긴다. 이 확인은 legacy publish 사전 확인보다 먼저 돌고, 빌드·마이그레이션이 길어 실제 교체가 배치 시간대에 들어가지 않도록 **이미지 빌드 뒤 첫 운영 변경 직전과 Worker(또는 `beat` 대상의 Beat) 교체 직전에 다시 본다** — 들어갔으면 그 단계 전에 멈춘다(빌드 뒤 첫 확인에서 멈추면 운영 변경이 없고 잠금도 되돌린다; Worker 교체 직전에 멈추면 이미 적용된 추가형 마이그레이션만 남으므로 배치 시간대 밖에서 같은 명령을 다시 실행한다). Worker를 바꾼 뒤에는 Beat까지 마저 바꾼다(둘 사이에서 멈추면 반쪽 상태가 남는다). `--override-reason`은 이 재확인도 경고와 함께 넘긴다. **운영 변경(마이그레이션·서비스 교체) 전에 실패하면 이 실행이 만든 잠금을 되돌린다** — 사전 확인·이미지 빌드 실패 뒤 고친 릴리스를 같은 날 다시 배포할 수 있다. 사유는 같은 경로의 `<날짜>-override-<HHMM>.json`에 남는다. `DEPLOY_CLOCK_KST`는 테스트 전용이며 운영에서는 사유 없이는 멈추고, 사유가 있으면 실제 시각으로 기록하고 `clock_override: true`를 남긴다. `site·admin·migrate·rollback`은 막지 않는다.
- **자동 롤백.** readiness Job이 실패하거나, API 배포 뒤 공개 표면 확인(`/api/v1/health/live`의 `release`가 이번 배포 SHA인지, 배포 전 정상이던 병원 헬스의 `slug`·`canonical_host`·Site 리비전이 그대로인지 — `scripts/deploy_smoke.py`)에서 API `release`가 다르거나 기준선 병원의 절반 이상이 실패하면 `.deploy-rollback`의 배포 시작 전 리비전으로 트래픽을 자동으로 되돌린다. 일부(절반 미만) 병원만 실패하면 경고와 함께 실패로 끝나되 롤백하지 않는다(그 병원 공개 주소를 확인한다). DB 마이그레이션은 되돌리지 않는다. 0084 같은 비호환 릴리스는 `AUTO_ROLLBACK=0`으로 끄고(멈추기만 한다) 아래 롤백 절을 따른다. 공개 표면 확인은 `SKIP_POST_DEPLOY_SMOKE=1`로 건너뛸 수 있다(로그에 남는다). 기준선은 첫 mutation 전에 만들며, 병원 목록 조회 실패·목록이 아닌 응답과 **정상 병원 0곳**은 배포를 멈춘다 — 0곳 기준선이면 배포 뒤 확인이 병원 헬스를 하나도 보지 않고 통과하기 때문이다. 공개 병원이 정말 없는 환경만 `ALLOW_EMPTY_SMOKE_BASELINE=1`로 명시한다.
- **고정 트래픽.** 서비스 트래픽이 특정 리비전에 고정돼 있으면 배포 전에 멈춘다. 고정이 의도한 것이 아니면 `--unpin`을 붙인다 — 새 리비전이 Ready가 된 뒤 `update-traffic --to-latest`로 옮기고, 롤백 좌표는 지금 트래픽을 받는 리비전으로 남긴다(배포 전에 `--to-latest`를 먼저 치면 고정의 이유였을 리비전으로 트래픽이 간다).

**(완료 2026-09-08 — 체크포인트 2 배포 전에 아래 절차로 생성했다. 새 프로젝트/환경에서만 다시 필요하다.)** **`BFF_ACTOR_SECRET` 생성(H-10).** 백엔드는 사람이 일으키는 admin 변경(POST/PATCH/PUT/DELETE)에 Admin BFF가 서명한 actor 단언을 요구하고, 이 값이 비어 있으면 API가 프로덕션에서 부팅에 실패한다. API와 Admin이 **같은 값**을 읽어야 서명이 검증된다. Terraform이 secret 리소스와 두 서비스 주입·IAM을 선언하고(`terraform/secretmanager.tf`), `scripts/deploy.sh`도 API·Admin 필수 시크릿 목록에 넣어 값이 없으면 배포 전 검사에서 멈춘다. 배포 전에 값만 만들어 둔다.

```bash
# 개행 없이 저장한다(openssl 출력 끝의 줄바꿈이 값에 들어가면 안 된다 — API·Admin 모두 값을 trim하지만 저장값 자체를 깨끗하게 둔다).
gcloud secrets create BFF_ACTOR_SECRET --project mso-platform-481505 --replication-policy=automatic --data-file=<(openssl rand -hex 32 | tr -d '\n')
# 접근 권한은 ADMIN_SESSION_SECRET과 같은 두 서비스 계정에 준다(terraform apply가 동일하게 부여한다).
gcloud secrets add-iam-policy-binding BFF_ACTOR_SECRET --project mso-platform-481505 \
  --member="serviceAccount:$(terraform -chdir=terraform output -raw service_account_email)" --role=roles/secretmanager.secretAccessor
gcloud secrets add-iam-policy-binding BFF_ACTOR_SECRET --project mso-platform-481505 \
  --member="serviceAccount:$(terraform -chdir=terraform output -raw frontend_service_account_email)" --role=roles/secretmanager.secretAccessor
```

배포 순서상 API가 Admin보다 먼저 새 리비전을 받는다. 단언은 브라우저가 아니라 Admin BFF(서버)가 서명하므로, **새 API가 뜬 뒤 새 Admin 리비전이 트래픽을 받기 전까지(보통 수 분) 모든 admin 변경 요청이 403 `ACTOR_ASSERTION_REQUIRED`("관리 화면을 새로고침한 뒤 다시 시도해 주세요")를 받는다.** Admin 리비전 교체가 끝나면 열려 있던 탭도 그대로 복구된다(새로고침 불필요). 다만 옛 콘텐츠 화면을 열어 둔 탭은 반려 요청에 사유 본문이 없어 새 API에서 422를 받으므로 새로고침이 필요하다 — 롤아웃 직후 운영자에게 두 사실을 알린다. 배치·CLI 호출은 `X-Admin-Actor-System: <job>` 헤더로 통과하며 감사 기록에는 `system:<job>`으로 남는다. 이 시스템 호출에는 **사람 계정이 없다** — 함께 보낸 `X-Admin-Actor`는 인가·감사 어디에도 채택되지 않고, 활성 운영자 계정을 요구하는 라우트(계정 관리·인수·운영센터 조치 등)는 403 `SYSTEM_ACTOR_NOT_ALLOWED`로 거부된다. 사람 요청에서 `X-Admin-Actor`가 단언의 이메일과 다르면 403 `ACTOR_ASSERTION_MISMATCH`다(BFF는 두 값을 같은 세션 이메일로 채우므로 정상 트래픽은 걸리지 않는다).

```bash
make db-budget-guard
make copy-guard
bash scripts/deploy.sh all
```

전체 배포의 표준 순서는 다음과 같다.

1. 어떤 변경보다 먼저 필수 설정·도메인·secret·저장소·런타임 조건을 검사한다.
2. 현재 서비스 5개의 리비전을 `.deploy-rollback`에 보관한다.
3. Backend/Site/Admin 이미지를 빌드하고 업로드한다.
4. 새 Backend 이미지로 마이그레이션을 실행한다. 기존 코드와 호환되는 추가형 스키마 변경을 전제로 한다.
5. Worker를 배포하고 Redis에 남은 RedBeat 스케줄을 재조정한다.
6. Beat를 배포한 뒤 production readiness Job으로 DB·Redis·현재 릴리스 큐 소비와 필수 설정을 확인한다.
7. API, Site, Admin을 배포한다.
8. 실제 트래픽·리비전과 외부 공개 표면을 별도로 검사한다. 앞선 readiness만으로 이후 프론트엔드까지 검증되었다고 보지 않는다.

**배포 횟수와 시간대(2026-10-08, 2026-10-13 완화).** 배포는 콘텐츠 배치 시간대(23:00과 01·04·07·12·18·22시 KST 전후)를
피한다. 배치 시간대 밖이면 하루 여러 번 배포할 수 있다(2026-10-13 대표 결정 — 10/8에는 하루 한 번으로 묶었다).
`deploy.sh`가 배치 시간대를 막고, 하루 1회 잠금은 `--override-reason`으로 넘긴다(위 "`deploy.sh`가 스스로 지키는 규칙"). 10/6~10/8의 V0 보고서 2건 유실과 `expired authenticated dispatch envelope` 폭주는 잦은 배포가 원인이었다 —
Cloud Run이 옛 워커를 10초 뒤 SIGKILL하면 kombu가 미확인 메시지를 3600초 뒤 되돌려 봉투가 만료됐다. PR #226 뒤의
워커는 `REMAP_SIGTERM=SIGQUIT`(cold shutdown)으로 실행 중 메시지를 즉시 큐에 반납하고, 반납된 사본은 새 워커가
몇 초 안에 다시 받는다. 봉투 수명은 2단계로 올린다 — 이번 릴리스는 수락 상한만 6시간(`DISPATCH_MAX_LIFETIME_SECONDS`)으로
열고 서명은 1시간(`DISPATCH_TTL_SECONDS`) 그대로 두어 겹침 구간의 옛 워커가 새 봉투를 거절하지 않게 하고, #226 이전
워커 리비전이 모두 사라진 다음 릴리스에서 서명을 6시간으로 올린다. 옛 워커가 실행 중이던 메시지는 7200초 뒤 되돌아와
만료로 거절되지만(일회성, 사고 1건), 새 순서에서는 만료 사본이 실행을 claim하거나 FAILED로 끝내지 못한다. 배포 뒤에는 `run_beat_entry daily-fleet-heartbeat`로
일일 요약을 즉시 받아 "점검 이상 없음"을 확인한다. **2026-10-11 릴리스(Phase 0)에서 서명을 6시간으로 올렸다**(#226 이전 워커
`00223-llc` 이하는 10/8 이후 트래픽 없음 — 10/8의 `00224-xnt`부터 #226이 들어 있다). 이후 워커를 `00223` 이하로 롤백하지 않는다 —
그 리비전은 수명이 정확히 1시간인 봉투만 받으므로 6시간 봉투를 모두 거절한다.

**Phase 0(2026-10-11, `0087_add_operation_run_redispatch_count`) — 배포 전 확인.** 0087은 추가형(`operation_runs.redispatch_count`
NOT NULL DEFAULT 0)이라 `deploy.sh`의 migrate 선행 순서로 충분하다. 배포 전에 아래를 읽기 전용으로 조회해 기록한다([운영 DB 읽기 전용 조회](#운영-db-읽기-전용-조회)의
migrate Job으로 돌리거나 같은 SELECT를 읽기 전용 세션에서 실행한다).

```sql
-- (a) 배포 뒤 첫 자율 복구가 FAILED(OPERATION_RUN_ABANDONED)로 정리할 RUNNING 잔재. MONTHLY_SOV_PERIOD는 20일 기준이라
--     10일 결과에 들어 있어도 정리되지 않을 수 있다.
SELECT operation_type, count(*) FROM operation_runs
WHERE state='RUNNING' AND operation_type <> 'SITE_REVALIDATION'
  AND (lease_expires_at IS NULL OR lease_expires_at <= now())
  AND updated_at <= now() - interval '10 days'
GROUP BY 1;
-- (b) 교체 후보 SQL이 정수로 캐스트하는 수리 일수(essence_check_summary 최상위 automatic_body_repair). 기대값 0.
SELECT count(*) FROM content_items
WHERE jsonb_typeof(essence_check_summary->'automatic_body_repair') = 'object'
  AND jsonb_typeof(essence_check_summary->'automatic_body_repair'->'exhausted_days') NOT IN ('number','null');
-- (c) 이미 교체된 슬롯 수와, 첫 스윕에서 곧바로 교체될 수 있는 생성 예산 소진(RETRYING) 슬롯 수.
SELECT count(*) FILTER (WHERE jsonb_array_length(coalesce(topic_swap_history,'[]'::jsonb)) > 0) AS swapped,
       count(*) FILTER (WHERE essence_check_summary->'generation_attempt'->>'reason' = 'GENERATION_BUDGET_EXHAUSTED'
                          AND essence_check_summary->'generation_attempt'->>'retry_class' = 'SAMPLE_RECOVERABLE'
                          AND first_published_at IS NULL AND human_edited_at IS NULL
                          AND jsonb_array_length(coalesce(topic_swap_history,'[]'::jsonb)) = 0) AS swap_candidates
FROM content_items
WHERE status NOT IN ('PUBLISHED','CANCELLED','WITHHELD');
```

(b)가 0이 아니면 배포 전에 그 행을 확인한다 — 정수가 아닌 값은 교체 후보 SQL의 캐스트를 실패시킨다. (c)의 `swap_candidates`는
현재 주제의 작가 예산이 찼는지를 파이썬 술어가 다시 보므로 상한일 뿐이다.

**Phase 0 — 배포 뒤 1회 조치.** 배포만으로는 이미 기한 없이 굳은 슬롯이 풀리지 않는다(게이트가 예전에 `OPERATOR_REQUIRED`로
남긴 `CONTENT_NOT_GENERATED` 등). `ops_sql_report --only=q4`로 `OPERATOR_REQUIRED`·`INPUT_CHANGE_REQUIRED` 슬롯을 원인별로 뽑고,
그중 원고 미생성으로 동결된 슬롯은 운영센터 사고 큐(`/operations?queue=incidents`)의 "작업 다시 시도"로 한 번 풀어 준다. `LEGACY_SPEND_UNKNOWN`(2026-10-11 조회 기준 12건)은 이 버튼으로 풀리지 않는다 — 운영센터의 "레거시 예산 교체 후 다시 시도"(`POST /api/admin/hospitals/{h}/content/{c}/regenerate`, 사유 필수·검증된 actor·멱등 키)로 글마다 예산을 교체해야 하며, 사람이 지출 사실을 확인하는 감사 가능한 행위라 일괄 스크립트로 대신하지 않는다.
**승인된 1회 예외(2026-10-13 대표 결정 D22):** 10/9 이전에 만든 빈 슬롯 23건(10/12~15 예정, `LEGACY_SPEND_UNKNOWN` 11 + 게이트가 `CONTENT_NOT_GENERATED`로 덮은 12)은 버튼과 같은 교체 함수(`generation_attempt_state.replace_legacy_budget_and_release`)를 시스템 actor `system:legacy-budget-reset-20261013`로 부르는 CLI로 한 번에 교체한다. 교체는 행마다 행 잠금·감사 로그(`LEGACY_BUDGET_REPLACED`, 교체 전 시도 기록 전체 포함)·커밋이며, 억제를 풀어 다음 복구 스윕이 다시 쓴다. 대상은 공개 이력·제목·본문이 없고 2026-10-09 16:00 UTC 이전에 생성된 DRAFT/READY 중 저장 원인 또는 열린 사고 코드가 `LEGACY_SPEND_UNKNOWN`이고 교체 함수가 받아들이는 행이며, 두 번째 실행은 0건이다. 이 예외는 이 23건에만 적용하고 이후의 레거시 슬롯은 위 버튼으로 처리한다:
`gcloud run jobs execute reputation-migrate --project mso-platform-481505 --region asia-northeast3 --wait --args=python,-m,app.utils.legacy_budget_reset` (dry-run, 건수·id 확인) → `--args="^@^python@-m@app.utils.legacy_budget_reset@--apply@--reason=<사유>"` (적용). 이 CLI가 든 이미지가 배포되기 전에는 같은 코드를 base64 `python -c`로 넘긴다.
다음 날 지표는 MasterPlan §5 Phase 0 (f)를 따른다.

**Phase 1(`0088_add_outbox_open_pairing_index`) — 배포 전 확인.** 0088은 `notification_outbox`에 열림 알림 짝 조회용 부분
인덱스를 CONCURRENTLY 없이 만든다(만드는 동안 outbox 쓰기가 기다린다). outbox는 운영 알림 행만 쌓이는 작은 표(수천 행 단위)라
1초 안팎이지만, 배포 전에 행 수를 읽기 전용으로 확인해 기록한다.

```bash
gcloud run jobs execute reputation-migrate --project mso-platform-481505 --region asia-northeast3 --wait \
  --args=python,-m,app.utils.ops_sql_report,--only=q7
```

`outbox_rows`가 **약 100만 행 이하**면 표준 `deploy.sh`(migrate 선행)로 진행한다. 넘으면 배포 전에 읽기 전용이 아닌 세션에서
`CREATE INDEX CONCURRENTLY ix_notification_outbox_open_pairing ON notification_outbox (incident_id, state, created_at)
WHERE notification_type = 'INCIDENT_OPEN';`을 먼저 실행한다 — 0088은 인덱스가 이미 있으면 생성을 건너뛴다.

**Phase 2(마이그레이션 없음, DB head `0088` 그대로) — 배포일 절차.** 대상은 API·Worker·Beat(`bash scripts/deploy.sh backend`)와
Admin(`bash scripts/deploy.sh admin` — 병원 정보 화면의 자동 입력 초안, 시간대 규칙 없음)이다. Site 변경은 없다. Phase 1이 먼저
배포됐는지, 운영 DB head가 `0088_add_outbox_open_pairing_index`인지 먼저 확인한다.

이 릴리스의 배포 근거는 MasterPlan의 **최신 결정**이다 — 하위 초안의 문구가 아니다. D26: 처리 ERROR 필수 자료는 Essence 합성에서
'즉시'가 아니라 다른 필수 자료가 PROCESSED면 **1시간**, ERROR뿐이면 **6시간** 뒤에 제외한다. D27: 대기 자기 도메인의 DNS 확인은
14일 창이 지나도 **멈추지 않고 15분마다** 계속하며(하루 1회로 줄이지 않는다), 창이 지난 뒤 그 회차가 실제로 DNS 불일치를 관측했을
때만 사람의 일 하나를 연다. D28: 배포 금지 시간대는 아래 1번이고 그 밖이면 하루 여러 번 배포할 수 있다(하루 잠금은 권고). 배포 뒤
공개 표면 확인(`deploy_smoke.py`)은 API `release`와 병원별 **slug·canonical_host·Site 리비전**만 비교한다 — hospital ID는 보지 않으므로
hospital ID 대조를 smoke가 했다고 기록하지 않는다(필요하면 따로 확인한다).

1. **시각.** `deploy.sh`(backend·api·worker·beat·all)가 막는 KST 구간은 21:15~02:00(22시 앞뒤 45분 + 22:15~02:00 야간), 03:15~04:45,
   06:15~08:45, 11:15~12:45, 17:15~18:45다. 배포 가능 구간은 02:00~03:14, 04:46~06:14, 08:46~11:14, 12:46~17:14, 18:46~21:14.
2. **Terraform 경보 2종(배포 코드와 독립, 먼저 적용해도 된다).** `terraform plan`이 아래 두 정책의 **추가 2건만** 보이는지 확인한 뒤
   그 둘만 적용한다. 둘 다 기존 이메일 채널(`google_monitoring_notification_channel.email[0]`)과 감시 스케줄러 잡을 참조하므로 다른
   리소스 변경이 나오면 멈추고 원인을 본다.

   ```bash
   terraform -chdir=terraform apply \
     -target='google_monitoring_alert_policy.watchdog_scheduler_failures[0]' \
     -target='google_monitoring_alert_policy.slack_path_failures[0]'
   ```
3. **`PIPELINE_WATCHDOG_TOKEN`.** 이번 릴리스부터 readiness가 운영에서 이 토큰이 비면 실패하고(`pipeline_watchdog_token_configured`)
   자동 롤백된다. 활성 버전이 있는지 확인하고, Cloud Scheduler 감시 잡의 헤더 값과 같은지 본다(다르면 감시 호출이 401이고 새 경보
   `watchdog_scheduler_failures`가 울린다 — 값 일치는 코드로 검증되지 않는다).

   ```bash
   gcloud secrets versions list PIPELINE_WATCHDOG_TOKEN --project=mso-platform-481505 --filter=state=enabled
   ```
4. **`OPS_ALERT_EMAIL` 값 = Terraform `alert_email`.** 저장소에는 실제 값이 없다(`terraform.tfvars` 비추적, 예시만 있음). 운영의 이메일
   경보 채널 주소를 읽어 같은 주소를 `.env.production`에 `OPS_ALERT_EMAIL=<주소>`로 넣는다(비밀이 아닌 env). `RESEND_API_KEY`는 이미
   필수 시크릿이라 메일은 바로 보낼 수 있다.

   ```bash
   gcloud monitoring channels list --project=mso-platform-481505 --filter='type=email' --format='value(labels.email_address)'
   ```
5. **하루 잠금 버킷.** 잠금 경로 `gs://reputation-reports-mso-platform-481505/ops/deploy-lock/`는 Phase 2 첫 배포가 처음 만든다. 작업 PC
   계정이 그 버킷에 객체를 만들고 지울 수 있는지 미리 본다(못 하면 첫 실행이 "배포 잠금을 확인하지 못했습니다"로 멈춘다).

   ```bash
   echo '{}' | gcloud storage cp - gs://reputation-reports-mso-platform-481505/ops/deploy-lock/probe.json
   gcloud storage rm gs://reputation-reports-mso-platform-481505/ops/deploy-lock/probe.json
   ```
6. **첫 실행과 `--accept-env-diff`.** 배포 정본이 로컬 파일에서 운영 env로 바뀌었다. 먼저 옵션 없이 `bash scripts/deploy.sh backend`를
   실행하면 차이 키 목록(값은 출력하지 않는다)만 보여 주고 멈춘다. 목록이 **정확히 `added: OPS_ALERT_EMAIL` 하나**일 때만
   `bash scripts/deploy.sh backend --accept-env-diff`로 다시 실행한다. `removed:`·`changed:`나 다른 `added:` 키가 하나라도 있으면
   `--accept-env-diff`를 붙이지 않는다 — 로컬 파일이 낡은 것이고, 붙이면 로컬 파일이 서비스 env를 통째로 바꿔 `removed` 키가 운영에서
   사라진다(9/23·10/1 사고의 원인). 로컬 파일을 운영 env에 맞춰 고친 뒤 다시 본다(Phase 1이 지우게 한 `SOV_MONTHLY_COHORT_LIMIT`이
   운영에 남아 있으면 `removed:`로 나온다 — Phase 1 배포 기록부터 확인한다). 이후 배포부터는 운영 env가 정본이다.
7. **배포 뒤 확인.** readiness 18개 검사 전부 통과(`checks.pipeline_watchdog_token_configured: true`, `facts.ops_alert_email_configured:
   true`), `/api/v1/health/live`의 `release`가 이번 릴리스(`deploy.sh`가 자동 확인), Beat `/live` 정상(`beat_liveness_failed` 로그 없음,
   기동 뒤 15분 유예), 새 리비전 ERROR 0건, 15분 뒤 `project_milestone_events` 결과에 `onboarding_stalls_*` 키와
   `monitor_live_custom_domains` 결과에 `pending_domains_*` 키, GCP 경보 정책 2개 존재. 트래픽이 고정돼 있으면 `--unpin`을 붙인다.
8. **롤백 — 마이그레이션이 없어도 트래픽 롤백만으로 안전하지 않다(첫 달 안분).** Phase 2 이전 Worker·API(main `08abf747` 이하)는
   그 달 약속 편수를 언제나 요금제 편수로 계산하고, 옛 보고서·현황·계약 이행 집계도 안분을 모른다. Phase 2가 첫 일정을 만들며 첫
   계약 월을 안분한 병원(예: PLAN_12 9편, 시작일 뒤로 게시 요일이 없는 달 0편)이 있는 채로 되돌리면, 매일 **21:30 KST 당월 조정**
   (`current-month-slot-reconciliation`)이 그 달을 요금제 편수까지 채운다(9 → 12편, 0편 달은 남은 날에 한 달 편수를 몰아 넣는다).
   채워진 슬롯은 23:00 야간 생성이 사서 쓰고 자동 발행한다 — **초과 배정·초과 비용**이다. 옛 코드가 만든 그 달 월간 보고서·현황은
   요금제 편수를 약속으로 보여 준다(9편을 다 내도 "12편 중 9편"). 호환 리비전은 만들지 않았다. 자동 롤백(readiness·smoke 실패)도
   같다 — 새 Worker·Beat는 API 승격 전에 15분 tick을 돌므로 실패한 배포도 첫 일정을 만들었을 수 있다. 롤백하면 다음을 한다.
   1. **대상 확인(읽기 전용).** 안분된 첫 달이 있는 병원과 그 달 슬롯 수를 본다. `q8` 행이 없거나 모든 행의 첫 달이 지난달 이전이면
      당월 조정과는 무관하다(지난달 보고서 재생성만 4번을 본다).

      ```bash
      gcloud run jobs execute reputation-migrate --region=asia-northeast3 --project=mso-platform-481505 --wait \
        --args=python,-m,app.utils.ops_sql_report,--only=q8
      ```

      결과는 Cloud Logging의 그 실행 로그에 `{"q": "q8", ...}`(병원·첫 달·`first_month_target`·`month_slots`·`topped_up_slots`)와
      `{"q": "q8b", ...}`(취소 후보 슬롯) 한 줄씩 남는다.
   2. **시각.** 옛 Worker가 21:30 당월 조정을 한 번이라도 돌리면 1번의 병원에 초과 배정이 생긴다. 롤백한 채 21:30을 넘기지 않거나
      (그 전에 원인을 고쳐 Phase 2로 다시 올린다 — 단 `deploy.sh`의 배치 시간대 규칙상 21:15 이후 Backend 배포는 사유가 필요하다),
      넘긴다면 3번을 그날 23:00 야간 생성 **전에** 반드시 한다.
   3. **초과 배정 되돌리기(수동).** 21:30이 지난 뒤(Phase 2로 다시 올린 뒤에도) `q8`을 다시 돌려 `topped_up_slots`가 0이 아닌 병원을
      찾는다. 옛 코드가 채운 슬롯은 `total_count`가 기록된 안분 편수보다 커서 구분된다. `q8b`가 그중 아직 생성·공개·사람 편집이
      없는(DRAFT·본문 없음·claim 없음) 슬롯을 준다. 그 슬롯을 기존 취소 API `POST /admin/hospitals/{hospital_id}/content/{content_id}/cancel`
      (감사 `cancel_content`)로 CANCELLED로 바꾼다 — 전용 CLI도 Admin 화면 버튼도 없어 **수동 절차**다(운영 Admin 키와
      `X-Admin-Actor-System` 헤더로 호출해 감사에 `system:<작업명>`으로 남긴다). 행을 DELETE하지 않는다 — CANCELLED 행도 그 달 슬롯 수에
      남아 옛 코드·새 코드 모두 다시 채우지 않는다. 이미 생성됐거나 공개된 초과 슬롯은 자동으로 되돌릴 방법이 없다 — 공개 글은 내리지
      않는 것이 기본이고, 사람이 그 달 약속과 함께 판단한다.
   4. **보고서.** 롤백 중 옛 코드가 만든 안분된 달의 월간 보고서는 Phase 2로 다시 올린 뒤 일반 재생성으로 다시 만든다(약속 편수가
      달라 숫자 그대로인 `TEMPLATE_REFRESH` 대상이 아니다).

   Phase 0의 롤백 금지(Worker `00223` 이하)는 그대로다. 같은 이유로 롤백 뒤 사람이 그 달 일정을 바꾸면 옛 교체 경로도 요금제 편수로
   다시 만든다 — 롤백 중에는 안분된 병원의 일정 교체를 미룬다.

**10/8 배치(PR #226, `0083_add_operation_run_not_before`) — 0083 migrate Job을 먼저 끝내고, 코드는 그 다음 배포한다.**
새 ORM이 `operation_runs.not_before_at`을 SELECT한다. 추가형·NULL 허용이라 0083 위의 옛 코드는 안전하다.
`deploy.sh`의 Backend 대상은 4단계에서 마이그레이션을 먼저 실행하므로 표준 경로를 쓰면 순서가 지켜진다.

**Slack 웹훅 시크릿(PR #228).** `deploy.sh`는 주입할 `SLACK_WEBHOOK_URL`·`SLACK_WEBHOOK_URL_DEV` 값이 비어 있지 않으면
`{}`를 POST해 생존을 확인한다(살아 있는 웹훅은 메시지를 만들지 않고 400 `no_text`를 돌려준다). 죽은 주소(3xx·403·404·410)면
배포 전에 멈추고, 5xx·시간 초과는 한 번 더 본 뒤 경고만 남긴다. **선택** 시크릿의 latest 버전이 DISABLED이면 '의도적으로
끔'으로 보고 주입하지 않는다(info 한 줄). 운영은 채널 하나이므로 `SLACK_WEBHOOK_URL_DEV`는
`gcloud secrets versions disable 1 --secret=SLACK_WEBHOOK_URL_DEV`로 꺼 둔다. 조회 실패와 필수 시크릿의 비활성은 종전처럼
배포를 멈춘다.

**10/1 배치(PR #177, `0082_add_content_reference_checks`) — 0082 migrate Job을 먼저 끝내고, 코드는 그 다음 배포한다.**
- 역순이면 새 ORM이 아직 없는 `content_items.reference_checks`를 읽는다. 그러면 콘텐츠를 조회하는 모든 경로(공개 사이트 포함)가 `UndefinedColumn`으로 실패한다.
- `deploy.sh`의 Backend 대상(`all`·`backend`·`api`·`worker`·`beat`)은 4단계에서 마이그레이션을 먼저 실행한다.
- digest 기반 수동 rollout 같은 대체 경로를 쓰면 `migrate`를 먼저 돌린다. 운영 DB current head가 `0082_add_content_reference_checks`인지 확인한 뒤에 새 리비전을 올린다.
- 0082 적용 뒤 옛 코드는 영향이 없다(추가형·NULL 허용 컬럼).

체크포인트 2(release `a774851`, 2026-09-08 21:11Z)는 [기록](../releases/2026-09-09-checkpoint-2.md)대로 head `0071`을 적용했다. 현재 운영 DB의 마이그레이션 체인은 `0065_provider_usage` → `0066_content_contracts` → `0067_measurement_slots` → `0068_lead_cost_deferral` → `0069_content_first_publication` → `0070_essence_evidence_noise_hash` → `0071_plan_enum_cleanup`이고 expected head는 `0071_plan_enum_cleanup`다. `0071`은 남은 `PLAN_8` 행을 `PLAN_12`로 옮긴 뒤 `hospitals.plan`·`content_schedules.plan`에 12/16/20 CHECK만 건다. `plan` enum 타입은 손대지 않고 폐기 label도 타입에 남긴다 — 값을 지우려면 타입 rename-swap이 필요한데 그러면 타입 OID가 바뀌어, 롤링 중 아직 도는 옛 API·Worker 리비전의 asyncpg/psycopg2 연결 풀이 들고 있는 타입 OID·prepared statement 캐시가 깨진다(stale type OID / `InvalidCachedStatementError`). 제약 추가는 타입 정체성을 바꾸지 않으므로 롤링 중 실행해도 안전하고, 두 컬럼 모두 CHECK가 막으므로 어떤 행도 `PLAN_8`을 가질 수 없다(코드의 `Plan`도 12/16/20뿐). `0070`은 승인 당시 노이즈로 제외한 근거 노트 집합 hash를 기록한다 — 기존 승인 행은 NULL이며 다음 재조정에서 병원당 1회 유료 재검수가 발생한다(운영 7곳). 공급자 시도 원장, 콘텐츠 revision·provenance·이미지 인증, 월간/V0 고정 관측 슬롯, 무료 진단 비용 차단 재개 시각, 최초 공개 시각·주체를 추가했다. `0069`는 남아 있던 `published_at`·`published_by`만 최초 공개 사실로 백필했다. 이전 수동 반려가 이미 지운 과거 값은 추정하지 않고 NULL로 남겼다. 배포 직전 이미지의 expected head, Alembic heads와 운영 DB current head를 다시 읽어 모두 일치시킨다.

### 2026-09-07~08 기존 공개 콘텐츠 전환

이 일회성 전환은 완료된 운영 이력이며 반복 실행 절차가 아니다. 공개 글 115건을 exact manifest로 고정한 뒤 FAQ 3건을 CAS로 수리하고 본문 22건을 독립 검수했으며, 기존 이미지 115건을 실제 바이트에 묶어 인증했다. 최종 결과는 7개 tenant·공개 글 115건 모두 public safe, 누락·미인증·미해결 0건이었다. 이미지 79건은 기존 바이트를 유지하고 36건은 안전한 바이트로 교체했으며, 배포 후 115건 모두 HTTP 200과 실제 바이트 SHA-256·URL 인증 hash 일치를 확인했다.

마이그레이션 → Worker → 콘텐츠·이미지 전환과 새 strict public gate → RedBeat·Beat·readiness → API·Site·Admin → 공개 HTTP 검증 순서를 사용했다. 상세 결과와 당시 소스·digest·비용 범위는 [2026-09-07~08 운영 전환 기록](../releases/2026-09-07-eb55518-partial.md)에 보존한다. 이후 배포에서 이 backfill을 다시 실행하지 말고 현재 공개 gate를 회귀 검사한다. 상세 운영 ID나 비공개 payload를 문서·로그 요약에 나열하지 않는다.

`backend`는 Backend 3종 배포다. **`api`도 Worker와 Beat를 먼저 함께 갱신하는 경로**이므로 API만 바뀐다고 가정하지 않는다. `worker`, `beat`, `site`, `admin`, `migrate` 개별 대상도 제공한다. 정확한 범위는 스크립트의 마지막 case문을 확인한다.

2026-09-07~08 전환은 로컬 업로드 지연 때문에 Cloud Build와 digest 기반 rollout을 사용했고 기존 서비스 환경·secret 참조 hash 보존을 별도로 검증했다. 이 실행을 `scripts/deploy.sh`의 기본 Cloud Build 기능으로 오해하지 않는다. 반복 가능한 기본 진입점은 위 스크립트이며 대체 배포도 같은 마이그레이션·Worker/Beat·readiness 순서를 지켜야 한다.

## 월간 리포트 템플릿 갱신 (숫자 그대로)

원장용 월간 PDF 템플릿을 바꿔 배포한 뒤 이미 만든 달의 리포트를 새 문구·디자인으로 다시 찍을 때는
일반 재생성(`rebuild=true`)을 쓰지 않는다 — 마감을 지금으로 잡아 숫자가 흔들린다.
`rebuild=true&template_only=true`(`TEMPLATE_REFRESH`)와 `python -m app.utils.monthly_template_refresh`
의 사전 확인 → 실행 → 사후 확인을 쓴다. 측정 복구 기간(매월 1~7일 KST)에는 실행하지 않는다.
절차·판정·명령은 [템플릿 갱신 절차](monthly-template-refresh.md)를 따른다.

## 파이프라인 외부 감시 (Cloud Scheduler)

Beat나 Worker가 죽으면 23:00 생성과 08:00 발행이 조용히 멈춘다. 그 사실을 알릴 작업도 같은 Beat 위에 있으므로 감시는 Celery 밖에 둔다. Cloud Scheduler 두 개가 API를 직접 호출한다 — `reputation-watchdog-heartbeat`(5분마다)와 `reputation-watchdog-publish-check`(매일 08:30 KST). 둘 다 공개 LB를 거쳐 `POST https://reputation.motionlabs.kr/api/v1/admin/watchdog/pipeline/alert`를 부르고 `X-Watchdog-Token` 헤더로 인증한다(선언: [terraform/watchdog.tf](../../terraform/watchdog.tf), 판정: [pipeline_watchdog.py](../../backend/app/services/pipeline_watchdog.py)). 새 Celery 태스크나 Beat 스케줄은 추가하지 않았다.

**무엇을 보는가.** (1) 큐 canary 신선도 — Redis에 남은 큐별 canary가 현재 릴리스로 15분 안에 갱신됐는지. (2) 예약 실행기 생존 — RedBeat 분산 락이 잡혀 있고 5분 주기 canary 스케줄이 15분 안에 실제로 실행됐는지. 락만으로는 "누군가 dispatcher 자리를 잡았다"까지만 증명되므로 두 근거를 모두 요구한다. (3) 당일 발행 — KST 오늘 발행 예정으로 남은 슬롯 수와 08:00 이후 공개된 글 수. (4) 마지막 야간 생성 배치 `OperationRun`의 나이(26시간). 판정만 읽으려면 `GET /api/v1/admin/watchdog/pipeline`을 같은 토큰이나 admin 키로 호출한다.

**알림 규칙과 채널.** `control`·`content` 큐 정지, 예약 실행기 정지, 08:30까지 당일 발행 0건 중 하나라도 성립할 때만 Slack을 보낸다. notification outbox를 쓰지 않고 webhook으로 직접 보낸다 — 그 outbox를 비우는 Worker가 죽었을 수 있다. 수신자는 사실별로 나뉜다: 실행기·대기열 정지는 개발 채널(`SLACK_WEBHOOK_URL_DEV`), 당일 발행 0건은 운영 채널(`SLACK_WEBHOOK_URL`)이다. **이 부품만의 예외로 개발 채널이 설정돼 있지 않으면 인프라 알림을 HOLD로 남기지 않고 운영 채널로 내려보낸다** — 침묵이 바로 이 부품이 막으려는 실패다([알림 정책](slack-notification-policy.md)). 같은 원인 집합은 수신자별로 KST 시간당 한 건이고, 조건이 사라지면 복구 한 건이 같은 채널로 간다. 정상 상태·보조 큐 지연·일부만 발행된 상태·예산 안의 자동 재시도는 알리지 않는다. 문구는 `무슨 문제인지 → 고객 영향 → 지금 할 일` 순서이며 개인정보와 내부 코드가 없다.

**경보를 받으면 먼저 볼 세 가지.** (1) Cloud Run `reputation-beat`·`reputation-worker`가 Ready이고 최근 재시작·OOM이 없는지. (2) Memorystore Redis 연결과 `GET /api/v1/admin/watchdog/pipeline`의 `beat_lock_held`·`beat_last_schedule_run_at`·`stale_queues`. (3) 운영센터 `/operations`의 오늘 발행 큐 — 남은 슬롯이 자동 복구(재시도 중)인지 사람의 일인지. 복구 뒤 다음 하트비트(최대 5분)에서 복구 메시지가 오는지 확인한다.

**토큰 교체.** `PIPELINE_WATCHDOG_TOKEN`은 API와 Cloud Scheduler가 같은 값을 읽는다. **비어 있어도 API는 정상 부팅한다** — 감시 토큰이 없다고 배포를 막으면 이 부품이 잡으려는 바로 그 정지를 배포가 스스로 만들기 때문이다. 대신 부팅 로그에 경고가 남고 `GET /api/v1/admin/watchdog/pipeline`의 `token_configured`가 `false`가 된다(그 상태에서 스케줄러 호출은 401이고 외부 감시는 실제로 꺼져 있다. admin 키 호출은 계속 된다). 교체할 때는 새 버전을 올린 뒤 API를 재배포해 새 값을 읽게 하고, 그 다음 `terraform apply`로 두 스케줄러 job의 헤더를 갱신한다(순서가 반대면 그 사이 호출이 401로 떨어진다).

```bash
gcloud secrets versions add PIPELINE_WATCHDOG_TOKEN --project mso-platform-481505 --data-file=<(openssl rand -hex 32 | tr -d '\n')
bash scripts/deploy.sh api
terraform -chdir=terraform apply -target=google_cloud_scheduler_job.watchdog_heartbeat -target=google_cloud_scheduler_job.watchdog_publish_check
```

첫 배포 전 준비: secret 컨테이너와 값을 먼저 만든다(`scripts/setup-gcp.sh`가 컨테이너를 생성한다). Cloud Run 주입은 두 경로 모두 준비돼 있다 — `scripts/deploy.sh`의 backend 필수 시크릿 목록과 `terraform/secretmanager.tf`의 `local.app_secret_env`.

## 검증할 증거

| 층 | 완료 판단 |
|---|---|
| 소스 | PR/main CI, 실제 배포 소스 Git SHA와 이미지 digest 대응 |
| 런타임 | 5개 서비스 Ready, 의도한 revision에 트래픽, 환경·secret 참조 보존 |
| DB | Alembic 현재 revision과 이미지의 expected head 일치 |
| 작업 | 현재 릴리스의 7개 큐 canary, 필수 task routing·priority, RedBeat schedule `2026-09-07.2`, queue wait |
| API / Admin | health와 로그인 표면 정상(LB 경로 `/api/v1/health/live`의 `release`가 배포 SHA — `deploy.sh`가 자동 확인), 인증·오류 경계 유지 |
| 병원 공개 표면 | 홈페이지와 대표 글, 이미지 SSR, 정확한 병원·canonical·공개 주소 |
| 크롤러 표면 | host별 sitemap, robots, llms.txt의 404/503·no-store, 부분 sitemap 금지, 구조화 데이터와 공개 URL 범위 |
| 복구 | IndexNow intent와 provider usage spool의 1분 drain, retry 상한·중복 억제·정상 무알림 |
| V0 백그라운드 | V0 미완료 병원의 프로파일·허브·주소 조건에 따른 공개, 같은 run의 짧은 구간 이어가기, 완료 슬롯 비재구매, 만료 RUNNING lease 인수, ACTIVE·PAUSED 상태 보존 |
| 공개 본문 | 현재 strict public gate, 후보 hash·근거·검수 상태와 공개 API 결과 일치 |
| 기존 이미지 | 실제 바이트·내용 hash·주제 hash·정책 버전 인증과 프록시 URL 버전 일치 |
| 로그 | 새 revision의 실제 오류와 작업 실패 여부, control/background queue wait; 조회 시점·범위 기록 |

병원 헬스는 `https://{병원 공개 호스트}/.well-known/reputation-health`다. HTTP 200뿐 아니라 `hospital_id`, `slug`, `canonical_host`, `release`(Cloud Run에서는 `K_REVISION`)가 해당 병원·배포와 맞아야 한다. 기본 주소와 자기 도메인을 모두 사용하는 운영 집합을 대상으로 확인한다.

`python -m app.utils.production_readiness`와 대기형 `python -m app.utils.wait_production_readiness`는 배포 이미지·운영 연결에서 실행하는 검사다. 공개 페이지 200만으로 DB 연결·스케줄 실행·실제 외부 AI 생성 성공까지 증명하지 않는다. 모델·이메일·Slack의 실제 호출은 별도의 명시된 검증 범위로 기록한다.

Worker는 `control,default,content,sov,reports,leadgen,certificates` 7개 큐를 하나의 서비스에서 소비한다. 2026-09-09 체크포인트에서 `content` 큐에 `recertify_published_content_image`(공개 글 제목 편집 후 이미지 재인증, 유료 호출 (글, 주제)당 최대 3회)와 `fetch_channel_source`(프로필의 공식 채널 URL을 근거 자료로 가져오기, 자료당 3회 예산 뒤 인시던트) 두 task가 추가됐다. 둘 다 `production_readiness.EXPECTED_TASKS`에 등록되어 있고 beat 스케줄은 바뀌지 않았다(재시도 sweep은 기존 `autonomous_recovery.reconcile`·자료 처리 복구 sweep 안에서 돈다). 발행·캐시 복구·자율 복구가 control priority 0으로 들어가고 IndexNow·usage spool은 낮은 우선순위다. control canary를 포함해 큐별 최신 canary가 현재 release를 가리키는지 확인한다. control은 전용 Worker가 아니므로 부하 중 30초 기준을 충족했는지는 queue wait 구조화 로그로 따로 증명한다.

RedBeat `2026-09-07.2`에는 IndexNow retry와 provider usage spool drain이 매 1분 추가된다. 영속 스케줄 재조정 뒤 등록 이름·task route·서명 목적이 새 이미지와 맞는지 확인한다. 정상 drain·복구를 Slack 메시지로 시험하지 말고 DB 실행 상태와 구조화 로그를 사용한다. 2026-09-07~08 기존 이미지 인증은 주기 작업이 아니라 완료 후 제거한 일회성 Job/CLI였다. 최종 실패나 사람이 결정할 예외만 [알림 정책](slack-notification-policy.md)에 따라 확인한다.

2026-09-08 운영 전환 runtime `ede3d8f5a8adec849c987d21c1491afef03edbca`의 [PR #86 CI](https://github.com/wjlee930501/reputation/actions/runs/34142628859)는 9/9 통과했다. Backend는 3,095 passed, 0 failed, 0 skipped, 17 warnings, coverage 83.03%였고 Admin·Site 검사와 컨테이너 빌드도 통과했다. 운영에서는 5개 서비스·3개 영속 Job, DB head, 7개 큐 readiness, 공개 115건과 실제 이미지 바이트를 확인했다. 이 완료 범위와 남은 장기 WATCH는 [후속 구현 기록](../reviews/2026-09-07-purpose-autonomy-efficiency-implementation.md)에 분리한다.

2026-09-09 체크포인트 1 runtime `4bd1e0312a9178ad53c2f1065ec42798d81d7c7c`(PR #91, CI 9/9)는 `scripts/deploy.sh all`로 마이그레이션(0070) → Worker → RedBeat 재조정 → Beat → readiness(7개 큐 canary, `recertify_candidate_count 0`, `null_noise_hash_approvals 8`) → API/Site/Admin 순으로 배포했고, 8개 병원 헬스·대표 글·이미지 200과 새 리비전 오류 0건을 확인했다. 상세는 [체크포인트 1 기록](../releases/2026-09-09-checkpoint-1.md).

## 롤백과 문서 변경

### 0084 목적 중심 전환의 compatible-reader 롤백

`41f61d6f2f47459e3135dac66e430e454a1f1735` baseline은 확장 스키마와 기존 입력을
characterization하는 용도다. 이 버전은 이미지 필수, `schedule_set` 공개 gate, 참고자료 나이
gate를 갖고 있으므로 0084 전환 뒤 public/read traffic의 롤백 대상이 아니다. Tasks 1~14를
검증한 뒤 Task 15 cleanup 전에 만든 `compatible_reader_sha`의 full SHA와 이미지 digest만
public/read 롤백 좌표로 사용한다.

롤백 순서는 고정한다. 먼저 Admin mutation traffic, 모든 mutation Worker, Beat를 0으로 내려
in-flight transaction이 없는지 확인한다. 그 다음 API public/read와 Site만 검증된 compatible
reader 이미지로 전환한다. expanded DB를 그대로 두며 `alembic downgrade`는 실행하지 않는다.
compatible reader에서 no-image, `schedule_set=false`, 오래된 승인 참고자료, pending candidate가
있는 항목을 읽어 active approved body만 반환하는지 확인한다. 이 상태에서는 baseline Worker,
Beat, Admin을 시작하지 않는다. revision-aware writer를 복구하고 post-drain reconciliation과
mirror/pointer parity 100%를 다시 확인한 뒤에만 Worker → Beat → Admin mutation 순으로 재개한다.

로컬/CI 리허설은 추적되는
`scripts/integrity_rehearsal/fixtures/compatible-reader.json` schema v2 manifest를 fail-closed
입력으로 사용한다. 이 파일에는 원본 pre-Task-15 checkpoint와 그 직계 자식인 read-only
hotfix의 full SHA, 정확한 3개 변경 경로, 검증 evidence hash가 모두 있어야 한다. harness는
hotfix `sourceSha`의 parent가 `originalCheckpointSha`인지, diff가 allowlist와 정확히 일치하는지
검증하고 그 SHA를 `git archive`로 build한 뒤 image tag/ID/digest를 별도 evidence에 기록한다.
체크포인트가 없거나 build 뒤 old/new/compatible 정체성이 겹치면 리허설을 시작하지 않는다.
외부 provider, GCS, Slack, DNS는 isolated fake boundary 밖으로 호출하지 않으며 그 미검증 범위를
리허설 결과에 남긴다.

0085를 포함한 backend/API/Worker/Beat/migrate/all 배포 전에는 운영 DB의 legacy publish
backlog와 변환 가능한 legacy task incident를 읽기 전용으로 확인한다. 운영 `DATABASE_URL`을
주입하고 `scripts/deploy.sh`를 실행한다.
운영 Cloud SQL은 사설 IP뿐이라 작업 PC에서는 직접 붙지 못한다. 로컬 `DATABASE_URL`이 비어 있으면
`deploy.sh`가 같은 읽기 전용 확인을 VPC 안의 `reputation-migrate` Job(그 시점에 배포돼 있는 이미지 그대로,
migrate·이미지 갱신 전)으로 실행하고 로그의 결과 한 줄(`jsonPayload`)을 증거 파일로 남긴다. 통과 기준은 같고,
Job이 돌지 못하거나 결과를 읽지 못하면 배포를 시작하지 않는다(2026-10-10).
`CONTENT_PUBLISH_RECOVERY_DATABASE_URL`은 격리된 integration test fixture 전용이다. preflight는
`CONTENT_PUBLISHED` open transport와 unapplied
`SENT`, `convertible_legacy_incidents`를 각각 세어 세 값이 모두 0일 때만 통과한다.
`unknown_legacy_incidents`는 자동 변환할 근거가 없는 레코드를 사실대로 보고하지만 배포를
자동 차단하지 않는다. 결과 JSON은 기본적으로
`.omo/evidence/deploy-preflight/legacy-publish-retirement-<revision>-<UTC>.json`에 남는다. 한 건이라도
남거나 DB/config 확인이 실패하면 배포를 시작하지 않는다. 운영 backlog는 실제 preflight를 실행하기
전까지 UNKNOWN으로 취급하며 Slack 발송으로 drain을 시험하지 않는다.

preflight가 변환 가능한 legacy incident를 보고하면 배포 전에 아래 maintenance CLI를 실행한다.
이 명령은 legacy incident, operation run, 알림 outbox를 삭제하지 않고 정규 incident 변환과
감사 로그만 남긴다. 같은 identity의 중복 실패는 가장 오래된 레코드만 정규 incident로 변환하고
나머지는 `ACKNOWLEDGED`/`recovered_at=NULL`로 supersede한다. 정확히 같은 identity의 뒤이은 성공만
recovery 근거로 사용하며 다른 월의 성공은 현재 실패를 회복시킨 것으로 취급하지 않는다.

```bash
cd backend
DATABASE_URL='postgresql+asyncpg://operator:…@…/…' \
  PYTHONPATH=. uv run python -m app.utils.reconcile_legacy_task_incidents
```

결과는 `status=APPLIED`와 `converted`, `superseded`, `recovered`, `unknown` count를 출력한다.
같은 명령을 다시 실행해 앞의 세 mutation count가 모두 0인지 확인한 뒤 read-only preflight를
재실행한다. `unknown`은 임의로 성공/회복 처리하지 말고 원본 incident와 operation run을 보존한 채
운영자가 target/period 근거를 별도로 확인한다. 이 과정은 새 Slack/notification delivery를 만들지
않으며, 실행 전후 incident/run/outbox count와 감사 로그를 배포 evidence에 함께 보관한다.

```bash
DATABASE_URL='postgresql+asyncpg://read-only-user:…@…/…' \
  bash scripts/deploy.sh all
```

```bash
bash scripts/deploy.sh rollback
```

일반 롤백은 `.deploy-rollback`에 저장된 revision으로 **트래픽을 복귀**한다. 0084 목적 중심 전환에는
위 compatible-reader 절차를 먼저 적용하며 이 일반 명령으로 baseline Worker/Beat/Admin까지 함께
복구하지 않는다. 이미 적용된 DB 마이그레이션은 되돌리지 않는다. 스키마가 구버전과 호환되는지
먼저 판단하며 무조건 `alembic downgrade`하지 않는다. 롤백 뒤에도 현재 큐·API·공개 표면을 다시
확인한다.

문서만 바뀐 릴리스는 기존 런타임이 그대로임을 기록한다. 문서 Git SHA를 기존 이미지의 소스 SHA로 바꿔 적거나 불필요한 전체 재배포를 수행하지 않는다.

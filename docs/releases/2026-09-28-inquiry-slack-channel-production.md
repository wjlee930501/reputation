# 2026-09-28 — 도입문의 Slack 채널 분리 운영 배포

공개 도입문의 접수 알림을 `#mkt-reputation`에서 뉴비짓 도입문의 채널 `#noti-도입문의-뉴비짓`으로
옮기고, 신청 요약과 `[Re:putation]` 출처 표시를 붙였다. 백엔드(API·Worker·Beat)만 세 번 배포했다.
이 문서를 더한 커밋은 배포 기록이며 런타임 이미지의 소스 SHA가 아니다.

| # | 시각(KST) | 소스(main) | 내용 |
|---|---|---|---|
| 1 | 14:47~14:50 | `2d7471e4fa14aacba8a64340af51c9778347a9bb`(PR #164) | 전용 웹훅 설정·요약 형식. secret이 없어 기존 채널로 발송 |
| 2 | 14:57~14:59 | 〃 | `SLACK_WEBHOOK_URL_INQUIRY` secret 생성 뒤 재배포해 연결 |
| 3 | 16:11~16:15 | `7266674b271c6620979f2f0f49dc9bb4bb7b1d89`(PR #165) | 광고 쿼리가 붙은 유입 경로 표시 회귀 수정 |

세 PR 모두 CI 통과(#164·#165 10/10), GPT 6 Astra(Codex) 교차 검토 승인. PR #166은 Terraform
import 블록만 더해 런타임 배포가 없다. **DB migration·RedBeat·Site·Admin 변경은 없다.**
DB head는 `0080_lead_diagnosis_supersede` 그대로다.

## 배포 전 운영

실행 직전 운영은 main `81691e4`(PR #162, admin-auth 세션 폐기 503 수정)였다. 9월 23일 기록 뒤
한 번 더 배포된 상태이며 그 배포의 기록은 없다.

| 서비스 | 배포 전 리비전 |
|---|---|
| `reputation-api` | `reputation-api-00203-928` |
| `reputation-worker` | `reputation-worker-00192-pd2` |
| `reputation-beat` | `reputation-beat-00187-6hl` |

`.env.production`을 운영 API 리비전의 평문 env와 대조했다. 로컬에만 있는 키 4개
(`SENTRY_DSN`·`INQUIRY_SMS_PROVIDER`·`INQUIRY_SMS_SENDER_NO`·`NHN_SMS_APP_KEY`)는 모두 빈 값이고
나머지 값은 같았다. 저장소 루트의 추적되지 않은 파일 때문에 `deploy.sh`가 배포 기준을 고정하지
못하므로 `REPUTATION_RELEASE_REVISION`에 main SHA를 명시했다. 백엔드 이미지 빌드 컨텍스트는
`backend/`라 그 파일은 이미지에 들어가지 않는다.

## Secret

`SLACK_WEBHOOK_URL_INQUIRY`(버전 1)를 gcloud로 만들고 `reputation-sa`에 secretAccessor를 붙였다.
Slack 앱 `우진_ClaudeCode_Noti`의 Incoming Webhook이며 대상은 `#noti-도입문의-뉴비짓`(C0C4QRE4TGV)이다.
`deploy.sh`는 이 secret을 선택 항목으로 mount한다 — 없거나 비어 있으면 도입문의 알림은
`SLACK_WEBHOOK_URL`로 간다. Terraform은 PR #166의 import 블록으로 다음 apply 때 입양한다
(변수를 쓴 import 블록이라 Terraform 1.6 이상 필요).

## 리비전과 이미지

| 배포 | 서비스 | 리비전 | 이미지 digest |
|---|---|---|---|
| 1 | api / worker / beat | `00204-7hg` / `00193-zwc` / `00188-cbp` | `sha256:7dcb02c1e32a62d85167a197bc530e212325f17e378a1b92d4c3a24ac7a8fee4` |
| 2 | api / worker / beat | `00205-6z4` / `00194-2d4` / `00189-l5c` | 〃(같은 소스) |
| 3 | api / worker / beat | `00206-8hf` / `00195-7dt` / `00190-bqz` | `sha256:c10ec702415b7892cac251a32ac084548f9173e536d9b4bc2faaacaa921c2f94` |

세 배포 모두 runbook 순서(마이그레이션 → Worker → RedBeat 재조정 → Beat → readiness gate → API)를
따랐고 migrate·redbeat-reconcile·production-readiness Job이 모두 성공했다. 현재 트래픽 100%가
배포 3 리비전이며 백엔드 3종의 `REPUTATION_RELEASE_REVISION`이 `7266674`다.

## 운영 사후 검증

- **실제 접수 2건**: Aside 브라우저로 `https://reputation.motionlabs.kr/contact`에 시험 문의를 제출했다.
  - 15:52 `[테스트] Re:putation 알림점검의원` — 새 채널 도착. 유입 경로가 `(기타)`로 표시됨 →
    사이트가 광고 유입 값을 `source_path` 쿼리로 덧붙이는데(`decorateSourcePath`) 경로 전체를
    비교했기 때문. PR #165로 수정.
  - 16:16 `[테스트2] Re:putation 알림점검의원` — 새 채널 도착, 유입 경로 `/contact`, 연락처 마스킹,
    자동 처리 `초도 노출 진단 자동 시작`.
- **헬스**: `https://reputation.motionlabs.kr/api/v1/health/live` 200, 랜딩 200.
- **로그**: 배포 3 리비전 `severity>=ERROR` 0건(2시간 창).

## 이번 배포가 바꾼 계약

- 공개 도입문의 접수 알림(`notify_lead_created`)만 `SLACK_WEBHOOK_URL_INQUIRY`로 간다. 무료 진단 접수·
  개인정보 파기 실패 등 다른 알림은 운영 채널에 남는다. 새 웹훅도 같은 호스트 허용 목록 검사를 받는다.
- 채널에 뉴비짓의 다른 제품 문의가 함께 들어오므로 제목·fallback text에 `[Re:putation]`을 붙인다.
- 요약은 처리방침의 Slack 국외 이전 고지 범위만 담는다: 병원명·진료과·지역·마스킹 연락처·
  유입 경로(`/`, `/contact`, `/#contact`, `/#lead`만, 광고 쿼리 제외, 그 밖은 `(기타)`)·접수 시각·
  자동 처리 결과. 핵심 키워드·문의 본문(주소·원장 성함·홈페이지)·담당자 성함은 싣지 않는다 —
  핵심 키워드를 넣으려면 처리방침의 Slack 이전 항목을 먼저 고친다.

## 남은 일

- 시험 리드 2건(`[테스트] …`, `[테스트2] …`)과 각각 자동 생성된 INTERNAL 초도 진단이 운영 DB에 있다.
  Admin 상담 요청에서 정리한다. 안내 문자는 문자 설정이 꺼져 있어 발송되지 않았다.
- 새 채널에 시험 알림 2건이 남아 있다.
- 웹훅 URL이 작업 대화에 노출됐다. 교체하려면 새 URL을 secret 새 버전으로 넣고 API만 재배포한다.

## 롤백

`.deploy-rollback`은 배포 3 직전(배포 2 리비전) 좌표를 담는다.

```bash
bash scripts/deploy.sh rollback
```

- 채널 분리 자체를 되돌리려면 위 '배포 전 리비전'으로 트래픽을 옮긴다. migration이 없어 DB는 손대지 않는다.
- 코드는 두고 채널만 되돌리려면 `SLACK_WEBHOOK_URL_INQUIRY` secret을 삭제하고 API를 재배포한다.
  `deploy.sh`는 없는 선택 secret은 건너뛰지만, 있는데 최신 버전이 비활성이면 배포를 거절한다.

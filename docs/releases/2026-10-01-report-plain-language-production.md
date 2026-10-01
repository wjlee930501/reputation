# 2026-10-01 — 미결 PR 정리·원장 보고서 개편 운영 배포

열려 있던 PR을 검토해 머지하고(#163·#168·#177·#179·#181~#194), 원장용 월간 보고서를 쉬운 말·새
디자인으로 바꿔 5개 서비스에 배포했다. 이어 Backend만 한 번 더 배포하고, 측정이 끝난 9월 보고서
5곳을 숫자 그대로 새 디자인 버전으로 다시 만들었다. 이 문서를 더한 커밋은 배포 기록이며 런타임
이미지의 소스 SHA가 아니다.

| # | 시각(KST) | 소스(main) | 대상 | 내용 |
|---|---|---|---|---|
| 1 | 19:13~ | `a8a86b57`(PR #193까지) | 5개 서비스 | 참고자료 출처 가드(#177·#183·#185, migration `0082`), 생성 claim·재시도(#179·#182·#184·#186·#188), 운영자 문구(#181·#187), SoV 정책 전환 알림(#163), 필수 문구 원문(#168), 강제 tool_choice 거절 모델 auto 재시도(#189), 모델 기본값(#190), Next.js 16.3.8 GHSA-vcvr(#191), 원장 보고서 개편·템플릿 갱신(#192·#193) |
| 2 | 배포 1 뒤 | `7e893596`(PR #194) | API·Worker·Beat | 템플릿 갱신 숫자 대조가 저장값에서 옮긴 반복 관측 횟수를 새 숫자로 보지 않음 |

**DB head는 `0080_lead_diagnosis_supersede` → `0082_add_content_reference_checks`**(0081 withheld 상태,
0082 `content_items.reference_checks`, 둘 다 추가형). 배포 1의 migrate Job이 코드보다 먼저 적용했다.
보류: #161(필수 문구 SOFT 강등)은 대표 판단 대기로 머지하지 않았다 — #168이 같은 문제를 다른 방식으로 해결했다.

## 배포 전 운영과 env

| 서비스 | 배포 전 | 배포 1 | 배포 2 |
|---|---|---|---|
| api | `00211-fz7` | `00212-dh6` | `00213-tgk` |
| worker | `00200-ffx` | `00201-q6z` | `00202-kkm` |
| beat | `00195-jjz` | `00196-9bz` | `00197-7k2` |
| site | `00142-xh2` | `00143-mvd` | — |
| admin | `00098-lbl` | `00099-fc4` | — |

작업 PC `.env.production`(9/20)은 운영과 달랐다 — `CLAUDE_MODEL`·`OPENAI_MODEL_QUERY`가 10/1 02:01 KST
운영 변경 이전 값이었고, 운영에만 있는 6개 키(`SOV_MONTHLY_COHORT_LIMIT`·`SOV_TRACKING_SET_N_DEFAULT`·
`SOV_MONTHLY_WINDOW_START_DAY`·`OPENAI_IMAGE_ASPECT_RATIO`·`GOOGLE_IMAGE_MODEL`·`OPENAI_IMAGE_MODEL`)가
없었다. 운영 API 리비전의 평문 env로 배포용 파일을 다시 만들고, 빈 값 4개(`SENTRY_DSN`·
`INQUIRY_SMS_*`·`NHN_SMS_APP_KEY`)는 그대로 두었다. `deploy.sh`는 `PUBLIC_DOMAIN`·`ADMIN_DOMAIN`
셸 변수가 필요하다(첫 시도는 사전 검사에서 멈춰 변경 없음).

## 사후 검증

- **env**: 배포 전후 5개 서비스 env 비교 — 백엔드 3종은 `REPUTATION_RELEASE_REVISION`만 바뀌었고 Site·Admin은 변화 없음.
- **readiness**: `reputation-production-readiness` 성공 — 현재 배포 버전의 모든 작업 큐 준비.
- **공개 병원 9곳**: `.well-known/reputation-health` 전부 200, `slug`·`canonical_host` 일치, `release` `reputation-site-00143-mvd`.
- **랜딩·Admin**: `/` 200, `admin.reputation.motionlabs.kr/login` 200.
- **로그**: 배포 1 새 리비전 5개 `severity>=ERROR` 0건(배포 직후 1시간 창).

## 9월 보고서 템플릿 갱신

[템플릿 갱신 절차](../ops/monthly-template-refresh.md)대로 `reputation-migrate` Job override로 실행했다.

- 첫 사전 확인: BLOCKED 4(측정 미완료 `RECOVERY_PENDING`), DIFF 5(새 템플릿이 저장된
  `observation_adequacy`의 '150번 중 150번'을 처음 적어 생긴 대조 오탐) → PR #194로 고쳐 배포 2.
- 재확인 PASS 5 → `execute --confirm`. 행복드림·강심장내과·마포성모탑·연세속시원·노원탑365가
  v2(`TEMPLATE_REFRESH`, v1 보존)로 생성됐고, 전체 `postcheck`에서 5곳 모두 PASS(저장 숫자·토킹 포인트
  숫자·옛/새 원장 PDF 숫자 사실 동일).
- Job 기본 task timeout 300초에 걸려 다섯 번째 병원의 실행 직후 사후 확인 전에 끊겼다(새 버전은 생성됨).
  남은 병원은 `gcloud run jobs execute ... --task-timeout=1800s`로 돌린다.
- **남은 일**: 측정 미완료 4곳(장편한외과·신기한속내과연합·장앤김더나은속내과·서울W내과 위례점)은
  10월 8일 00:00 KST 이후 같은 절차. 새 버전은 AE가 원장에게 다시 전달해야 반영된다.

## 롤백

`.deploy-rollback`(배포 2 기준: api `00212-dh6`·worker `00201-q6z`·beat `00196-9bz`)로 `bash scripts/deploy.sh
rollback`. 배포 1 이전으로 되돌리면 트래픽만 바뀌고 `0081`·`0082`는 남는다(추가형이라 옛 코드와 호환).
템플릿 갱신으로 만든 v2는 append-only라 지우지 않는다 — v1과 전달 기록은 그대로 있다.

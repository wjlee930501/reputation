# 2026-09-23~24 — 통합 정합성 보완·파비콘 운영 배포

세 번 배포했다. 첫 번째가 [배포 준비 문서](2026-09-23-integration-hardening-deploy-plan.md)의
실행이고, 뒤의 둘은 Site만 바꾼 파비콘 배포다. 이 문서를 더한 커밋은 배포 기록이며
런타임 이미지의 소스 SHA가 아니다.

| # | 시각(KST) | 소스(main) | 대상 | 내용 |
|---|---|---|---|---|
| 1 | 09-23 23:26~23:33 | `38cff0e98bbbc2429932dd4272d555ded4269655`(PR #154) | 5개 서비스 | #153 통합 정합성 보완. 런타임 코드는 `6ca8d24`와 같다(#154는 문서) |
| 2 | 09-24 03:21 | `a649cc1`(PR #155) | Site | 플랫폼 파비콘을 새 브랜드 심볼로 교체 |
| 3 | 09-24 07:20 | `fad966d`(PR #156) | Site | 병원 페이지 탭 아이콘을 병원별 모노그램으로 |

세 PR 모두 CI 9/9 통과. 로컬 사전 가드 `make db-budget-guard`(75/80)·`make copy-guard` 통과.
**DB migration·RedBeat·Terraform 변경은 없다.** DB head는 `0080_lead_diagnosis_supersede` 그대로다.

## 배포 전 실제 운영은 준비 문서와 달랐다

준비 문서와 runbook은 운영을 `300a663`(PR #143)으로 적었으나, 실행 직전 조회한 운영은
그 뒤 다른 세션에서 두 번 배포된 상태였다. 그 두 배포의 기록은 없다.

| 서비스 | 배포 전 리비전 | 이미지 태그 | 소스 |
|---|---|---|---|
| `reputation-api` | `reputation-api-00200-4c5` | `reputation:20260923-102858` | `62caa56`(PR #150, `REPUTATION_RELEASE_REVISION`) |
| `reputation-worker` | `reputation-worker-00189-4kt` | 〃 | 〃 |
| `reputation-beat` | `reputation-beat-00185-qfk` | 〃 | 〃 |
| `reputation-admin` | `reputation-admin-00095-9jp` | `admin:20260923-102858` | 같은 시각 빌드 |
| `reputation-site` | `reputation-site-00139-fnn` | `site:20260923-175849` | 미기록(랜딩 병합 #151·#152 시각과 맞음) |

따라서 배포 1이 실제로 새로 올린 런타임 변경은 주로 PR #153이다.

## 배포 1 — `.env.production` 재구성

이 작업 PC의 `.env.production`(2026-08-10)은 운영과 달랐다. `deploy.sh`는 `--env-vars-file`로
**기존 env를 전부 지우고 파일 내용만 적용**하므로 그대로 배포하면 운영 설정이 바뀐다.

- 모델 5개가 `vendor/` 접두사 없는 옛 값이라 preflight가 먼저 거절했다(`CLAUDE_MODEL` 등).
- `LEAD_CONSENT_VERSION`이 운영 값과 달랐다.
- 운영에만 있는 키 11개(`COST_GUARD_*`, `SOV_*`, `DB_CONNECTION_MODE`, 이미지 모델 등)가 없었다.

그래서 운영 `reputation-api` 리비전의 평문 env 47개를 그대로 옮겨 파일을 다시 만들었다
(비밀은 Secret Manager 참조라 파일에 없다. 옛 파일은 `.env.production.bak-20260923`, 둘 다 git 미추적).
API·Worker·Beat의 평문 env는 `SERVICE`만 달랐다. Terraform env 드롭 preflight가 요구한
`INQUIRY_SMS_PROVIDER`·`INQUIRY_SMS_SENDER_NO`·`NHN_SMS_APP_KEY`는 운영에도 없었으므로
빈 값(`KEY=`)으로 적어 현재 상태를 유지했다 — **도입문의 안내 문자는 배포 전과 같이 꺼져 있다.**

배포 후 API·Worker·Beat의 env(평문 값과 secret 참조)를 배포 전과 비교해
`REPUTATION_RELEASE_REVISION`만 바뀐 것을 확인했다.

> 다른 PC에서 배포할 때도 같은 위험이 있다. 배포 전에 로컬 `.env.production`의 키·평문 값을
> 운영 리비전의 env와 대조한다.

## 배포 1 — 이미지와 런타임

이미지 태그 `20260923-232611` 3종(`reputation`·`site`·`admin`)을 같은 소스에서 빌드했다.
digest는 각 리비전의 `status.imageDigest`(Artifact Registry `reputation` 저장소)다.

| 서비스 | 배포 후 리비전 | 이미지 digest |
|---|---|---|
| `reputation-api` | `reputation-api-00201-g5w` | `sha256:249e1bdd579a9c3a6d6eddcf25dd3c4abf13a707a8a7cc561bc07e9e336fefad` |
| `reputation-worker` | `reputation-worker-00190-f9b` | 〃 |
| `reputation-beat` | `reputation-beat-00186-8sg` | 〃 |
| `reputation-site` | `reputation-site-00140-h7t` | `sha256:e8f153c086322c5e35ea947c8ecedffc9409730fb1dcd424aa660e04893104cf` |
| `reputation-admin` | `reputation-admin-00096-rrh` | `sha256:afde602fe38fadba073adbc95de5ee85d86a6a79838d467151603559e3cefaa5` |

순서는 runbook과 같다 — 롤백 좌표 → 이미지 3종 → 마이그레이션(`reputation-migrate-lmlkw`,
변경 없음) → Worker → RedBeat 재조정(`reputation-redbeat-reconcile-p6qxz`) → Beat →
readiness gate(`reputation-production-readiness-zkwbf`) → API → Site → Admin.
5개 서비스 모두 Ready, 트래픽 100%가 새 리비전, 백엔드 3종의 `REPUTATION_RELEASE_REVISION`이 `38cff0e`다.

## 배포 1 — 운영 사후 검증

- **작업 큐**: readiness Job 성공 — 현재 배포 버전의 모든 작업 큐 준비 확인.
- **공개 병원 9곳**: `.well-known/reputation-health` 전부 200. `slug`·`canonical_host`가
  각 병원과 맞고 `release`가 `reputation-site-00140-h7t`다.
- **랜딩·Admin**: `/` 200, `admin.reputation.motionlabs.kr/login` 200.
- **로그**: 새 리비전 5개 `severity>=ERROR` 0건(23:33 KST 조회, 1시간 창).

## 배포 2·3 — 파비콘

- **배포 2**(`reputation-site-00141-7g9`, `site:20260924-032152`,
  `sha256:dbec165a782b0fa10d3f5182ec2d41887364b7e7256555a551779f4d0f5e6cd1`): `app/favicon.ico`를 새 주황
  심볼(16/32/48)로 바꾸고 `icon.png`(512)·`apple-icon.png`(180)를 더했다. 운영 `/favicon.ico`가
  저장소 파일과 바이트 일치, 세 파일 200을 확인했다.
- **배포 3**(`reputation-site-00142-xh2`, `site:20260924-072009`,
  `sha256:055897f4bd25e5d39c95272dec2916f529214b298712910bee7494acd2795a8a`): 병원 페이지가 플랫폼 심볼을
  내보내지 않도록 `/favicon/{slug}`가 대표색 바탕에 병원명 첫 글자를 얹은 PNG를 만든다.
  `app/favicon.ico`는 Next가 하위 세그먼트에도 항상 링크하므로 `public/`으로 옮겼다.
  - 병원 9곳(커스텀 도메인 6·기본 주소 3) 모두 모노그램 링크 2개(icon·apple)만 있고
    플랫폼 아이콘 링크가 없다. 아이콘 PNG 9개가 200 `image/png`이며 실제 렌더를 눈으로 확인했다.
  - 랜딩은 `icon.png`·`apple-icon.png` 링크 그대로, `/favicon.ico` 200.
  - 새 Site 리비전 `severity>=ERROR` 0건(30분 창).

## 이번 배포가 바꾼 계약

- PR #153의 계약은 CLAUDE.md 최신 배포 절에 적었다(갈음 진단 복구 409, 리드 진단 생성 멱등,
  원장용 PDF 내부 표식 거절, 백엔드 Admin 링크는 4개 탭 경로, NHN 문자 secret 조건부 조회).
- 병원 페이지 탭 아이콘은 `lib/clinic-favicon.ts` 규칙으로 자동 생성한다. 흰 글자는 대비 3:1
  이상일 때 쓰고, 번들 폰트(`site/assets/fonts`) 밖의 글자는 빈 모노그램이다. 병원 조회 실패 시
  플랫폼 심볼이 아닌 회색 중립 아이콘을 준다. 대표색·이름이 바뀌면 링크의 `v`가 바뀐다.

## 검증하지 않은 범위

- 준비 문서의 기능 확인(Admin `노출 진단 생성` 1건, 같은 요청 재제출 멱등, `값 고쳐 다시 만들기`,
  보고서 탭 V0 행, 랜딩 도입문의 1건 제출, 운영 센터 옛 링크)은 로그인·실제 제출이 필요해 하지 않았다.
- readiness `facts.inquiry_sms` 값은 Job 로그에서 따로 읽지 않았다. 문자 설정 키는 배포 전과 같이
  비어 있다.
- 운영 env의 `LEAD_CONSENT_VERSION`은 `v1.2026-05`다(이번 배포는 값을 보존했다). 준비 문서의
  '남은 결정'은 동의 기록을 `v1.2026-08`로 적었으므로 처리방침 버전 결정 때 함께 맞춘다.
- 링크 없이 `/favicon.ico`를 직접 요청하는 크롤러는 병원 도메인에서도 플랫폼 아이콘을 받는다
  (미들웨어 matcher가 이 경로를 건너뛴다).

## 롤백

`.deploy-rollback`은 마지막 배포(배포 3) 직전 좌표만 담는다.

```bash
bash scripts/deploy.sh rollback
```

- 배포 3만 되돌리면 Site가 `reputation-site-00141-7g9`로 돌아간다(병원 페이지에 플랫폼 심볼).
- 배포 1 전체를 되돌려야 하면 위 '배포 전 리비전' 표의 5개 리비전으로 트래픽을 옮긴다.
  migration이 없으므로 DB는 손대지 않는다. 새 코드가 남긴 `CREATE_LEAD_DIAGNOSIS` OperationRun
  행은 옛 코드가 읽지 않아 무해하다.

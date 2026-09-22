# 2026-09-22 — 노출 진단 수동 생성 운영 배포

## 배포 기준

PR #143(기능)과 #142(직전 배포 기록)가 병합된 main tip
`300a6636aee6d5fb73141848f34189194a86c9e5`를 운영 5개 서비스에 배포했다.
기능 코드는 그 부모인 `c104bad4e3486658c51a69d4d83cd5ed5812c348`에 들어 있다.
이 문서를 추가한 커밋은 배포 기록이며 런타임 이미지의 소스 SHA가 아니다.

- PR #143 CI 10/10 통과(`rehearsal` 포함). rehearsal은 실제 큐를 돌리므로 새
  마이그레이션이 실DB에 적용되는 것도 여기서 한 번 확인됐다.
- 이미지 태그 `20260922-183731` 3종 모두 같은 소스에서 빌드했다.
- **DB head `0079_topic_swap_fallback` → `0080_lead_diagnosis_supersede`.**
  직전 배포(`8f40f49`)와 달리 이번에는 schema가 바뀐다.
- 로컬 사전 가드: `make db-budget-guard`(75/80), `make copy-guard` 통과.

배포 명령은 직전과 같다. `PUBLIC_DOMAIN`·`ADMIN_DOMAIN`은 `.env.production`이 아니라
셸 환경변수로 넘긴다(`scripts/deploy.sh:113`).

## 이미지와 실제 런타임

| 서비스 | 이미지 digest (Artifact Registry `reputation` 저장소) |
|---|---|
| API / Worker / Beat | `sha256:0850f78b3ac13a0e8a3885957c1e7ec2bd753261fe739a09629164cca46153cf` |
| Site | `sha256:06503a02915410d3e19f1de35086153370a110eeb71502b312ef5c2aaea502a5` |
| Admin | `sha256:6b2f95fd2bf2beb4d48e1b28d590bdaa5e095c46bd6d4983aebd8b79f1966438` |

| 서비스 | 배포 전 리비전 | 현재 리비전 |
|---|---|---|
| `reputation-api` | `reputation-api-00197-8vm` | `reputation-api-00198-r2k` |
| `reputation-worker` | `reputation-worker-00186-6f9` | `reputation-worker-00187-wsn` |
| `reputation-beat` | `reputation-beat-00182-t6q` | `reputation-beat-00183-ks8` |
| `reputation-site` | `reputation-site-00133-j7l` | `reputation-site-00134-x65` |
| `reputation-admin` | `reputation-admin-00091-wbc` | `reputation-admin-00092-zln` |

5개 서비스 모두 Ready이고 트래픽이 위 리비전에 있으며 image 태그 일치를 확인했다.
배포 순서는 runbook과 같다 — 롤백 좌표 → 이미지 3종 → 마이그레이션 → Worker →
RedBeat 재조정 → Beat → readiness gate → API → Site → Admin.

## 마이그레이션

`0080_lead_diagnosis_supersede`가 적용됐다. `lead_diagnoses`에 `superseded_at`,
`superseded_by_id`(self-FK, `ON DELETE SET NULL`)와 활성 조회용 부분 인덱스
`ix_lead_diagnoses_active_lead`를 더한다. Additive only이며 기존 행은 전부 NULL이라
활성으로 읽힌다 — 적용 전 동작과 같다.

readiness Job이 이미지의 expected head와 운영 DB의 current head를 비교하므로, 그
통과가 head 일치의 증거다(`reputation-production-readiness-vzlql`).

## 운영 사후 검증

- **작업 큐**: `reputation-production-readiness-vzlql` 실행 성공. 새 스키마 위에서
  현재 릴리스의 7개 큐 canary 준비 확인. RedBeat 저장 스케줄 정합성 복구 완료.
- **공개 병원 9곳**: `.well-known/reputation-health` 전부 200이고 `release`가 모두
  `reputation-site-00134-x65`다.
- **새 API 라우팅**: 인증 없는 `POST /api/v1/admin/lead-diagnoses`가 401이고, 존재하지
  않는 경로는 404다 — 라우트가 실제로 등록됐고 부수효과 없이 인증에서 막힌다.
- **Admin**: `/login` 200. `/diagnoses`는 `?redirect=%2Fdiagnoses`를 달고 로그인으로
  보낸다(모든 경로가 미들웨어에서 먼저 걸리므로 이것만으로 페이지 존재가 증명되지는
  않는다 — Admin 이미지 빌드가 정적 페이지 13개를 만든 것이 그 근거다. 직전 12개).
- **랜딩 회귀**: 200이고 `#lead` 앵커 4개가 그대로다.
- **로그**: 새 리비전 5개 모두 `severity>=ERROR` 0건(배포 후 20분 창).

## 이번 배포가 바꾼 계약

- 리드당 진단이 '1건'에서 **'활성 1건'**이 됐다. 옛 진단은 지우지 않고
  `superseded_at`·`superseded_by_id`로 갈음해 보존한다.
- 갈음된 진단은 두 claim(`_claim_for_execution`·`_claim_for_report`)과 선택 쿼리
  네 곳에서 모두 제외된다. `_exhausted_to_failed`도 건너뛴다. 운영자 큐·복구
  버튼에도 올라오지 않으며 보고서는 계속 열린다.
- Admin 네비에 `노출 진단 생성` 탭이 생겼다. 문의가 없는 병원도 진단하며 리드 행이
  함께 생기고 `privacy`는 False다.
- 상담 요청 화면에 `값 고쳐 다시 만들기`가 생겼다. 고객에게 나간 진단
  (`SENDING`·`SENT`·`FAILED`)은 갈음 대상이 아니다.

## 검증하지 않은 범위

- **새 화면을 로그인 상태로 눌러보지 않았다.** 배포 후 다음 둘이 남아 있다 —
  `노출 진단 생성`으로 1건 만들기, 기존 도입문의 1건을 `값 고쳐 다시 만들기`로
  교정해 **새 질의로 측정이 도는지** 확인하기. 두 번째가 이번 변경의 핵심이다.
- 갈음된 진단이 실제 운영 폴러에서 건너뛰어지는지는 통합 테스트로만 확인했다.
- 직전 배포에서 남긴 **도입문의 성공 경로**(폼 제출 → 진단 자동 생성 → 안내 SMS →
  Slack) 실제 1건 검증도 그대로 남아 있다.

## 롤백

`.deploy-rollback`에 배포 직전 5개 리비전을 기록했다(위 표의 '배포 전 리비전').

```bash
bash scripts/deploy.sh rollback
```

트래픽만 되돌린다. **마이그레이션은 되돌리지 않는다.** `0080`은 additive이고 옛
코드는 두 컬럼을 모르므로, 스키마를 그대로 둔 채 트래픽만 복귀해도 정상 동작한다.
`alembic downgrade`를 실행할 이유가 없다.

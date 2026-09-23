# 2026-09-23 — 통합 정합성 보완 배포 준비 (미배포)

상태: **배포 전 준비 문서다.** 이 문서가 병합된 시점에 운영은 여전히 `300a663`(PR #143)이다.
배포를 실행하면 결과(리비전·digest·검증)를 같은 형식의 `…-production.md` 기록으로 남기고,
CLAUDE.md·`docs/ops/deployment-runbook.md` 상단의 최신 배포 절을 그때 갱신한다.

## 배포 대상

main의 런타임 소스는 PR #153 병합 커밋 `6ca8d24614d2be98d95ae86dd02859dc7aa4cb12`다.
이 문서를 더한 커밋은 문서만 바꾸므로 main tip을 배포해도 런타임 코드는 `6ca8d24`와 같다.

운영(`300a663`) 이후 쌓인 변경:

| PR | 범위 | 서비스 |
|---|---|---|
| #144 | 노출 진단 수동 생성 배포 기록(문서) | — |
| #145 | Admin BFF 허용 목록에 `lead-diagnoses` 추가 | Admin |
| #146 | 노출 진단 생성 이력 목록(`GET /admin/lead-diagnoses`)과 화면 | API · Admin |
| #147 | OpenAI 광고 전환 픽셀 | Site |
| #148·#149·#150 | 진단 제안서·월간 보고서·내부 리포트 지면 개편, 문의 주소 설정 통일 | API · Worker |
| #151·#152 | 랜딩 개편 병합(reputation-landing) | Site |
| #153 | 통합 정합성 보완(Round 3~4) | API · Worker · Beat · Admin · Site |

## 이번 배포가 바꾸지 않는 것

- **DB migration 없음.** 운영 head `0080_lead_diagnosis_supersede` 그대로다.
- RedBeat 스케줄 없음(`2026-09-17.1` 그대로). 새 Celery task·큐 없음.
- Terraform·`scripts/deploy.sh`·Dockerfile 변경 없음. 새 필수 환경변수·secret 없음.

## 실행 전 확인

1. **Terraform을 함께 적용하지 않는다면 건너뛴다.** 적용할 계획이면 먼저
   `terraform -chdir=terraform state list | grep -E 'anthropic_api_key|openai_api_key|gemini_api_key'`로
   옛 provider secret 3개가 state에 남아 있는지 확인한다. 남아 있으면 apply가 Secret Manager에서
   secret과 IAM binding을 삭제하고, 09-16·09-17 롤백 좌표의 리비전이 뜨지 않는다. 이번 배포는
   Terraform 변경이 없으므로 `deploy.sh`만 실행하면 이 위험과 무관하다.
2. 로컬 사전 가드: `make db-budget-guard`(75/80), `make copy-guard`.
3. main을 최신으로 받고 워크트리가 깨끗한지 확인한다.

```bash
git fetch origin main && git checkout --detach origin/main
make db-budget-guard && make copy-guard
PUBLIC_DOMAIN=reputation.motionlabs.kr ADMIN_DOMAIN=admin.reputation.motionlabs.kr \
  bash scripts/deploy.sh all
```

`PUBLIC_DOMAIN`·`ADMIN_DOMAIN`은 `.env.production`이 아니라 셸 환경변수로 넘긴다
(`scripts/deploy.sh:113`, 09-22 배포에서 미지정 시 preflight가 멈춘 이력이 있다).
순서는 runbook과 같다 — 롤백 좌표 → 이미지 3종 → migration(변경 없음) → Worker →
RedBeat 재조정 → Beat → readiness gate → API → Site → Admin.

## 배포 중 알려 둘 것

- API가 Admin보다 먼저 새 리비전을 받는다. 그 사이(보통 수 분) Admin의 노출 진단 생성은
  BFF가 요청 키를 아직 붙이지 않으므로 옛 화면에서 키 없이 들어온다 — 백엔드는 키를 선택으로
  받으므로 거절하지 않는다. 새 Admin이 트래픽을 받으면 BFF가 키 없는 생성 요청을 막는다.
- 운영 Admin(`00092-zln`)은 지금 `노출 진단 생성`·`값 고쳐 다시 만들기`가 BFF에서 403이다(#145
  미배포). 새 Admin 리비전이 트래픽을 받는 순간 풀린다.

## 배포 후 확인

기본 확인(runbook `검증할 증거`):

- 5개 서비스 Ready, 트래픽이 새 리비전, 이미지 태그 일치.
- readiness Job 성공: 7개 큐 canary, `schema_current`(head `0080`).
  - 새 참고 항목 `facts.inquiry_sms`에서 `configured`와 `skip_reason`을 읽는다. `false`면
    도입문의 안내 문자가 발송되지 않고 있는 것이다(준비 판정은 막지 않는다).
- 공개 병원 9곳 `.well-known/reputation-health` 200, `release`가 새 site 리비전.
- 새 리비전 `severity>=ERROR` 0건(배포 후 20분 창).

이번 배포 고유 확인:

| 확인 | 기대 결과 |
|---|---|
| Admin `노출 진단 생성`에서 1건 생성 | 201, 아래 이력 목록에 즉시 보임. 403이면 Admin 리비전 확인 |
| 같은 화면에서 응답 직후 새로고침 없이 한 번 더 제출 | 같은 진단이 돌아옴(새 진단 없음) |
| 상담 요청 화면 `값 고쳐 다시 만들기`로 병원명 정정 | 새 진단의 판정 대상이 고친 이름, 이력에 옛 진단은 옛 이름으로 남음, 연락처 칸 없음 |
| 보고서 탭의 초기 진단(V0) 행 | `다시 만들기` 버튼 없이 안내 문구만 보임 |
| 랜딩 `/#lead` 접속 | 도입문의 폼 위치로 스크롤 |
| 랜딩 도입문의 1건 제출 | 리드 저장 → INTERNAL 초도 진단 생성 → (문자 설정 시) 안내 문자 |
| 운영 센터의 옛 온보딩 인시던트 링크 | `/hospitals/{id}/info`로 열림 |
| API 부팅 로그 | 문자 발송이 꺼져 있으면 `NHN_SMS_SECRET_KEY` Secret Manager 조회 실패 경고가 없음 |

## 롤백

```bash
bash scripts/deploy.sh rollback
```

트래픽만 되돌린다. 이번 배포에는 migration이 없으므로 DB는 손대지 않는다. 새 코드가 남기는
`CREATE_LEAD_DIAGNOSIS` OperationRun 행은 옛 코드가 읽지 않으므로 롤백 뒤에도 무해하다.

## 배포와 별개로 남은 결정

코드 변경 없이 대표 결정이 필요한 항목이다. 결정 뒤 코드와 CLAUDE.md를 함께 고친다.

- `/ai-diagnosis`: 랜딩 병합(#151)에서 Site가 셀프 신청을 종료했다(`/contact`로 이동, 신청 410).
  CLAUDE.md는 "유지"로 적혀 있고 Backend 공개 신청 경로는 열려 있다.
- 개인정보 처리방침: 화면은 `v1.2026-09`, 도입문의 동의 기록은 `v1.2026-08`이다. 새 폼이 받는
  병원 주소·홈페이지·원장 성함이 수집 항목에 없다.
- `/brochure`: 원장 성함·휴대폰·동의를 받지만 Backend 저장 경로가 없어 버려진다. 홈 링크는
  플래그로 숨겨져 있으나 주소 직접 접근은 열려 있다.

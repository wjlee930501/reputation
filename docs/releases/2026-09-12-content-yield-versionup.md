# 2026-09-12 콘텐츠 수율 버전업 (v2.7) 기록

문서 버전: 1.0 · 브랜치 `claude/system-performance-review-x6vtn4` · 기준 `ca48ee8`(main) · 상태: 구현·로컬 검증 완료, 운영 배포 전

계획과 근거는 [버전업 계획](../plans/2026-09-12-content-yield-versionup-plan.md)을 본다. 이 문서는 실제 변경, 검증 증거, 배포 시 주의를 기록한다. 수치는 2026-09-12 로컬 실행 결과다.

## 왜

운영 7개 병원의 누적 발행은 2026-09-07 기준 115편으로 계약(월 12~20편)의 절반 아래였다. 네 갈래 독립 감사와 코드 재확인으로 확인한 원인은 게이트 자체가 아니라, 확률적인 LLM 실패를 "입력 변경 필요"로 굳혀 배포 전까지 슬롯을 비워 두는 재시도 설계, 프롬프트와 검증기의 자기모순(참고자료·분량 단위·max_tokens), 일상 임상 문장을 잡는 금지 표현 필터, 재검수 경로 없는 독립 검수의 UNCERTAIN 차단, 이미지의 절대 발행 선행조건, Essence 자동 검수의 1회 보류 영구 정지였다.

## 변경 (커밋 순)

1. 3f25ace 독립 AI 검수: 확신도 부족 합성 UNCERTAIN을 상위 모델로 1회 재검수, 통과 기준 0.85→0.70, SOFT·STYLE만 남으면 PASS, 검수자 입력에 작가의 Essence와 통과한 결정적 게이트 목록 추가.
2. 29ec465 작성 엔진·필터: 참고자료 "비워 두라" 지시 제거와 COLUMN·HEALTH 참고자료 요구, 분량 단위 정합(평문 기준), max_tokens 12,000과 잘림 감지, 실제 매칭 어휘 전부를 프롬프트에 노출, 결정적 검증 거절을 다음 회차 지시로 전달(blind 재시도 제거), 비대상 유형의 키워드 정렬 검사 제외, 금지 표현 필터의 좁은 문맥 예외와 양방향 테스트.
3. 8992c19 이미지 재사용: 이미지 예산 소진·정책 거절 시 같은 병원의 가장 오래된 인증 이미지를 빌려 발행(`image_reused_from_content_id`, 마이그레이션 0074), 재사용 인증 판정, 01:20·04:20·07:20 교체 스윕(RedBeat 2026-09-12.2), 화이트리스트 참고자료 제목의 기관명 치환.
4. f54e576 문서: CLAUDE.md 2.7, 시스템 구조 2.5, Slack 정책.
5. (이 커밋) 재시도 정책·스윕·인시던트, Essence 자율 복구, 수율 관측.
   - `SAMPLE_RECOVERABLE` 재시도 클래스: 작가 거절·이미지 실패·UNCERTAIN만 남은 검수 차단을 KST 하루 예산(본문 2세션·이미지 4회)과 소진 3일 상한으로 재시도하고, 소진 뒤에만 `OPERATOR_REQUIRED`와 원인별 인시던트 1건. 저장 본문 수리 코드도 같은 예산으로 계수. HARD 사실·안전 지적의 삭제형 재작성 1회와 재검수. 보완 순서를 Essence 스크린·독립 검수 → 키워드·계절로 변경. 정책 거절 이미지의 repair 프롬프트 1회. 07:45·08:00이 저장된 종착 원인을 보고. 22:30 지연 복구는 7일 catch-up 밖만 이동. 예산 안 차단은 RETRYING, 같은 글의 다른 원인 인시던트 회수, `CONTENT_AI_REVIEW_CONFIG_ERROR` 즉시 알림, 옛 딥링크 교체. 08:00 요약에 "대표 이미지 재사용 발행" 절과 크레딧·할당량 안내(`image_failure_class`).
   - Essence: 보류 초안의 사이클·시각 기록과 24h × 2^(cycle-1) 백오프 최대 4회, 옛 마커(8)는 사이클 1로 재해석, 사람이 손댄 초안 제외, 72시간 ERROR 자료의 합성 제외와 gap 기록, 샤드 인지 프롬프트와 `evidence_elsewhere`, `essence` 비용 카테고리(일 60·월 600), 반복 실패 claim 백오프, base 판정 함수 통일, 자동 예산이 남은 초안은 현황 예외 카드에서 제외.
   - 수율: `services/content_yield.py`(계약 예정·발행·재사용 이미지·재시도 중·조치 필요·원인별 차단), 월요일 주간 요약 맨 위에 병원별 수율 줄(차단 0건이어도 예정 슬롯이 있으면 발송), `GET /admin/operations/content-yield?weeks=`.

## 검증 (2026-09-12, 로컬 Postgres 16 5432/5434 UTF8 + Redis, CI와 같은 env)

- 기준선(변경 전 `ca48ee8`): backend 3,538 passed / 9 skipped.
- 변경 후: 아래 "최종 실행"에 기록.
- ruff 전체 통과. `scripts/check_user_facing_terms.py` OK. `scripts/check_db_connection_budget.py` 75/80.
- 마이그레이션 head `0073_add_director_deltas` → `0074_add_image_reuse_marker`(추가 컬럼·FK만, 롤링 안전).
- 프런트엔드(admin/site)는 변경하지 않았다. `make test-frontend`는 이 브랜치에서 돌리지 않았다.
- 실제 모델·Slack 호출과 운영 왕복은 검증하지 않았다. 재시도 예산·재사용·재검수 동작은 단위·Postgres 통합 테스트로 확인했다.

## 최종 실행

- backend 전체: 3,725 passed / 0 failed / 9 skipped (70.6초). 기준선 대비 +187 테스트.
- ruff 전체 통과, copy-guard OK, db-budget-guard 75/80.
- skip 9건은 기준선과 동일(REQUIRE_PDF_RENDER 미설정 등 환경 의존).

## 배포 시 주의

- 운영 배포본 `a774851` 이후 main의 31개 커밋(stable-base Essence 포함)이 미배포다. 이 브랜치의 변경은 그 위에 쌓이므로 함께 나간다. 배포 전 `alembic upgrade head`가 0072·0073·0074를 적용해야 한다.
- 비용 가드에 `essence` 카테고리가 추가된다. 기본 한도 일 60·월 600이며 Redis 키가 새로 생긴다. 기존 `content` 한도는 그대로다.
- RedBeat 스케줄 버전 `2026-09-12.2`. 배포 후 영속 스케줄 재조정에서 `refresh-reused-content-images`(01:20·04:20·07:20 KST) 등록을 확인한다.
- `GENERATION_GATE_CATALOG_VERSION`이 `2026-09-13.1`로 올라 기존에 거절된 슬롯이 다음 스윕에서 한 번 더 평가된다. 여기에 `SAMPLE_RECOVERABLE` 예산이 더해지므로 배포 직후 며칠은 콘텐츠 생성 호출이 평소보다 늘 수 있다. 비용 가드 일 한도(현재 250)를 관찰한다.
- 옛 마커(사이클 8)로 보류된 Essence 초안은 첫 재조정에서 자동 재검수를 1회 받는다(병원당 합성·검수 각 1~2회 비용).
- 첫 이미지 재사용 발행이 나오면 08:00 요약에 새 절이 나타난다. 이미지 공급자 크레딧·할당량과 비용 가드 한도를 확인하는 것이 그 절의 유일한 후속 행동이다.

## v2.7.1 후속 (2026-09-13, 대표 지시 C1~C6)

- C1 외부 감시: `services/pipeline_watchdog.py`, `GET/POST /admin/watchdog/pipeline[/alert]`, `terraform/watchdog.tf`(Cloud Scheduler 5분·08:30 KST), `PIPELINE_WATCHDOG_TOKEN`(Secret Manager, 비어 있으면 경고만). 인프라 조건은 개발 채널, 발행 누락은 운영 채널, 개발 웹훅 미설정 시 운영 채널 대체. 배포 전 시크릿 생성과 `terraform apply`가 필요하다.
- C3 처리량: 23:00·01·04·07 배치를 디스패처로 바꾸고 글 단위 태스크 `generate_claimed_content_item`(claim token 재검증, 900/1000초, max_retries 0)으로 병렬 처리. 23:00 창 이틀, 병원 라운드로빈, 용량 경고, 자율 복구 allowlist 등록. 동시성은 바꾸지 않았다(`CELERY_CONCURRENCY=2`, DB 예산 75/80). 처리량은 동시성에 비례하므로 병원이 늘면 Worker 동시성·인스턴스를 올리고 `scripts/check_db_connection_budget.py`를 통과시킨다.
- C4 참고자료 주제 일치: 결정적 겹침 점수로 명백히 무관한 출처만 제거(판단 불가는 유지), 검수자 REFERENCE 지적은 SOFT 비차단. 차단 코드 없음.
- C6 중복 주제: 문자 bigram Jaccard 0.6 또는 동일 제목·같은 키워드의 첫 H2 일치를 유사로 보고 문체 보완 예산 1회로 다른 각도를 요구. 그래도 유사하면 발행하고 `duplicate_topic_findings` 기록. 차단 아님.
- C2·C5 이미지: `app/utils/check_image_provider.py` 9단계 진단(`docs/ops/image-provider-runbook.md`), 정책 검수 실패의 원인 보존과 `PROVIDER_NOT_CONFIGURED` 진단, 같은 유형 우선 재사용, 병원 대표 이미지 fallback(마이그레이션 0075: hospitals에 fallback 인증 5컬럼, content_items에 `image_fallback_source`). 08:00 요약이 "병원 대표 이미지 사용"을 구분해 보여 준다.
- 검증: backend 전체 3,841 passed / 9 skipped(아래 최종 실행 갱신), ruff·copy-guard·DB 예산 통과. 마이그레이션 head 0075.
- 이미지 생성이 현재 실패하는 원인은 운영 자격 증명 없이 확정할 수 없다. 코드 기준 유력 순위는 (1) 정책 검수 모델(`GEMINI_MODEL`, Vertex `GOOGLE_IMAGE_LOCATION`) 도달 불가가 `POLICY_UNAVAILABLE`로 뭉개짐, (2) 클라이언트 `api_version="v1"` 고정과 `response_modalities`·`image_config`·`thinking_config` 필드의 v1beta1 의존, (3) 429/크레딧 고갈, (4) 버킷 이름 규칙 불일치, (5) 정책 거절 반복이다. 진단 스크립트의 첫 실패 단계가 원인을 가른다.

## 미결·후속

- 제공자 사용량 원장(`provider_usage`)은 Essence 호출을 여전히 `content` 카테고리로 기록한다. CHECK 제약 마이그레이션이 필요하다.
- Admin UI에 수율 화면은 없다. API만 있다.
- `IMAGE_REUSED` 상태의 교체 시도는 다음 KST 일부터 시작한다(당일 이미지 예산을 이미 쓴 슬롯).
- 새 병원의 첫 글은 재사용할 이미지가 없어 종전처럼 이미지 준비까지 발행이 막힌다.

# 이미지 생성 장애 진단 안내

문서 버전: **2.0** · 갱신일: **2026-09-20 (Asia/Seoul)**
대상 코드: `backend/app/services/image_engine.py`, `backend/app/utils/check_image_provider.py`

대표 이미지가 만들어지지 않는다는 증상 하나에 원인이 여럿이다. 콘텐츠 파이프라인은 이미지
실패가 본문 저장과 발행 시도를 막지 않도록 삼키게 설계돼 있어서(의도한 계약이다), 운영
로그만으로는 크레딧·할당량·모델 슬러그·권한·정책 거절을 구분하기 어렵다. 이 진단기는 운영
코드가 쓰는 함수와 설정을 그대로 한 번씩 밟아 **처음 끊기는 지점**을 지목한다.

2026-09-20부터 이미지 생성·정책 검수·모든 LLM 호출은 `OPENROUTER_API_KEY` 하나로
OpenRouter(`https://openrouter.ai/api/v1`)를 거친다. `IMAGE_PROVIDER`·
`IMAGE_FALLBACK_PROVIDER`는 OpenRouter 안에서 어떤 모델 계열(`google/…`·`openai/…`)을
쓸지 고르는 라우팅 라벨일 뿐, 공급자 직결 SDK는 더 이상 없다. Vertex 권한·ADC는 이미지
모델 경로와 무관하다(GCS 업로드에는 여전히 필요).

## 실행

```bash
cd backend
uv run python -m app.utils.check_image_provider
```

옵션:

- `--skip-paid` — 4·5단계(실제 이미지 1장 생성 + 검수 1회)를 건너뛴다. 이 두 단계는 유료
  호출이며 비용 가드 카운터도 올라간다. 원인이 설정·권한 쪽으로 좁혀졌으면 이걸 쓴다.
- `--skip-db` — 8·9단계(DB 조회)를 건너뛴다. `SYNC_DATABASE_URL`이 없는 환경에서 쓴다.

개발 샌드박스에서는 Postgres·Redis가 꺼져 있을 수 있다. 그때 8·9단계와 7단계가 실패하거나
`availability: UNAVAILABLE`로 나오는 것은 **환경 문제이지 이미지 경로의 진단이 아니다**
(이 안내를 처음 쓸 때의 검증 환경이 그랬다 — 클러스터와 Redis를 직접 올린 뒤에야 8·9단계가
의미 있는 값을 냈다). 운영 원인 판정은 반드시 운영 값으로 돌린 출력으로 한다.

운영 값으로 돌려야 의미가 있다. `OPENROUTER_API_KEY`와 GCS 업로드용 ADC(`gcloud auth
application-default login` 또는 워커 서비스 계정 키), `GCP_PROJECT_ID`,
`GCP_STORAGE_BUCKET`, `REDIS_URL`, `SYNC_DATABASE_URL`을 같은 셸에 넣고 실행한다.
비밀값은 출력에서 꼬리 4자만 남기고 가려진다.

마지막 줄 `most likely cause:`가 **처음 실패한 단계**와 그 조치를 말한다. 뒤 단계의 실패는
대개 앞 단계의 결과이므로 앞에서부터 고친다.

## 단계별 의미와 조치

| # | 단계 | FAIL이 뜻하는 것 | 조치 |
|---|---|---|---|
| 1 | settings/secrets resolved | `OPENROUTER_API_KEY`가 비었거나 버킷 이름이 규칙과 다르다 | `OPENROUTER_API_KEY`·`IMAGE_PROVIDER` 확인. 버킷 이름은 `reputation-images-<GCP_PROJECT_ID>` 규칙(terraform `storage.tf`, `scripts/setup-gcp.sh`)이다. 기본값 `reputation-images` 그대로면 업로드가 항상 실패한다 |
| 2 | OpenRouter client init | `OPENROUTER_API_KEY`가 없다 | Secret Manager/Cloud Run 환경에 키가 있는지, 워커 egress가 `openrouter.ai`에 닿는지 확인 |
| 3 | image model reachable | 무과금 `GET /models` 카탈로그 조회가 실패하거나 설정 슬러그가 카탈로그에 없다 | `GOOGLE_IMAGE_MODEL`·`OPENAI_IMAGE_MODEL`·`GEMINI_MODEL`·`OPENAI_MODEL_PARSE` 값을 OpenRouter 카탈로그의 실제 `vendor/model` 슬러그로 맞춘다. 조회 자체가 401/403이면 키 문제 |
| 4 | one image generation | 이미지 모델 호출 자체가 실패했다 | 원문에서 구분한다. `429`/`quota`/`insufficient credits` → OpenRouter 크레딧·할당량. `404` → 모델 슬러그가 카탈로그에 없음(오타 포함). `400`이면 그 모델이 안 받는 파라미터(`aspect_ratio`/`resolution`/`quality`)를 의심한다. 본문에 `IMAGE_SAFETY`·`PROHIBITED_CONTENT`·`moderation_blocked`가 보이면 공급자 안전 차단이다 |
| 5 | policy review of that image | 이미지는 나왔는데 검수가 막혔다 | `FAIL: 정책 검수가 거절했다`면 정책 거절 루프다(assessment 불리언이 함께 찍힌다) — 주제/프롬프트 문제이며 크레딧이 아니다. 그 외 FAIL은 검수 모델(`GEMINI_MODEL`, 폴백 `OPENAI_MODEL_PARSE`) 접근 실패이며, 이 경우 **모든** 이미지가 `POLICY_UNAVAILABLE`로 버려진다 |
| 6 | GCS write+delete round-trip | 발행에 쓰는 것과 같은 권한으로 버킷 쓰기가 실패했다 | 버킷 존재와 이름 규칙, 서비스 계정의 `storage.objects.create`/`delete` 확인 |
| 7 | cost guard | WARN | `availability: UNAVAILABLE`이면 Redis에 못 닿은 것이고 가드는 fail-open이라 **이미지 실패의 원인이 아니다**. `daily_used >= daily_limit`이면 `COST_BLOCKED`로 막힌 것이므로 상한 상향(기본값의 2배까지)이나 다음 날을 기다린다. 킬스위치가 켜져 있으면 그것부터 끈다 |
| 8 | 최근 20건 공급자 시도 | WARN이면 최근 이미지 생성 호출 기록이 아예 없다 | 공급자를 부르기 **전에** 막혔다는 뜻이다 — 1·2·7단계(설정·클라이언트·비용 가드)를 본다 |
| 9 | 저장된 실패 분류 | 분류별 집계 | `COST_GUARD`·`PROVIDER_QUOTA`·`POLICY_REJECTED`·`PROVIDER_ERROR` 비중으로 전체 상을 본다. 한 분류가 압도적이면 그 조치만 하면 된다 |

## 코드가 이미지 실패를 남기는 자리

`generate_image`는 실패해도 예외를 올리지 않고 `("", "")`를 돌려준다. 대신 호출부가 준
`diagnostics` 딕셔너리에 `reason`을 남기고, 그 값이 `workers/tasks._image_failure_class`에서
운영 분류로 바뀐다.

| `diagnostics["reason"]` | 언제 | 저장 분류 |
|---|---|---|
| `COST_BLOCKED` | image/content 예약이 거부됨 | `COST_GUARD` |
| `PROVIDER_NOT_CONFIGURED` | `OPENROUTER_API_KEY`가 없음 | `PROVIDER_ERROR` |
| `PROVIDER_EMPTY` | SDK 미설치 등으로 바이트가 비어 돌아옴 | `PROVIDER_ERROR` |
| `IMAGE_SAFETY` | 모델이 프롬프트/결과를 안전 차단 | `POLICY_REJECTED` |
| `POLICY_REJECTED` | 독립 정책 검수가 거절(assessment 동봉) | `POLICY_REJECTED` |
| `POLICY_UNAVAILABLE` | 검수 모델 호출 실패. `policy_error`에 공급자 원문 | `PROVIDER_ERROR`(원문에 quota 신호가 있으면 `PROVIDER_QUOTA`) |
| `PROVIDER_ERROR` | 그 밖의 공급자 오류 | `PROVIDER_ERROR`/`PROVIDER_QUOTA` |

## 기본 모델 경로 뒤의 폴백

`IMAGE_FALLBACK_PROVIDER=openai`(기본)이면 기본 경로(기본 프롬프트 → 안전 폴백 프롬프트,
또는 repair 프롬프트)가 `IMAGE_SAFETY`·`POLICY_REJECTED`·`PROVIDER_ERROR`·`PROVIDER_EMPTY`로
끝났을 때 `OPENAI_IMAGE_MODEL`(기본 `openai/gpt-5-image-mini`)로 후보를 한 번 더 만들고
**같은 정책 검수**를 통과시킨다. 두 경로 모두 같은 `OPENROUTER_API_KEY`로 OpenRouter를
거치며 provider 라벨만 `google`/`openai`로 갈린다. 검수 모델이 죽은 `POLICY_UNAVAILABLE`과
`COST_BLOCKED`는 폴백하지 않는다. 이미지 1건의 유료 호출 상한은 기본 경로 최대 2단계 × 3회 +
폴백 3회이며 전부 image 계수에 잡힌다. 폴백까지 실패하면 저장 분류는 기본 경로의 원인을
유지하고 `openai_fallback_error`에 폴백 원문(quota 신호 포함)만 덧붙는다. 폴백이 성공한
글은 `image_prompt`가 폴백 프롬프트다.

2026-09-12~14 운영에서 Gemini 후보가 3일 내내 전부 실패한 패턴(기본 프롬프트 `IMAGE_SAFETY`
차단 → 안전 폴백 후보가 검수 거절)이 이 폴백을 넣은 이유다. 거절 사유의 불리언은 저장된
`generation_attempt.image_policy_rejection`이나 이 진단기의 5단계 출력으로 본다.

## 이미지가 끝내 안 만들어질 때의 발행

이미지 실패가 발행을 하루 넘게 막지 않도록 두 단계의 대체가 있다.

1. 같은 병원의 **이미 인증된 공개 글 이미지**를 빌린다. 같은 `content_type`을 먼저 보고,
   없으면 유형을 가리지 않고 가장 오래 전에 인증된 것을 쓴다
   (`services/content_image_reuse.select_reusable_hospital_image`).
2. 빌릴 것이 없는 첫 글이면 **병원 히어로 이미지**를 실제 바이트로 검수해 쓴다
   (`certify_hospital_fallback_image`). 인증은 병원 행에 캐시되고 `hero_image_url`이 바뀌면
   다시 검수한다.

대체 발행 사실은 08:00 요약(`GENERATION_BLOCKED_DIGEST`) 안의 **한 섹션**으로만 보고된다 —
새 Slack 메시지를 만들지 않는다. 섹션의 줄은 출처로 갈린다: 다른 글에서 빌렸으면
`재사용 발행 N건`, 첫 글이라 병원 히어로를 썼으면 `병원 대표 이미지 사용 N건`. 뒤쪽은 그
병원에 빌릴 인증 이미지가 **하나도 없었다**는 뜻이며, 조치는 두 경우 모두 같다(공급자
크레딧·할당량과 비용 가드 한도 확인).

히어로 대체본의 정책 판정은 생성 이미지와 다르다. 이건 병원이 스스로 고른 자기 자산이므로
**로고를 허용**하고(자기 로고는 사칭이 아니다) 주제 적합성도 요구하지 않는다. 반면 **글자와
식별 가능한 인물은 그대로 막는다** — 문구가 박힌 배너는 의료광고 표현 검사를 우회하는 통로가
되고, 인물 사진은 동의 범위를 넘어 콘텐츠 카드로 재사용되기 때문이다.

둘 다 임시 상태다. 교체 스윕(01:20·04:20·07:20 KST, `workers/published_image_refresh`)이 그
글의 주제 이미지를 만들어 붙이고 marker(`image_reused_from_content_id`,
`image_fallback_source`)를 지운다. 그러니 이 진단기가 지목한 원인을 고치는 일은 여전히
사람의 몫이다 — 대체는 발행을 살릴 뿐 원인을 없애지 않는다.

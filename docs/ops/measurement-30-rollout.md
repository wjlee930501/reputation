# 측정 키워드 30개 운영 반영 절차 (2026-11-21)

기준: [원 요청과 기준](../plans/2026-11-21-measurement-30-requirements.md), [설계](../plans/2026-11-21-measurement-30-design.md).
PR ① #378 측정 계약 → ② #379 A그룹 정확도 → ③ #380 30개 초안·어드민 → ④ 원장 보고서 → ⑤ 이 문서·한도.

**마감**: 12/1 00:15 KST 월간 측정(11월분) 전에 운영에 나가 있어야 한다. 매월 1일 00:15~09:00은 배포 금지이고, 그 밖에도 배치 시간대(22:15~02:00, 04·07·08·12·18·22시 앞뒤 45분)는 `deploy.sh`가 막는다. **11/27~28 낮**에 배포한다.

## 0. 한 쪽 체크리스트(12/1 전 운영 실행 순서)

| # | 언제 | 할 일 | 확인 |
|---|---|---|---|
| 1 | 배포 날(배치 시간대 밖) | env `COST_GUARD_MONTHLY_SOV_QUERIES=13500`(일 9,000 그대로) → `deploy.sh backend --accept-env-diff`·`deploy.sh admin` | readiness 통과, 마이그레이션 head `0101_add_query_target_set_roles` |
| 2 | 배포 직후 | `measurement_set_rollout precheck` | 코호트 병원마다 `action=apply`, 고정 20·교체 10, `problems=[]` |
| 3 | 2 직후 | (필요하면) `show --ids=<id>`로 문장 확인 | 어색한 문장은 4 뒤 어드민에서 고친다 |
| 4 | 3 직후 | `measurement_set_rollout apply --confirm` | 병원마다 `action=applied`, `summary.fixed=20` |
| 5 | 4 직후 | `measurement_set_rollout precheck` 다시 | 전부 `skip_curated`, `curated_now=true`, `legacy_tracked=0` |
| 6 | 5 직후 | `ops_sql_report --only=q17` | 코호트 병원 모두 `curated=true`, 그룹 A5·B10·C8·D7 |
| 7 | 배포 다음 날~11/30 | 마케터: 콘텐츠 > 측정 키워드에서 문구 고치기·교체 10개 고르기(시즌 강조 키워드) | **11/30 23:00 편집 마감**(1일 00:15 측정표 동결 — 그 뒤 저장은 다음 달부터) |
| 8 | 11/30 18:45~21:30 | 워커 최소 인스턴스 1 → 2(`gcloud run services update reputation-worker --min-instances=2 …`) | 인스턴스 2대 |
| 9 | 11/30 저녁 | §4 확인 목록, 코호트 20곳 넘으면 일 한도 상향을 대표에게 묻기 | q17 마지막 확인 |
| 10 | 12/1 00:45·03:00·06:00 | `ops_sql_report --only=q17`의 q17b(새벽 진행) | `answers_received`가 늘고 `frozen_set30=true` |
| 11 | 12/1 07:15~09:00 | 보고서 확인(§5) | 1면 B~D, P3 A행·정확도 줄, 부록 A 30개 |
| 12 | 12/1 09:00 뒤 | 워커 최소 인스턴스 2 → 1 | 인스턴스 1대 |

**로컬 리허설(2026-11-21)**: 로컬 테스트 DB에 운영과 같은 모양의 병원 3곳(내과·치과·한의원, 옛 15개 질문 11~14개, 코호트)을 만들어 2→3→4→5→6을 실제로 돌렸다. precheck 3곳 `apply`·문제 0 → `apply --confirm` 3곳 `applied`(곳마다 새 질문 39개 = 세트 30 + 후보 9, 옛 질문 11~14개는 측정에서만 빠짐) → 다시 precheck 3곳 `skip_curated`·`legacy_tracked=0` → 다시 apply 3곳 `skipped_curated` → q17 3곳 `curated=true`·고정 20·교체 10·A5·B10·C8·D7 → 월간 세트 등록(`register_tracking_set`) 3곳 유효·30개·바뀐 것 0. 옛 질문 문구("○○ 내과 병원 추천해줘")는 지침 3-1 문형과 달라 그대로 이어 쓴 질문은 0개였다 — 11월분은 어차피 질문 세트가 바뀐 첫 달이라 지난달과 비교하지 않는다.

## 1. 배포 전

1. PR ①~⑤를 순서대로 병합한다(②~⑤는 앞 PR 위에 쌓은 PR — 앞이 병합되면 base를 main으로 바꾼다). 병합 직전 `origin/main`의 마이그레이션 head를 확인한다 — ①은 `0101_add_query_target_set_roles`를 더한다. 다른 PR이 먼저 `0101`을 쓰면 번호를 올리고 `scripts/integrity_rehearsal/check.py`·`backend/tests/test_migration_chain_linearity.py`의 head도 함께 올린다.
2. 운영 env(정본은 운영 `reputation-api` 서비스 env — `deploy.sh`가 로컬 `.env.production`과 다르면 멈춘다)를 바꾼다.
   - `COST_GUARD_MONTHLY_SOV_QUERIES=13500` (9,000 → 13,500, 2026-11-21 사용자 승인)
   - `COST_GUARD_DAILY_SOV_QUERIES=9000` (그대로. 코호트가 20곳을 넘으면 올릴지 정한다 — 새벽 하루가 이 한도에 묶인다)
   - 선택: `SOV_MONTHLY_ANSWER_CONCURRENCY=6`(코드 기본값과 같다. 1이면 동시 답변을 끈다 — 롤백 스위치)
   - 로컬 `.env.production`을 운영 env로 다시 만든 뒤 위 값을 고치고 `--accept-env-diff`로 배포한다(`removed` 키가 없는지 확인).
3. 워커 최소 인스턴스는 **12/1 새벽 창에만 2로** 올린다(2026-11-21 결정 — §4의 11/30 저녁에 올리고 12/1 09:00 뒤 1로 되돌린다). 12/1은 새벽 창의 첫 실제 실행이다(11/1은 10월 측정표가 이미 끝나 바로 끝났다).

   **부하 계산**(10/30~31 월말 측정 실측: 칸당 약 22초, 답변 + 판정). 병원 16곳 × 30문항 × 2플랫폼 × 5반복 = 4,800칸(ChatGPT 2,400 · Gemini 2,400).
   - ChatGPT: 프로세스당 동시 6(`SOV_MONTHLY_ANSWER_CONCURRENCY`, 그 안의 공급자 풀 한도는 10). 칸당 약 30초로 보면 2,400 × 30 ÷ (6 × 프로세스 수).
   - Gemini: 같은 키의 짧은 burst가 429를 내서 **프로세스당 1개로 직렬화**한다(`sov_engine._pool_limit`, 기존 계약). 칸당 약 12초로 보면 2,400 × 12 ÷ 프로세스 수 — 이쪽이 창 길이를 정한다.
   - 워커 1대(프로세스 2): Gemini 14,400초 ≈ 4.0시간, ChatGPT 6,000초 ≈ 1.7시간(겹쳐 돈다) → 약 4시간, 창 405분의 60%.
   - 워커 2대(프로세스 4): Gemini 약 2.0시간 → 약 2시간, 창의 30%. 재시도(429·5xx는 3회까지 지수 백오프)와 01·04시 콘텐츠 스윕 몫의 여유다.
   - 동시 답변 없이 차례로 사면(옛 코드) 워커 1대로 4,800 × 22 ÷ 2 ≈ 14.7시간 — 창의 217%. 15문항이어도 109%였다.

## 2. 배포

```bash
./scripts/deploy.sh backend --accept-env-diff --override-reason="측정 키워드 30개"
```

```bash
./scripts/deploy.sh admin --override-reason="측정 키워드 30개"
```

- `backend`가 마이그레이션 `0101`(추가형, 칼럼 2개 + 검사 제약)을 먼저 돌린다.
- Site는 바뀌지 않는다.

## 3. 배포 직후 — 병원별 30개 등록

모두 migrate Job에서 돌린다(운영 DB는 사설 IP). gcloud `--args`는 쉼표로 인자를 나누므로 인자마다 따로 넘긴다.

1. **읽기 전용 확인** — 지금 상태와 초안 요약(병원마다 JSON 한 줄):

   ```bash
   gcloud run jobs execute reputation-migrate --project mso-platform-481505 --region asia-northeast3 --wait \
     --args=python,-m,app.utils.measurement_set_rollout,precheck
   ```

   확인할 것: `curated_now=false`, `draft.A.fixed=5`, 고정 합 20·교체 합 10, `problems=[]`, `action=apply`. 문제가 있는 병원은 `show --ids <id>`로 문장을 보고 어드민에서 고치거나 그 병원만 빼고 적용한다.

2. **적용** — 코호트 병원 전부(이미 30개 방식인 병원은 건너뛴다):

   ```bash
   gcloud run jobs execute reputation-migrate --project mso-platform-481505 --region asia-northeast3 --wait \
     --args=python,-m,app.utils.measurement_set_rollout,apply,--confirm
   ```

   - 같은 문구의 옛 질문은 다시 쓴다(측정 이력 보존). 세트에서 빠진 옛 질문은 측정에서만 빠지고 글 주제로 남는다.
   - 병원마다 커밋하고 감사 로그(`apply_measurement_set`, actor `system:measure-30-draft`)를 남긴다. 거절된 병원은 `action=rejected`와 사유가 나온다.
   - 특정 병원만: `--ids=a,b`(gcloud에서는 `--ids=<uuid>` 한 토큰). 초안으로 다시 만들기: `--replace`(마케터가 고친 세트를 덮어쓴다 — 쓰지 않는다).

3. **확인** — 세트 준비 상태(q17):

   ```bash
   gcloud run jobs execute reputation-migrate --project mso-platform-481505 --region asia-northeast3 --wait \
     --args=python,-m,app.utils.ops_sql_report,--only=q17
   ```

   코호트 병원(`monthly_sov_cohort=true`)이 모두 `curated=true`, `fixed`가 15~20(초안은 20), `rotating`이 0~10(초안은 10), `legacy_tracked=0`이어야 한다.

4. **마케터 안내** — 어드민 병원 > 콘텐츠 > '측정 키워드 30개' 카드 > 측정 키워드 화면에서 그룹별 질문을 보고 고치고 교체 10개를 고른다. 11/30 23:00까지 저장한 세트로 12/1 새벽에 11월분을 잰다(이 시각 뒤 저장은 다음 측정부터 쓰인다 — 1일 새벽 측정이 시작되면 화면이 알려 준다). 시즌 강조 키워드(예: 연세속시원·서울W 11~12월 내시경)는 교체 질문으로 넣는다.

## 4. 11/30 저녁 최종 확인(측정 전날)

- [ ] q17: 코호트 병원 모두 `curated=true`, 모양 정상. 마케터가 고친 병원도 고정 15~20·교체 ≤10.
- [ ] 운영 env: `COST_GUARD_MONTHLY_SOV_QUERIES=13500`, `COST_GUARD_DAILY_SOV_QUERIES=9000`(또는 결정한 값). readiness Job 통과.
- [ ] 비용 사용량: readiness `facts`의 SoV 일·월 사용량이 12/1 새벽 소요(병원 수 × 약 330)를 덮는다 — 12월 1일은 월 카운터가 새로 시작된다.
- [ ] Beat 스케줄에 `monthly-sov-measurement`(1일 00:15~06:45 30분마다)가 있다(`backend/app/core/celery_app.py`).
- [ ] 워커 리비전이 ①을 포함한다(`SOV_MONTHLY_ANSWER_CONCURRENCY` 코드).
- [ ] **워커 최소 인스턴스 1 → 2**(배치 시간대 밖, 11/30 18:45~21:30 사이). `gcloud run services update reputation-worker --min-instances=2 --project mso-platform-481505 --region asia-northeast3`. 12/1 09:00 뒤 1로 되돌린다(`--min-instances=1`).
- [ ] **일 한도 경고**: 코호트(q17의 `monthly_sov_cohort=true`)가 **20곳을 넘으면** 일 한도 9,000을 올릴지 대표에게 묻는다 — 새벽 하루가 일 한도에 묶이고 30문항 병원 1곳이 약 330단위다(24곳 안팎에서 막힌다).
- [ ] 12/1 00:15~09:00 배포·env 변경 금지.

## 5. 12/1 확인

- **00:45, 03:00, 06:00** — 새벽 창 진행(q17b): `planned_slots`(병원당 300) 대비 `answers_received`·`judged`가 늘고 있는지, `frozen_set30=true`인지. 멈춘 병원은 워커 로그 `run_sov_for_hospital`과 비용 보류(`MONTHLY_SOV_COST_GUARD_BLOCKED`)를 본다.

  ```bash
  gcloud run jobs execute reputation-migrate --project mso-platform-481505 --region asia-northeast3 --wait \
    --args=python,-m,app.utils.ops_sql_report,--only=q17
  ```

- **07:00** — 측정 마감. 끝나지 않은 칸은 LIMITED/UNAVAILABLE로 닫힌다(사고는 09:00 최종 판정까지 RETRYING).
- **07:15~08:45** — 보고서 최종화. 원장 보고서에서 확인할 것:
  - 1면 AI 노출률이 B~D 기준이다(A 병원명 질문이 섞이지 않는다). 11월분은 질문 세트가 바뀐 첫 달이라 지난달과 비교하지 않는다 — 증감이 조용히 빠진다(문구로 설명하지 않는다).
  - P3 그룹 표 A~D에 값이 있고, A행 아래 'AI가 병원 정보를 맞게 안내한 답변' 한 줄(말한 답변이 있을 때만).
  - 부록 A에 30개와 그룹. 마케팅팀이 지운 문구(측정 방법·측정 실패·고정 키워드로만 비교 등 9개)가 없다.
- **09:00** — 최종 판정. `ops_sql_report --only=q15`로 전달 게이트.

## 6. 되돌리기

- **동시 답변만 끄기**: `SOV_MONTHLY_ANSWER_CONCURRENCY=1`(env만, 코드 롤백 없음).
- **30개 세트를 옛 방식으로**: 그 병원 질문의 `set_role`을 모두 지우면(어드민 저장으로는 할 수 없다 — DB 작업) 다음 등록에서 옛 10~15개 자동 선택으로 돌아간다. 이미 얼린 이번 달 측정표는 그대로다.
- **코드 롤백**: 마이그레이션 `0101`은 추가형이라 옛 코드가 칼럼을 무시하고 읽는다. 다만 옛 코드는 30개 세트를 모르고 월간·주간 등록이 `in_tracking_set`을 15개로 다시 고른다(마케터가 고른 세트의 측정 플래그가 바뀐다 — 칼럼은 남는다). 옛 코드는 A그룹 질문을 LOCAL로 대표 노출률에 넣는다.

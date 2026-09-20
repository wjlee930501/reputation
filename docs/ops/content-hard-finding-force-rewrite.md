# CONTENT_AI_HARD_FINDING 강제 재작성 운영 절차

## 서비스 기준

독립 AI 검수는 재작성을 요구할 수 있을 뿐, 어떤 글도 발행 가능으로 만들지 못한다.
사실·의료 안전 지적(HARD)이 남은 글은 발행하지 않는다. 이 문서의 절차는 그 규칙을
바꾸지 않는다 — 막힌 글을 **다시 쓰게** 만들 뿐이고, 발행 여부는 기존 08:00 게이트가
그대로 판정한다. 지적이 재작성 뒤에도 남으면 그 슬롯은 계속 차단 상태로 남는다.

## 이 절차가 필요한 증상

- 아침 발행 로그: `due=5 published=0 blocked=5`, 모두 `CONTENT_AI_HARD_FINDING`.
- 야간·주간 복구 스윕이 같은 행을 계속 claim하지만, 각 실행이 수 초 안에 끝나고
  본문은 그대로다. 운영 센터의 "작업 다시 시도"도 같은 코드로 즉시 실패한다.

원인은 고장이 아니라 설계된 종착이다. 모델이 HARD로 단정한 지적은
`INPUT_CHANGE_REQUIRED`로 분류되고, 저장된 본문이 있는 슬롯의 예약 스윕 경로는 그
지적을 fail-closed로 되돌린다 — 새 근거 없이 "다시 써 봐"를 무한 반복하며 작가 세션
비용을 쓰지 않기 위한 계약이다. 그래서 이 형태의 정체는 **사람의 결정 없이는 절대
스스로 풀리지 않는다.**

## 강제 경로가 실제로 하는 일

1. 저장된 `essence_check_summary["ai_review"]`의 차단 지적을 재작성 제약으로 바꾼다.
   HARD 사실·의료 안전 지적에는 삭제·완화 지시문(새 사실·수치·효과·장비·경력 추가
   금지)이 붙고, 나머지 차단 지적은 그대로 제약이 된다.
2. 그 제약으로 작가 세션 1회를 사고, 결과를 **반드시** 독립 검수에 다시 태운다.
3. 새 판정을 저장한다. 통과하면 대표 이미지를 만들고 발행 준비 판정을 다시 기록한다.
   여전히 HARD면 그 판정을 저장하고 차단 상태로 끝낸다(이미지 예산도 쓰지 않는다).
4. 발행은 건드리지 않는다. 이후 08:00~23:00 매시 `morning_content_auto_publish`가
   기존 게이트로 판정한다.

예약 스윕(23:00 야간, 01·04·07·12·18·22 복구, 07:45 마감)의 동작은 **달라지지 않는다.**
강제는 명시적으로 요청한 실행에서만 켜진다.

## 트리거

### 1) Celery 태스크 직접 호출 (한 글)

```
task: app.workers.tasks.regenerate_content_item
args: ["<content_id>", true]
queue: content
headers: build_dispatch_headers("regenerate-content", "<content_id>")
```

두 번째 인자 `force_hard_rewrite=true`가 강제 경로다. 생략하면 종전 동작(운영 센터의
"작업 다시 시도"와 동일)이다. 운영 센터의 재시도 버튼은 저장된 dispatch payload의
인자 1개만 허용하므로 화면에서 이 경로가 우연히 켜지는 일은 없다.

### 2) Cloud Run Job (여러 글, 발행 순서대로)

```
SERVICE=force-hard-rewrite
FORCE_HARD_REWRITE_CONTENT_IDS=<쉼표로 구분한 content_id 목록>
FORCE_HARD_REWRITE_APPLY=   # 비워 두면 계획만 출력(쓰기·적재 없음)
```

또는 이미지 안에서 직접:

```
python -m app.utils.force_hard_finding_rewrite <content_id> ...          # 계획만
python -m app.utils.force_hard_finding_rewrite --apply <content_id> ...  # 실제 적재
```

`--apply` 없이 실행하면 DB에 아무것도 쓰지 않고 큐 메시지도 만들지 않는다. 적재
순서는 발행 순서(`scheduled_date` 오름차순 → `sequence_no`)이므로 가장 오래 밀린
슬롯이 먼저 단일 `content` 큐에 들어간다.

이 진입점은 **현재 차단 코드가 정확히 `CONTENT_AI_HARD_FINDING`인 슬롯만** 적재한다.
이미 발행 가능해진 글, 이미 공개·종료된 글, 다른 원인(예: `CONTENT_IMAGE_NOT_READY`)으로
막힌 글은 이유를 이름으로 적어 거부한다.

## 2026-09-20 사고의 대상 슬롯

배포 후 아래 순서 그대로 적재한다(앞의 세 건이 2026-09-13로 가장 오래 밀렸다).

| 순서 | content_id | 예정일 |
|---:|---|---|
| 1 | `84eb0353-d64c-49aa-9fa0-d675061bef4b` | 2026-09-13 |
| 2 | `af3ba158-ebce-42e6-a6f4-5387e6a96805` | 2026-09-13 |
| 3 | `b5c63202-9efa-46cd-8f9e-f4a0ec714d4b` | 2026-09-13 |
| 4 | `42ef2b13-ecbe-44bb-8655-d949a95d7f3b` | 2026-09-15 |
| 5 | `6c537f05-0d5f-404e-9399-6da7a2a45a3c` | 2026-09-16 |

```
FORCE_HARD_REWRITE_CONTENT_IDS=84eb0353-d64c-49aa-9fa0-d675061bef4b,af3ba158-ebce-42e6-a6f4-5387e6a96805,b5c63202-9efa-46cd-8f9e-f4a0ec714d4b,42ef2b13-ecbe-44bb-8655-d949a95d7f3b,6c537f05-0d5f-404e-9399-6da7a2a45a3c
```

먼저 `FORCE_HARD_REWRITE_APPLY`를 비운 채 실행해 다섯 건 모두
`CONTENT_AI_HARD_FINDING`으로 확인된 것을 로그에서 본 뒤, 같은 Job에 값을 넣어
다시 실행한다. 2026-09-21 KST 이후에는 7일 catch-up 창 밖으로 밀린 슬롯을 22:30
백로그 복구가 미래 날짜로 옮기므로, 옮겨진 뒤에는 새 예정일 기준으로 다시 확인한다.

## 확인

- 감사 로그 `force_content_hard_finding_rewrite`: 어느 글을 어떤 차단 상태에서
  강제 재작성했는지 글마다 한 줄.
- 워커 로그 `Forcing a hard-finding rewrite for stored content <id>`: 제약 개수 포함.
- 성공하면 `essence_check_summary["ai_review"]`가 새 판정으로 바뀌고 본문·제목이
  갱신된다(`body_updated_at`). 실패하면 같은 코드로 차단이 유지된다.
- 발행은 별도로 확인한다 — 다음 매시 정각 자동 발행이 기존 게이트로 판정한다.

## 하지 않는 일

- HARD 지적을 건너뛰고 발행하지 않는다. 그런 경로는 없고, 추가하지도 않았다.
- 예약 복구의 판정 기준을 바꾸지 않는다(UNAVAILABLE 재검수 경로 포함).
- 사람이 검토하지 않은 새 사실·수치·경력을 만들어 넣지 않는다. 강제 재작성의 지시문은
  지적된 주장의 **삭제·완화**이며, 새 근거가 필요한 지적은 결국 차단으로 남는다.

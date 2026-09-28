# Hotfix 운영 안내 — 공개 글 비공개(보존) 전환 + 예약 자동 발행 보류

작성: 2026-09-29 (Asia/Seoul) · 기준: `5862e557`에서 분기한 `hotfix/withhold-and-autopublish-hold`
설계: `/workspace/hotfix-unpublish-design/DESIGN.md` · DB 변경: `0081_add_withheld_content_status`(enum 값 추가만)

## 1. 무엇이 바뀌나

- **A. 비공개(보존) 전환** — 새 콘텐츠 상태 `WITHHELD`("비공개(보존)")와 Admin API 두 개.
  - `POST /admin/hospitals/{hospital_id}/content/{content_id}/withhold` `{ "reason": "..." }`
  - `POST /admin/hospitals/{hospital_id}/content/{content_id}/restore` `{ "reason": "..." }`
  - withhold는 반려(reject)와 달리 제목·본문·참고자료·이미지·`published_at/by`·`first_published_*`를
    **지우지 않고** 공개 목록·상세·이미지·sitemap·llms·IndexNow·자동 작업에서만 뺀다.
    restore는 원래 `published_at` 그대로 다시 공개한다(새 발행 아님, 발행 알림·LLM 호출 없음).
  - WITHHELD 글에는 발행(`/publish`)·재배치(`/reschedule`)·종료(`/cancel`)·재생성·이미지 재생성이
    409로 막힌다. 참고자료 PATCH와 반려(reject, 되돌릴 수 없음)는 허용된다.
  - Admin 화면에는 아직 버튼이 없다. API로만 호출한다(아래 5절). 화면은 "비공개(보존)"으로만 표시한다.
- **B. 예약 자동 발행 보류** — 환경변수 `AUTO_PUBLISH_HOLD_HOSPITALS`.
  - `""`(기본) = 꺼짐. **배포만으로는 동작이 바뀌지 않는다.**
  - `"*"` = 모든 병원의 예약 자동 발행을 멈춘다.
  - `"<병원UUID>,<병원UUID>"` = 나열한 병원만 멈춘다. 공백·빈 항목은 무시한다.
    UUID가 아닌 항목은 **경고 로그(`AUTO_PUBLISH_HOLD_HOSPITALS: ignoring invalid hospital id`)를 남기고
    무시한다** — 그 병원은 보류되지 않으므로 설정 후 로그와 운영 센터 due 목록으로 반드시 확인한다.
  - 멈추는 것은 08:00~23:00 예약 자동 발행(DRAFT/READY)뿐이다. 수동 발행(`/publish`)과 이미 공개된
    글의 가시성은 바뀌지 않는다. 같은 조건을 07:45 사전 게이트·파이프라인 감시·운영 센터 오늘 목록이
    함께 쓰므로 보류 중인 글로 페이지나 "발행 누락" 거짓 알람이 나가지 않는다.

## 2. 배포 순서

`scripts/deploy.sh`의 실제 규칙을 따른다. backend 세 서비스는 같은 이미지·같은 env 파일을 쓴다.

1. `.env.production`을 **현재 운영 env와 대조**한다. `deploy.sh`는 서비스 env를 이 파일 내용으로 통째로
   바꾼다(`--env-vars-file`). 오래된 파일로 배포하면 다른 설정이 사라진다.
2. 보류를 바로 켤 것인지 정한다(3절). 켜지 않으면 `AUTO_PUBLISH_HOLD_HOSPITALS`를 비워 두거나 적지 않는다.
3. `bash scripts/deploy.sh api` 한 번으로 올린다. 이 명령은 다음 순서로 진행한다.
   이미지 빌드 → **migrate Job(0081)** → worker → RedBeat reconcile → beat → readiness 게이트 → api.
   (`migrate`만 먼저 따로 돌려도 된다: `bash scripts/deploy.sh migrate`.) 스케줄 변경이 없어
   `REDBEAT_SCHEDULE_VERSION`은 그대로다.
4. 세 서비스 모두 새 리비전이 트래픽 100%인지 확인한다.

   ```bash
   for s in reputation-api reputation-worker reputation-beat; do
     gcloud run services describe "$s" --region=asia-northeast3 \
       --format='value(status.latestReadyRevisionName,status.traffic)'
   done
   ```

**withhold는 api·worker·beat 세 개가 100% 새 리비전으로 전환된 뒤에만 사용**한다.
옛 코드는 `WITHHELD` 행을 읽으면 enum `LookupError`로 실패한다 — 공개 상세·이미지가 404 대신 500,
Admin 콘텐츠 목록·월간 리포트·주간 수율·재검증 작업이 예외로 멈출 수 있다. 롤링 도중이나 일부 트래픽이
옛 리비전에 남아 있을 때 withhold를 호출하지 않는다.

## 3. 예약 자동 발행 보류(B) 켜고 끄기

- 값은 api·worker 둘 다에 있어야 한다(발행기·07:45 게이트·감시 태스크는 worker, 운영 센터·감시 API는 api).
  `deploy.sh`는 `.env.production`의 비밀이 아닌 키를 api·worker·beat·migrate에 똑같이 넣으므로,
  `.env.production`에 `AUTO_PUBLISH_HOLD_HOSPITALS=*`(또는 UUID 목록)를 적고 `bash scripts/deploy.sh api`로
  세 서비스를 함께 올리면 된다. 빈 값은 전달되지 않아 기본값 `""`(꺼짐)이 된다.
- 급하게 콘솔/`gcloud run services update --update-env-vars`로 바꿨다면 **같은 값을 `.env.production`에도
  적는다.** 적지 않으면 다음 `deploy.sh`가 값을 지워 보류가 조용히 풀린다. 쉼표가 들어간 목록은
  구분자를 바꿔야 한다: `--update-env-vars='^@^AUTO_PUBLISH_HOLD_HOSPITALS=<uuid>,<uuid>'`.
  worker와 api 둘 다 바꾼다.
- 적용 확인: 운영 센터 오늘 목록에서 보류 병원의 "발행 예정"이 빠졌는지, worker 로그에
  `auto publish skipped: reason=auto_publish_hold`가 찍히는지(후보를 만든 뒤 보류가 켜진 경우) 본다.
- 부작용: 보류 기간의 슬롯은 발행되지 않으므로 그만큼 해당 월 계약 편수가 줄고, 다음 월간 리포트에 반영된다.

### 보류를 풀 때의 위험 — 해제 전에 반드시 목록을 검수한다

자동 발행은 오늘을 포함한 **지난 7일(catch-up 창)** 의 DRAFT/READY를 다시 집는다. 보류를 풀면
그동안 쌓인 보류분이 **다음 정시 실행에서 한꺼번에 발행**된다.

1. 해제 전에 대상 병원의 콘텐츠 탭에서 예정일이 지난 7일 안인 초안·발행 준비 글을 모두 연다.
2. 내보내면 안 되는 글은 `POST .../reschedule`로 내일 이후로 옮기거나, 참고자료·본문을 고친다.
   (발행기의 안전 게이트는 그대로지만, 사람이 확인하려고 보류한 글이라면 게이트 통과만으로 충분하지 않다.)
3. 그 뒤 값을 비우고 배포한다.

## 4. 롤백

- **B만 되돌리기**: `.env.production`에서 값을 비우고 `bash scripts/deploy.sh api`. 새 리비전부터 즉시 원복된다.
  (3절의 "보류를 풀 때의 위험"이 그대로 적용된다.)
- **A(코드) 되돌리기**:
  1. 먼저 WITHHELD 행을 0건으로 만든다 — 각 글을 `restore`(다시 공개)하거나 `reject`(반려, 본문 삭제)한다.
     확인(읽기 전용): `SELECT count(*) FROM content_items WHERE status::text = 'WITHHELD';`
  2. 0건이 된 뒤 `bash scripts/deploy.sh rollback`으로 직전 리비전(배포 시작 때 `.deploy-rollback`에 기록된
     좌표; 설계 기준 5862e557 = api `00208-8wm` / worker `00197-mhl` / beat `00192-28l`)으로 트래픽을 되돌린다.
     리비전마다 env가 따로 저장돼 있으므로 B 설정도 함께 되돌아간다.
  3. DB는 되돌리지 않아도 된다. 남는 enum 값은 어떤 행도 쓰지 않으면 옛 코드에 무해하다.
     굳이 `alembic downgrade 0080_lead_diagnosis_supersede`를 실행하면, 0081 downgrade는 WITHHELD 행이
     하나라도 있으면 **실패하도록** 되어 있고(메시지에 남은 행 수가 나온다), 0건이면 enum 값을 남긴 채
     아무것도 하지 않는다(PostgreSQL은 enum 값을 지울 수 없고, 타입 재생성은 롤백 중 content_items 전체를
     다시 쓰며 잠근다). 다시 올릴 때도 `ADD VALUE IF NOT EXISTS`라 안전하다.

## 5. withhold / restore 호출 방법

사람이 일으키는 admin 변경이므로 확인된 로그인 계정이 필요하다(없으면 403). Admin BFF를 거치면
BFF가 actor 서명을 붙인다. 가장 간단한 방법은 **로그인한 Admin 콘솔 탭의 개발자 도구 콘솔**에서 부르는 것이다
(같은 origin·CSRF 토큰 쿠키를 그대로 쓴다).

```js
// Admin 콘솔(https://<ADMIN_DOMAIN>)에 로그인한 탭의 DevTools 콘솔
const csrf = decodeURIComponent(document.cookie.match(/(?:^|; )admin_csrf=([^;]+)/)[1])
const call = (action, hospitalId, contentId, reason) =>
  fetch(`/api/admin/hospitals/${hospitalId}/content/${contentId}/${action}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Admin-CSRF-Token': csrf },
    body: JSON.stringify({ reason }),
  }).then(async (r) => [r.status, await r.json()])

await call('withhold', '<HOSPITAL_ID>', '<CONTENT_ID>', '원장 요청으로 참고자료 재확인 전까지 내림')
await call('restore', '<HOSPITAL_ID>', '<CONTENT_ID>', '참고자료 확인 완료, 다시 공개')
```

같은 요청을 curl로 보낼 때(세션 쿠키·CSRF 값은 브라우저에서 복사, 공유하지 않는다):

```bash
curl -sS -X POST "https://<ADMIN_DOMAIN>/api/admin/hospitals/<HOSPITAL_ID>/content/<CONTENT_ID>/withhold" \
  -H "Origin: https://<ADMIN_DOMAIN>" \
  -H "Content-Type: application/json" \
  -H "X-Admin-CSRF-Token: <ADMIN_CSRF_COOKIE_VALUE>" \
  -b "admin_session=<ADMIN_SESSION_COOKIE_VALUE>; admin_csrf=<ADMIN_CSRF_COOKIE_VALUE>" \
  -d '{"reason": "원장 요청으로 참고자료 재확인 전까지 내림"}'
```

응답과 규칙:

| 요청 | 성공(200) | 실패 |
|---|---|---|
| withhold | `{"detail":"Withheld","status":"WITHHELD","published_at":"<원래 값>","content_revision":N}` | 403 로그인 계정 확인 불가 · 409 발행(PUBLISHED) 상태가 아님 · 404 글/병원 없음 · 422 사유 3~500자 아님 |
| restore | `{"detail":"Restored","status":"PUBLISHED","published_at":"<원래 값>","content_revision":N}` | 403 · 409 WITHHELD가 아님 · 409 `HOSPITAL_NOT_PUBLIC`(일시정지·비공개·일정 미설정 병원) · 409 `RESTORE_BLOCKED`(`blockers`·`blocker_labels`: 근거 변경 `CONTENT_AUTHORITY_CHANGED`, 운영 기준 불일치, 참고자료 0개 `MISSING_REFERENCES`, 이미지 인증 무효, 검수 지적, 금지 표현 등) |

- 둘 다 감사 기록을 남긴다: `withhold_content` / `restore_content`(actor, reason, published_at, published_by, revision).
- 두 요청 모두 IndexNow 의도와 공개 표면 재검증을 함께 건다(withhold는 `unpublished_from=published_at`).
- restore가 `RESTORE_BLOCKED`면 사유를 고친 뒤(예: 참고자료 PATCH) 다시 부른다. 근거가 바뀐 글
  (`CONTENT_AUTHORITY_CHANGED`)은 되돌리지 않는 것이 맞다 — 반려(reject)로 새로 쓰게 한다.
- WITHHELD 글을 다시 공개할 때 `/publish`를 쓰면 409(`CONTENT_WITHHELD`)다. restore를 쓴다.

## 6. 배포 후 확인(읽기 전용)

- 세 서비스 새 리비전 100%, Cloud Run 새 리비전 ERROR 0건.
- 공개 병원 헬스·공개 글 수가 배포 전과 같다(마이그레이션은 행을 바꾸지 않는다).
- `SELECT status::text, count(*) FROM content_items GROUP BY 1;` — 운영자가 withhold하기 전까지 WITHHELD 0건.
- B를 켰다면 운영 센터 오늘 목록과 worker 로그로 보류 적용을 확인한다(3절).

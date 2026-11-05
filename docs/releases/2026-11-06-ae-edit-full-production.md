# 2026-11-06 운영 배포 — AE 수정 판 전면 확장 (보고서의 모든 글자·숫자)

main `9f4a95cd`(PR #292 `claude/ae-edit-full`)을 2026-11-06 04:48~04:58 KST에 Backend·Admin에 배포했다. 이날 첫 릴리스라 `--override-reason`은 쓰지 않았고,
배치 시간대(04시 앞뒤 45분) 끝인 04:47에 시작했다. 원래 계획은 02:00~03:15였지만 gcloud 인증 만료로 미뤘다.
env 정본은 운영 서비스이고 `--accept-env-diff`는 쓰지 않았다. 마이그레이션은 없다(DB head 그대로).

대표 지시(2026-11-05 오후): "보고서 파일은 AE들이 직접 수정할 수 있어야 한다", "숫자도 자유롭게", "직접 리포트를 수정하는 부분은 UIUX도 매우 중요하고
동작에 오류도 없어야 해". 계약은 CLAUDE.md의 'AE 수정 판' 항목과 [템플릿 갱신](../ops/monthly-template-refresh.md)의 'AE 수정 판' 절에 있다.

## 바뀐 것

- **범위**: 원장용 v4 보고서에 찍히는 모든 글자를 고치거나 지울 수 있다. 제목·카드·핵심 3줄·안내문·막대·표의 모든 칸·히트맵·경쟁 표·사례·계획 표·부록 A~D와,
  템플릿에 박혀 있던 소제목·범례·캡션·머리글이 대상이다. 지우기는 문장·목록 항목·표 행 단위로 하고, 부록 D처럼 절 전체도 지울 수 있다.
  고칠 수 없는 것은 병원명·기간·링크 주소·마스트헤드다. 본문 6절과 부록은 저마다 한 글자는 남아야 한다(`SECTION_EMPTY`).
- **숫자**: 숫자 보존 게이트(`AE_EDIT_NUMBERS`·`AE_EDIT_FACTS`·'원문에 없는 숫자')를 없앴다. 원장 PDF 검증·전달 게이트·postcheck는 고친 뷰 자신의 사실과 대조한다.
  측정값과 달라진 숫자는 거절하지 않는다. WARN `AE_EDIT_NUMBERS_CHANGED`·`ae_overrides.number_changes`·감사 로그에 남기고, Admin에는 '측정값과 다름'으로 표시한다.
  의료광고 금지 표현 필터와 내부 표식 검사는 그대로다.
- **저장 형식**: `render_inputs.ae_overrides` schema 2(fields·pristine `base_view`·history·number_changes·fact_changes).
  승계 규칙은 경로 등급(`path_class`)으로 정한다.
- **Admin 화면(`ReportAeEditPanel`)**: 보고서 순서(01~06·부록)대로 모든 항목이 보이고, 항목마다 '지우기'·'원래대로'가 있다.
  PDF에서 본 문장이나 숫자를 찾는 '문장 찾기', '고친 항목만 보기', 절별 고친 수를 보여 준다. 버튼 줄은 하단에 고정된다.
  '저장 전 검증'(`POST …/ae-edit/preview`) 뒤에 패널 안에서 PDF를 미리 볼 수 있고(`POST …/ae-edit/preview-pdf`, '새 탭으로 열기' 포함), 버튼이 꺼진 이유를 한 줄로 알려 준다.
  고친 채 닫으면 확인을 묻는다. Admin CSP에 `frame-src 'self' blob:`을 더했다. 패널 안 PDF iframe이 막혔던 것을 브라우저 검증에서 발견해 고쳤다.

## 배포 전 검증

- Backend CI는 main과 같은 16건 실패뿐이다(보안 스캔·Terraform fmt·리허설 등 기존 실패, 새 실패 0). Admin 테스트 680/680, Copy guard, Docker 빌드 3종 통과.
- Codex(`gpt-6.1-sol`, xhigh, read-only) 정식 3차 검토 뒤 마지막 UX 커밋 재검토: "차단 결함 없음".
- 로컬 production build에서 브라우저로 마케터 예시를 조작해 확인했다. 증거는 `.omo/evidence/ae-full-ux/` 01~15다.
  - '실패 61회' 숫자 수정은 '측정값과 다름'으로 표시된다.
  - '순위나 환자 수 증가를 약속하지 않습니다', 부록 D 전체, 부록 B 전체, 'AI 인용' 줄을 지울 수 있다.
  - 패널 안 PDF 미리보기, 새 버전 2 생성(10쪽, 지운 문장 없음) 모두 확인했다.
  - 금지 표현 오류는 해당 칸 아래에 표시된다. 네트워크 실패 안내, 375px 모바일 화면도 확인했다.
  - '측정 방식이 바뀌어…' 문장 삭제와 빈 절 거절은 화면 fixture에 해당 사례가 없어 테스트로만 덮었다.

## 리비전

| 서비스 | 리비전 | 이미지 |
| --- | --- | --- |
| API | `reputation-api-00281-5mp` | `reputation:20261106-044812` |
| Worker | `reputation-worker-00271-dt8` | 같음 |
| Beat | `reputation-beat-00266-2nn` | 같음 |
| Admin | `reputation-admin-00118-xss` | `admin:20261106-045734` |
| Site | `reputation-site-00149-6ck` | 그대로 |

롤백 좌표는 `.omo/rollback-aefull/backend`(api `00280-sgl`·worker `00270-dnm`·beat `00265-k6z`)와 `.omo/rollback-aefull/admin`(`00117-jjv`)이다.
첫 실행(04:47)은 롤백 좌표 디렉터리가 없어 운영 변경 전에 멈췄고, 그날의 배포 잠금은 스크립트가 되돌렸다. 디렉터리를 만든 뒤 다시 실행했다.

## 배포 뒤 검증

- readiness Job `reputation-production-readiness-klskz` 성공, `/api/v1/health/live` release `9f4a95cd`, 공개 표면 smoke 통과.
- 새 리비전 ERROR 0건(04:48 이후, API·Worker·Beat·Admin). Admin 로그인 화면 200, 응답 CSP `frame-src 'self' blob:`.
- **AE 미리보기**(장편한외과의원 10월 최신 판 `7d744c36`, 쓰기 없는 `GET …/ae-edit/preview`):
  - 편집 항목은 463개다. 옛 버전(`76a1478a`)은 `AE_EDIT_NOT_LATEST`로 거절되고 최신 버전 id를 돌려준다.
  - 아래 수정을 한 번에 넣으면 문제 0건이고, 미리보기에서 해당 문장이 모두 사라진다.
    - 부록 B(4줄)·부록 D(3줄) 전부 지우기
    - '측정 방식이 바뀌어 지난달과 직접 비교하지 않았습니다…'(`summary.notice`) 지우기
    - '계획 150회 / 확인 89회 / 실패 61회' → '계획 150회 / 확인 89회'
    - 범례 '빗금 측정 실패' 빼기
    - 막대 값 3 → 4
  - 막대 값 변경은 `number_changes`에 기록된다. '완치를 보장합니다'는 `FORBIDDEN_EXPRESSION`, '내부 검수용'은 `INTERNAL_MARKER`로 거절된다.
  - `POST` 미리보기·PDF 미리보기·저장은 사람 계정 서명(`ACTOR_ASSERTION_REQUIRED`)이 필요해 서버 키로는 403이다(설계). 로그인한 Admin 화면에서 확인한다.

## 남은 일

- 로그인한 Admin 화면에서 패널 조작·패널 안 PDF 미리보기를 운영으로 확인한다. AE 실제 저장(새 버전 만들기)은 운영에서 아직 실행하지 않았다.
- 10월분은 아직 원장에게 전달하지 않았다. AE가 고친 새 버전도 다시 전달해야 반영된다.
- 경쟁 병원이 없는 3곳(장편한·연세속시원·신기한속)은 등록 뒤 '경쟁 병원 반영'을 돌린다.

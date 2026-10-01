# 참고자료 출처 가드 mutation 표

`reference_guard_mutants.py`는 참고자료 가드(PR #177)를 하나씩 제거하는 한 줄 편집을 적용한다.
그다음 그 가드를 잡아야 하는 pytest node id를 돌리고, 결과(CAUGHT/SURVIVED)를 적은 뒤 원본을
메모리 사본으로 되돌린다. 파일 이름이 `test_*.py`가 아니므로 pytest가 수집하지 않는다.

```bash
# 대상 문자열이 모두 정확히 한 번씩 있는지만 본다(테스트 실행 없음, DB 불필요)
python3 backend/tests/mutation/reference_guard_mutants.py --check-targets

# 전체 표 — 격리 하네스 안에서만
python3 backend/tests/mutation/reference_guard_mutants.py --out /tmp/mutants.md
```

- 표는 stdout에 쓴다. `--out`을 주면 그 경로에도 쓴다. 모든 행이 CAUGHT이면 종료 코드 0이다.
- 일부 행은 PG 테스트를 부른다. 그래서 `.github/workflows/ci.yml` backend 잡 env에 있는 DB·Redis
  URL을 **모두** export한 격리 하네스 안에서 돌린다. 모든 URL은 일회용 `_test` DB와 일회용 Redis를
  가리켜야 한다.
  - 공유 개발 DB(5434·55432)와 운영 DB에는 절대 연결하지 않는다.
  - 변수가 비면 그 행은 mutation 결과가 아니라 BASELINE-FAIL로 나온다.
- 네트워크는 쓰지 않는다. 모든 GET은 테스트 더블이고, 운영 외 환경의 기본 fetcher는 오프라인이다.
- 스크립트는 작업 트리의 파일을 잠시 고친다. 같은 트리에서 두 개를 동시에 돌리지 않는다. 중단됐으면
  `git diff`로 복원 여부를 확인한다.
- 가드를 옮기거나 문구를 바꾸면 `--check-targets`가 실패한다. 대상 문자열을 함께 고친다.

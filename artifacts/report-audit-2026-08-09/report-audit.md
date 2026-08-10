# Re:putation diagnosis-report PDF audit — 2026-08-09

## Verdict

The current `lead-v1` lead diagnosis renderer is legible, but it is written and ordered like a methods appendix: the first useful result is a five-column measurement table, followed by model IDs, the full system instruction, reproducibility caveats, and full query text. A hospital director has to translate API/model jargon before learning what the number means or what happens next. The report also ends with a negative “not provided” list rather than a clear service hand-off.

The existing checked-in sample PDFs are more severe: all eight PDFs under `reports/` render Korean as empty square glyphs at 120/180 dpi. The samples embed `DejaVu-Sans` rather than a Korean-capable font, so the delivered document is not reliably readable. The separate `doctor_report.html` fixture renders Korean correctly, but spills into a mostly blank second page despite `generate_doctor_pdf_report()` documenting it as a one-page PDF.

## What was actually inspected

- `lead-fixture.pdf`: generated from the real `lead_report.build_lead_report_payload()` and `render_lead_report_pdf()` functions using the existing `test_lead_report_blur.py`-shaped diagnosis/results fixture. It is one A4 page and was rendered to `lead-fixture-page-1.png`.
- Eight existing PDFs in `reports/`: each was rendered with `pdftoppm`; every file is one A4 page. `sample-contact-sheet.png` is the complete page contact sheet; `jangpyeonhan-v0-page-1.png` is the 180 dpi representative V0 page.
- `doctor-fixture.pdf`: rendered from `doctor_report.html` with the existing `test_doctor_report_view.py` fixture. It is two A4 pages; both pages were inspected.

The fixture is deterministic, not live production data. No live DB/provider credentials were available, and the backend venv initially could not load WeasyPrint's `libgobject`; rendering succeeded only after using the installed Homebrew libraries via `DYLD_LIBRARY_PATH=/opt/homebrew/lib`. The fixture therefore validates the current renderer and layout, not live model quality or real hospital performance.

## Visual findings

### P0 — delivered glyph failure in existing samples

`reports/jangpyeonhan-surgery-demo_V0-진단.pdf` and the other existing samples render title, labels, and body Korean as square boxes. `pdffonts` reports only `DejaVu-Sans` / `DejaVu-Sans-Bold` in the V0 sample (`sample-jangpyeonhan-surgery-demo_V0-진단-pdffonts.txt`), while the template asks for `NanumGothic`, `Malgun Gothic`, and `Apple SD Gothic Neo` (`backend/app/templates/report.html:8`). This is a font-resolution/embedding defect, not a copy concern. A director cannot read the hospital name, headings, caveat, or action table in the actual sample.

The legacy V0 path is still active: `generate_pdf_report()` loads `report.html` (`backend/app/services/report_engine.py:408-469`), and the worker invokes it for V0 (`backend/app/workers/tasks.py:574-584`). Fixing only the newer lead template will not repair already-delivered V0 PDFs.

### P0 — lead-v1 is legible but not director-first

The rendered lead fixture is visually clean (one A4 page, consistent Korean font, no clipping), but hierarchy is report-engineering-first:

1. The opening result is `측정 결과` and a dense table (`backend/app/templates/lead_report.html:47-76`), not a plain-language headline such as “AI 답변 16번 중 4번에서 병원이 언급됨”.
2. The headline says “전체 16번의 측정 중 4번 언급되었습니다” (`lead_report.html:74-76`) but gives no immediate interpretation, benchmark, or next step. The only interpretation is a caveat explaining what 0 means (`lead_report.html:84-88`), which is irrelevant for non-zero results.
3. The next section is explicitly labelled `측정 조건 — 전문 공개` and shows judge/model IDs plus the full system instruction (`lead_report.html:95-109`). In the image this is a large table and a long paragraph of “judge/target” methodology. It answers an auditor’s question before a director’s question.
4. The full query list and timestamps occupy another large table (`lead_report.html:111-135`). Reproducibility is valuable, but it should be an appendix or a compact “측정한 질문 3개” callout.
5. The payload is structurally prohibited from carrying actions (`lead_report.py:84-99`; module contract at `lead_report.py:12-22`). Consequently, the PDF can only state what was measured; it cannot turn the result into a useful diagnosis or sales conversation.
6. The final block leads with “이 진단에서는 제공하지 않습니다” and a list of locked items (`lead_report.html:138-146`). It feels like a paywall/error state and contains no prominent next-step CTA. The notices (`lead_report.html:148-150`) are legal/process disclosures, not a human hand-off.

The page is not visually broken, but its monotonous white background, hairline rules, table density, and code-style model IDs make it read like a technical export. There is no chart, signal/quality indicator, “what this means for your hospital” sentence, or contact action in the current lead template.

### P1 — doctor report pagination contradicts its product promise

The doctor view is intentionally more human (large “47번”, question headings, evidence cards), but the fixture is two pages. Page 1 ends after the “다음 달에는 무엇을 하나요?” bullets; page 2 contains only the yellow “원장님께 부탁드립니다” box and three pale footnotes, with most of the page blank. This is an orphaned continuation rather than a deliberate second page.

The code calls this a “1페이지 PDF” (`backend/app/services/report_engine.py:661-669`), yet `doctor_report.html` has no explicit pagination strategy (`doctor_report.html:11-47`, `:116-131`). The large headline/card spacing and footer margin push the last blocks over the page boundary. Either fit a genuinely one-page version or introduce a deliberate “2. 근거와 다음 액션” second page with a page header; do not leave a one-box page.

### P1 — legacy report copy and enums expose internal language

The existing monthly sample contains `Plan.PLAN_16` in the “요금제” KPI. This comes directly from `report.html:77-85`, which prints `hospital.plan` without a human label. The old template also uses emoji in section headings (`report.html:52`, `:76`, `:89`, `:132`, `:160`, `:170`, `:180`); the rendered legacy samples show these as inconsistent glyphs alongside the Korean square failure. Replace enum values with “월 16편” and use text labels or an icon system with verified font support.

## Sales and comprehension barriers

| Barrier | What the director sees | Why it weakens the sale |
|---|---|---|
| Result without meaning | 5-column table, then “16번 중 4번” | No answer to “is this good/bad and why should I care?” |
| Technical vocabulary | `OpenAI API · gpt-5.6-luna`, judge model, system instruction | Sounds like an internal benchmark, not a patient-discovery outcome |
| Methodology before value | Full prompt and all query timestamps on page 1 | Cognitive load arrives before trust or relevance |
| Negative paywall | “제공하지 않습니다” list | Frames the offer as withheld information instead of a clear next step |
| No conversion path | Only legal/AI notices at the bottom | A director cannot act immediately after seeing the diagnosis |
| Broken legacy output | Korean glyph boxes in actual samples | Destroys trust before the reader can evaluate the number |
| Accidental second page | Doctor PDF page 2 is mostly blank | Feels unfinished and increases printing/email friction |

## Prioritized redesign blueprint

### P0 — make every delivered PDF readable

1. Make the PDF renderer deterministic: package a Korean font asset and use `@font-face` in `lead_report.html`, `doctor_report.html`, and `report.html`; fail the build if `pdffonts` does not show the expected embedded Korean font. Do not rely on host font fallback.
2. Keep one canonical production path per audience. If `lead-v1` is the intended lead artifact, stop creating new lead PDFs through the legacy `report_engine.generate_pdf_report(..., report_type="V0")` path, or update the legacy template in the same release.
3. Add a rendered-glyph smoke test: generate a fixture, run `pdftoppm`, and assert that the title region is not a high percentage of empty square glyphs. Keep `REQUIRE_PDF_RENDER=1` in CI; the current report tests pass 42/42 but do not inspect glyph appearance.

### P0 — redesign page 1 around a director’s decision

Recommended first-page order:

1. **Headline:** “이번 진단에서 AI 답변 16번 중 4번에 장편한외과의원이 언급되었습니다.”
2. **Meaning line:** “이 수치는 진료의 질이나 환자 수가 아니라, 공개된 질문 세트에서 병원 정보가 답변에 포함된 횟수입니다.”
3. **Signal card:** 4/16 successful answers, 2 failed requests, measurement period, and a plain-language quality badge (“8/9 successful per model”).
4. **Why it matters / next step:** one concrete, non-guaranteed action preview (“월간 리포트에서는 언급되지 않은 질문과 근거 콘텐츠를 연결해 다음 발행 주제를 정합니다.”) plus a contact/booking CTA.
5. **Evidence strip:** 2–3 patient questions, collapsed/shortened; move full timestamps and reproducibility detail to the appendix.

The current `LeadReportPayload` does not carry the analysis period, interpretation, or CTA data (`lead_report.py:84-99`). Add explicit fields rather than making the Jinja template infer them.

### P1 — put methodology in an appendix, not the pitch

Rename “측정 조건 — 전문 공개” to “어떻게 측정했나요?” and show a three-line summary on page 1: question count, repeats, and providers. Put raw system prompt and model IDs on an appendix page with a short glossary. Replace “자와 대상이 함께 움직여…” (`lead_report.html:106-109`) with a plain sentence; keep the rigorous explanation available for AE/auditor review.

### P1 — turn the paywall into a constructive hand-off

Replace `lead_report.html:138-146` with “다음 월간 리포트에서 확인할 수 있는 것” and three benefit cards: missing-question evidence, competitor/context evidence, and prioritized content actions. Add a single CTA (“상담에서 병원 질문 세트를 함께 검토하기”) and contact details. This preserves the intentional data boundary in `lead_report.py:12-22` without making the page feel like an error.

### P1 — make pagination intentional

For `doctor_report.html`, either reduce vertical spacing and evidence copy until the tested fixture is exactly one page, or add an explicit page break before “원장님께 부탁드립니다” and label page 2 as “다음 달 실행과 참고사항”. Add `break-inside: avoid`, stable footer/header, and a page-count assertion. The current `generate_doctor_pdf_report()` one-page claim must match the artifact.

### P2 — remove internal labels and unverified glyphs

Map `PLAN_16` to “월 16편”, `PLAN_12` to “월 12편”, etc. Replace emoji section labels in `report.html` with text/icon assets whose font support is tested. Use “AI 모델”, “성공 답변”, “언급된 답변”, and “응답 오류” in director-facing tables; retain API/model IDs only in the methodology appendix.

## Manual QA matrix

| Scenario | Criterion | Surface / exact invocation | Verdict | Artifact refs |
|---|---|---|---|---|
| S1 | Lead PDF is generated and visually inspectable | `APP_ENV=development ADMIN_SECRET_KEY=x DYLD_LIBRARY_PATH=/opt/homebrew/lib backend/.venv/bin/python artifacts/report-audit-2026-08-09/render_lead_fixture.py`; `pdfinfo`, `pdftoppm -png -r 180` | PASS (1 A4 page, no clipping; comprehension defects recorded above) | A1, A2, A3 |
| S2 | Existing V0 samples remain readable | For each `reports/*.pdf`: `pdfinfo`, `pdffonts`, `pdftoppm -png -r 120`; inspect complete contact sheet and representative 180 dpi page | FAIL — Korean glyphs render as square boxes; embedded font is DejaVu-Sans | A4, A5, A6 |
| S3 | Director report honors one-page contract | `APP_ENV=development ADMIN_SECRET_KEY=x DYLD_LIBRARY_PATH=/opt/homebrew/lib backend/.venv/bin/python artifacts/report-audit-2026-08-09/render_doctor_fixture.py`; inspect both rendered pages | FAIL — 2 pages, page 2 mostly blank | A7, A8, A9 |
| S4 | Source-level report tests pass | `cd backend && APP_ENV=development ADMIN_SECRET_KEY=x DYLD_LIBRARY_PATH=/opt/homebrew/lib REQUIRE_PDF_RENDER=1 .venv/bin/pytest -q tests/test_lead_report_blur.py tests/test_doctor_report_view.py` | PASS — 42 passed; tests do not catch glyph fallback or page overflow | A10 |

### Adversarial cases

| Scenario | Criterion | Adversarial class | Expected behavior | Verdict | Artifact refs |
|---|---|---|---|---|---|
| A1 | Font portability | Missing/incorrect host font | Korean text remains readable with embedded asset | FAIL on legacy samples; lead fixture passes only because this host resolves Apple SD Gothic Neo | A4, A5, A6 |
| A2 | Director comprehension | Technical-methodology overload | Page 1 states result, meaning, and next step before raw model/prompt details | FAIL — current lead page leads with table and methodology | A2, A3; `lead_report.html:47-109` |
| A3 | Pagination | Content overflow | A one-page doctor report is one page, or a deliberate second page is designed | FAIL — orphaned page 2 | A7, A8; `report_engine.py:661-669` |
| A4 | Data realism | Live provider/DB unavailable | Run against production-like measurement data | BLOCKED — no live credentials/DB; deterministic fixture used; no product verdict inferred | A11 |

## Artifact references

| ID | Kind | Description | Path |
|---|---|---|---|
| A1 | PDF | Current `lead-v1` fixture PDF | `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/lead-fixture.pdf` |
| A2 | Screenshot | Current lead PDF, complete page at 180 dpi | `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/lead-fixture-page-1.png` |
| A3 | Text/metadata | Lead `pdfinfo`, `pdffonts`, and extracted text | `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/lead-fixture-pdfinfo.txt`, `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/lead-fixture-pdffonts.txt`, `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/lead-fixture-text.txt` |
| A4 | Screenshot | All eight existing sample PDF pages, contact sheet | `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/sample-contact-sheet.png` |
| A5 | Screenshot | Existing V0 sample at 180 dpi; visible Korean square glyphs | `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/jangpyeonhan-v0-page-1.png` |
| A6 | Text/metadata | Existing V0 `pdfinfo`/`pdffonts` and extracted text | `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/sample-jangpyeonhan-surgery-demo_V0-진단-pdfinfo.txt`, `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/sample-jangpyeonhan-surgery-demo_V0-진단-pdffonts.txt`, `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/legacy-v0-text.txt` |
| A7 | PDF | `doctor_report.html` fixture PDF | `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/doctor-fixture.pdf` |
| A8 | Screenshot | Doctor PDF page 1 | `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/doctor-fixture-page-1.png` |
| A9 | Screenshot/metadata | Doctor PDF page 2 (orphaned tail), `pdfinfo`, extracted text | `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/doctor-fixture-page-2.png`, `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/doctor-fixture-pdfinfo.txt`, `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/doctor-fixture-text.txt` |
| A10 | Test log | Existing lead/doctor report tests | `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/report-tests.txt` |
| A11 | Execution note | Fixture renderer + inability to use live data | `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/lead-fixture-render.log` |

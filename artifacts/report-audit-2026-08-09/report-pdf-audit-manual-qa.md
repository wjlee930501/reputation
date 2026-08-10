# Manual QA matrix — diagnosis report PDFs (2026-08-09)

## surfaceEvidence

| scenario id | criterion reference | surface | exact invocation | verdict | artifactRefs |
|---|---|---|---|---|---|
| S1 | Lead diagnosis PDF is generated and inspectable | `lead_report.py` → `lead_report.html` → PDF | `APP_ENV=development ADMIN_SECRET_KEY=x DYLD_LIBRARY_PATH=/opt/homebrew/lib backend/.venv/bin/python artifacts/report-audit-2026-08-09/render_lead_fixture.py`; `pdfinfo`; `pdftoppm -png -r 180` | PASS: 1 A4 page, no clipping; director comprehension issues remain | A1,A2,A3 |
| S2 | Existing sample V0 PDFs readable | `reports/*.pdf` rendered pages | For each sample: `pdfinfo`, `pdffonts`, `pdftoppm -png -r 120`; inspect contact sheet + representative 180 dpi page | FAIL: Korean glyphs are square boxes; V0 embeds DejaVu-Sans | A4,A5,A6 |
| S3 | Doctor monthly report obeys one-page contract | `doctor_report.html` → `generate_doctor_pdf_report()` | `APP_ENV=development ADMIN_SECRET_KEY=x DYLD_LIBRARY_PATH=/opt/homebrew/lib backend/.venv/bin/python artifacts/report-audit-2026-08-09/render_doctor_fixture.py`; inspect both pages | FAIL: 2 pages; page 2 is mostly blank tail | A7,A8,A9 |
| S4 | Existing report tests pass | lead/doctor report tests | `cd backend && APP_ENV=development ADMIN_SECRET_KEY=x DYLD_LIBRARY_PATH=/opt/homebrew/lib REQUIRE_PDF_RENDER=1 .venv/bin/pytest -q tests/test_lead_report_blur.py tests/test_doctor_report_view.py` | PASS: 42 passed; does not catch font fallback/page overflow | A10 |

## adversarialCases

| scenario id | criterion reference | adversarial class | expected behavior | verdict | artifactRefs |
|---|---|---|---|---|---|
| A1 | Font portability | Missing/incorrect host font | Korean text stays readable via embedded font | FAIL on legacy samples; fixture passes only via host font resolution | A4,A5,A6 |
| A2 | Director comprehension | Technical-methodology overload | First page leads with result, meaning, next step; raw prompt/models appendix | FAIL: lead page starts with measurement table and full methodology | A2,A3 |
| A3 | Pagination | Content overflow | One-page contract is true or second page is intentionally designed | FAIL: orphaned doctor page 2 | A7,A8,A9 |
| A4 | Data realism | Live provider/DB unavailable | Run against production-like measurement data | BLOCKED: deterministic fixture only; no live credentials/DB | A11 |

## artifactRefs

| id | kind | description | path |
|---|---|---|---|
| A1 | PDF | Current lead-v1 fixture | `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/lead-fixture.pdf` |
| A2 | screenshot | Lead page 1 at 180 dpi | `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/lead-fixture-page-1.png` |
| A3 | metadata | Lead PDF info/fonts/text | `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/lead-fixture-pdfinfo.txt` |
| A4 | screenshot | All existing sample pages contact sheet | `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/sample-contact-sheet.png` |
| A5 | screenshot | Existing V0 representative, visible square glyphs | `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/jangpyeonhan-v0-page-1.png` |
| A6 | metadata | V0 `pdfinfo`/`pdffonts`/text | `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/sample-jangpyeonhan-surgery-demo_V0-진단-pdffonts.txt` |
| A7 | PDF | Doctor fixture PDF | `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/doctor-fixture.pdf` |
| A8 | screenshot | Doctor page 1 | `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/doctor-fixture-page-1.png` |
| A9 | screenshot | Doctor page 2 orphaned tail | `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/doctor-fixture-page-2.png` |
| A10 | test log | 42 passing report tests | `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/report-tests.txt` |
| A11 | execution note | Fixture/live-data limitation | `/Users/woojinlee/Documents/projects/reputation/artifacts/report-audit-2026-08-09/lead-fixture-render.log` |

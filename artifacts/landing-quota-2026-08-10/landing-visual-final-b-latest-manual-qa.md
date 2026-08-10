# Landing visual QA — latest-tree re-audit

Read-only re-audit after the design-token CSS patch and final recapture. `AGENTS.md` and `DESIGN.md` were re-read; all five latest `live-*-final.png` files were opened with `detail=original` and validated with `file`.

## surfaceEvidence

| scenario id | criterion reference | surface | exact invocation | verdict | artifactRefs |
|---|---|---|---|---|---|
| L-LATEST-DSK-01 | desktop responsive surface | `/` landing hero + quota, 1280×900 | `file artifacts/landing-quota-2026-08-10/live-desktop-final.png`; `view_image(path=.../live-desktop-final.png, detail=original)` | PASS | A1 |
| L-LATEST-TAB-01 | tablet responsive surface | `/` landing hero + quota + evidence band, 768×900 | `file artifacts/landing-quota-2026-08-10/live-tablet-final.png`; `view_image(path=.../live-tablet-final.png, detail=original)` | PASS | A2 |
| L-LATEST-MOB-01 | mobile hero/quota and CJK wrapping | `/` landing top viewport, 375×812 | `file artifacts/landing-quota-2026-08-10/live-mobile-final.png`; `view_image(path=.../live-mobile-final.png, detail=original)` | PASS | A3 |
| L-LATEST-FORM-01 | diagnosis instructions/API token and mobile form | `/ai-diagnosis` top viewport, 375×812 | `file artifacts/landing-quota-2026-08-10/live-form-mobile-final.png`; `view_image(path=.../live-form-mobile-final.png, detail=original)`; source audit `site/app/globals.css:8422-8490` | FAIL | A4, S1, S2 |
| L-LATEST-CTA-01 | final CTA mobile surface | `/` landing final CTA, 375×812 | `file artifacts/landing-quota-2026-08-10/live-cta-mobile-final.png`; `view_image(path=.../live-cta-mobile-final.png, detail=original)` | PASS | A5 |

## adversarialCases

| scenario id | criterion reference | adversarial class | expected behavior | verdict | artifactRefs |
|---|---|---|---|---|---|
| L-LATEST-BOUNDS-01 | responsive bounds | clipping / horizontal overflow | Requested frames keep visible content inside their image bounds. | PASS | A1, A2, A3, A4, A5 |
| L-LATEST-CJK-01 | natural Korean wrapping | orphaned particles / broken semantic phrases | Korean copy wraps at phrase boundaries without detached particles or tofu. | PASS | A2, A3, A4, A5 |
| L-LATEST-GEMINI-01 | diagnosis method copy | source/API identifier wrapping | `Gemini API` remains one unbroken token in the 375px instructions. | PASS | A4, S1 |
| L-LATEST-QUOTA-01 | live quota truthfulness | unavailable / stale data state | Valid slots show live values; null/failed fetch shows `접수 현황 · 실시간 확인 중` without a guessed count. | PASS | A1, A3, A4, A5, S3 |
| L-LATEST-TOUCH-01 | mobile interaction sizing | undersized major controls | Visible landing CTA/toggle and diagnosis inputs/inline correction have ≥44px CSS heights. | PASS | A3, A4, A5, S4 |
| L-LATEST-HIER-01 | diagnosis explanatory surface | lost surface contrast / hierarchy regression | The `어떻게 재나요` explanatory block remains a visually distinct inset surface from the page background. | FAIL | A4, S2 |

## artifactRefs

| id | kind | description | path |
|---|---|---|---|
| A1 | screenshot | Latest desktop RGB PNG, 1280×900; SHA-256 `11fd5a5d3bba741536cd54618abfd70f536d6e1401235a6100837a2d3f10b36f` | `artifacts/landing-quota-2026-08-10/live-desktop-final.png` |
| A2 | screenshot | Latest tablet RGB PNG, 768×900; SHA-256 `e7a2e94c07b15dfc8a6ac47bda944d0f5152b75d8d316db7f2cc3eec894e06c4` | `artifacts/landing-quota-2026-08-10/live-tablet-final.png` |
| A3 | screenshot | Latest landing mobile RGB PNG, 375×812; SHA-256 `e805c34692379ff2591ab1b56df4fda13f107923749cfe00d4a7ae86206b0b2f` | `artifacts/landing-quota-2026-08-10/live-mobile-final.png` |
| A4 | screenshot | Latest diagnosis-form mobile RGB PNG, 375×812; SHA-256 `b61d0347805d375e76462e2ca67f1cd59cd10eb642b8dbe13787bcbfc484c27b` | `artifacts/landing-quota-2026-08-10/live-form-mobile-final.png` |
| A5 | screenshot | Latest CTA mobile RGB PNG, 375×812; SHA-256 `e65cd22830972d57464a01aca9ac83b415ac7ec60d07928dd280ad40367b0531` | `artifacts/landing-quota-2026-08-10/live-cta-mobile-final.png` |
| S1 | source | `.dg-no-break` keeps `ChatGPT(OpenAI API)` and `Gemini API` intact | `site/app/ai-diagnosis/page.tsx:35-40`; `site/app/globals.css:8490` |
| S2 | source | Tokenized diagnosis surface maps `--dg-surface-subtle` to `--color-revisit-coolgrey-95` | `site/app/globals.css:8422-8458` |
| S3 | source | Quota renderer/source fallback and no-store slot proxy | `site/app/_components/DiagnosisQuota.tsx:34-49`; `site/app/api/diagnosis/slots/route.ts:5-20` |
| S4 | source | 44px input/hint and mobile CTA/toggle sizing | `site/app/globals.css:8478-8489`; `site/app/globals.css:2324-2329, 2350-2377` |

## verdict

**FAIL.** Five latest captures are dimensionally valid and pass bounds, CJK/API wrapping, quota truthfulness, and visible touch-size checks. The `/ai-diagnosis` explanatory panel loses its intended visual anchor: `--dg-surface-subtle` resolves to `#f9fbfe`, identical to the page’s `--paper` background, so the rounded inset surface is visually absent in `live-form-mobile-final.png` (A4). Map it to a contrasting cool-grey surface token (for example `--color-revisit-coolgrey-90`) while retaining the token-only implementation, then recapture this surface.

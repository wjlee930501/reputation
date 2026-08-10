# Landing visual QA — final latest-tree re-audit

Read-only re-audit after the `--dg-surface-subtle` correction. The five latest captures were opened at original resolution and validated with `file`.

## surfaceEvidence

| scenario id | criterion reference | surface | exact invocation | verdict | artifactRefs |
|---|---|---|---|---|---|
| L-L2-DSK-01 | desktop responsive surface | `/` landing hero + quota, 1280×900 | `file artifacts/landing-quota-2026-08-10/live-desktop-final.png`; `view_image(path=.../live-desktop-final.png, detail=original)` | PASS | A1 |
| L-L2-TAB-01 | tablet responsive surface | `/` landing hero + quota + evidence band, 768×900 | `file artifacts/landing-quota-2026-08-10/live-tablet-final.png`; `view_image(path=.../live-tablet-final.png, detail=original)` | PASS | A2 |
| L-L2-MOB-01 | mobile hero/quota and CJK wrapping | `/` landing top viewport, 375×812 | `file artifacts/landing-quota-2026-08-10/live-mobile-final.png`; `view_image(path=.../live-mobile-final.png, detail=original)` | PASS | A3 |
| L-L2-FORM-01 | diagnosis instructions, inset surface, and mobile form | `/ai-diagnosis` top viewport, 375×812 | `file artifacts/landing-quota-2026-08-10/live-form-mobile-final.png`; `view_image(path=.../live-form-mobile-final.png, detail=original)`; source audit `site/app/globals.css:8422-8522` | PASS | A4, S1, S2 |
| L-L2-CTA-01 | final CTA mobile surface | `/` landing final CTA, 375×812 | `file artifacts/landing-quota-2026-08-10/live-cta-mobile-final.png`; `view_image(path=.../live-cta-mobile-final.png, detail=original)` | PASS | A5 |

## adversarialCases

| scenario id | criterion reference | adversarial class | expected behavior | verdict | artifactRefs |
|---|---|---|---|---|---|
| L-L2-BOUNDS-01 | responsive bounds | clipping / horizontal overflow | Requested frames keep visible content inside image bounds. | PASS | A1, A2, A3, A4, A5 |
| L-L2-CJK-01 | natural Korean wrapping | orphaned particles / broken semantic phrases | Korean copy wraps at phrase boundaries without detached particles or tofu. | PASS | A2, A3, A4, A5 |
| L-L2-GEMINI-01 | diagnosis method copy | source/API identifier wrapping | `Gemini API` remains one unbroken token in 375px instructions. | PASS | A4, S1 |
| L-L2-QUOTA-01 | live quota truthfulness | unavailable / stale data state | Valid slots show live values; null/failed fetch shows `접수 현황 · 실시간 확인 중` without a guessed count. | PASS | A1, A3, A4, A5, S3 |
| L-L2-TOUCH-01 | mobile interaction sizing | undersized major controls | Visible landing CTA/toggle and diagnosis inputs/inline correction expose ≥44px CSS heights. | PASS | A3, A4, A5, S4 |
| L-L2-HIER-01 | diagnosis explanatory surface | lost surface contrast / hierarchy regression | `어떻게 재나요` remains a visually distinct inset surface from the page background. | PASS | A4, S2 |

## artifactRefs

| id | kind | description | path |
|---|---|---|---|
| A1 | screenshot | Latest desktop RGB PNG, 1280×900; SHA-256 `87b82a455256dedd26e87128e7c3a9fc8aa147bb8ab7b7c5d75a749657f5b4c2` | `artifacts/landing-quota-2026-08-10/live-desktop-final.png` |
| A2 | screenshot | Latest tablet RGB PNG, 768×900; SHA-256 `c0c089b87e2b39a79b225d5e5a41afab6dd343e1a9be9908bcbd4d2f8c4114d6` | `artifacts/landing-quota-2026-08-10/live-tablet-final.png` |
| A3 | screenshot | Latest landing mobile RGB PNG, 375×812; SHA-256 `5cbc92a56d76c5332a837010df3f63d7dd76fd482568272e9fbb2d867701795e` | `artifacts/landing-quota-2026-08-10/live-mobile-final.png` |
| A4 | screenshot | Latest diagnosis-form mobile RGB PNG, 375×812; SHA-256 `e0e5a09a5f01bdb22062a464b74596569980121ffe277681667bdc9b16a62d8a` | `artifacts/landing-quota-2026-08-10/live-form-mobile-final.png` |
| A5 | screenshot | Latest CTA mobile RGB PNG, 375×812; SHA-256 `6dbcc9497f665a4d738b797d3a9b3e5ea90e57743d48cdcbc285fb7f2dcf12de` | `artifacts/landing-quota-2026-08-10/live-cta-mobile-final.png` |
| S1 | source | `ChatGPT(OpenAI API)` and `Gemini API` use `.dg-no-break` | `site/app/ai-diagnosis/page.tsx:35-40`; `site/app/globals.css:8522` |
| S2 | source | Tokenized diagnosis surface now maps inset panel to contrasting shared coolgrey-85 | `site/app/globals.css:8422-8490` |
| S3 | source | Quota renderer and no-store slot proxy preserve unavailable/live truthfulness | `site/app/_components/DiagnosisQuota.tsx:34-49`; `site/app/api/diagnosis/slots/route.ts:5-20` |
| S4 | source | Diagnosis inputs/inline hint and landing mobile controls use 44px+ sizing tokens | `site/app/globals.css:8477-8518`; `site/app/globals.css:2324-2329, 2350-2377` |

## verdict

**PASS.** All five latest captures match the exact requested dimensions and show no clipping or horizontal overflow. Korean/CJK wrapping remains natural, `Gemini API` is unbroken, quota copy is truthful in both live and unavailable states, visible major mobile controls meet the 44px criterion, and the corrected tokenized `어떻게 재나요` panel is visibly distinct from the page background.

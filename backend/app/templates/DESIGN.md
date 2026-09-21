# Report editorial system

## 1. Atmosphere & Identity
Existing Jinja/WeasyPrint reports become an editorial sequence: conclusion, evidence, decision. Monthly uses navy/teal measurement rules; diagnosis uses ink/copper and query-led proposals. The brief is the approved visual contract.

## 2. Color
Canvas white; warm surface #f7f5f0; ink #202b32; monthly navy #233b52; teal #176c68; diagnosis copper #99522e; muted #56616a; rules #d6d9d8. Status is always written, never color alone.

## 3. Typography
Existing bundled Pretendard only. Document descriptor 12pt, hospital identity 22pt, editorial outcome and page title 28pt, section/proof title 15pt, body 11pt, caption/table 9pt. Tabular numbers. Korean keep-all with emergency wrapping for long fields. The hospital and measured outcome lead page one; document type is subordinate.

## 4. Spacing & Layout
A4, 17mm horizontal and vertical rails. 4/8/12/16/24pt spacing. Three explicitly validated main pages; dense evidence and complete questions flow through appendices. No clipping or fixed-height text regions. Screen preview max 210mm with responsive rails.
Appendices use a 22pt title, 12pt section spacing, and 4pt paragraph/table padding to keep methods together without reducing the 9pt evidence text.

## 5. Components
Shared report_editorial.css: masthead, descriptor, hospital identity, editorial outcome, ruled metric pair with identical 0–100% tracks (6pt high), warm proof cards, numbered priorities (22pt number, 12pt vertical padding), agreement fulfillment, appendix table, caveat and single CTA. Bars exist only for valid values; percentage-point change requires a comparable pair. Cited published work leads the proof page. Methods and all work remain in the appendix; page three connects ranked questions to operation. Empty/unavailable/comparison-blocked states carry their own copy. Main-page end anchors and appendix row anchors are validation contracts, not decoration.

## 6. Motion & Interaction
Static document. Underlined links, visible keyboard focus. No motion. Diagnosis customer CTA uses configured contact; INTERNAL has no customer CTA.

## 7. Depth & Surface
Crisp rules and warm evidence bands. No gradients, shadows, emoji or repeated rounded cards.

## 8. Accessibility Constraints & Accepted Debt
Readable 11pt body and 9pt captions, Korean embedded fonts and Unicode mapping, semantic headings/tables, link checks. PDF export is paginated; HTML preview reflows. No accepted clipping or omitted evidence. Visual QA must inspect generated production-renderer samples.

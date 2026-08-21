# 강심장내과의원 공개 표면 시각 시스템 감사

## Audit scope

Live surface: `https://reputation.motionlabs.kr/gangsimjangnaegwayiweon`.

Fresh Aside captures were taken for home, `/contents`, the actual first `/contents` item (`/contents/9489e9c6-02f7-49bf-bd03-a90e3bfa3a3f`), `/doctor`, `/treatments`, and `/visit` at 1440×900 and 390×844 (12 PNGs). Each run used `snapshot(page)` before `page.screenshot`, then `file`/byte count/SHA-256 and `view_image` inspection. The initial full-page home screenshot was rejected because the capture repeated the hero; the accepted home capture is the stable 1440×900 viewport capture.

## Capture inventory

| surface | desktop | mobile | observed state |
|---|---|---|---|
| home | `home-desktop.png` | `home-mobile.png` | hero, navy monogram/type panel, treatment rail, quick contact |
| contents | `contents-desktop.png` | `contents-mobile.png` | 3 items; FAQ/건강 정보/지역 특화 filters; generic ultrasound illustration |
| first content detail | `content-detail-desktop.png` | `content-detail-mobile.png` | first FAQ article; summary, TOC, related links, reference; mobile table scroller |
| doctor | `doctor-desktop.png` | `doctor-mobile.png` | monogram `장`, credentials, 3 linked articles; no photo asset |
| treatments | `treatments-desktop.png` | `treatments-mobile.png` | 12 bordered treatment rows; no horizontal card scroller |
| visit | `visit-desktop.png` | `visit-mobile.png` | phone/map/hours cards, address and official channels |

The exact dimensions, document heights, H1 heights, image state, CTA samples, overflow checks, byte counts and SHA-256 signatures are in [`metrics.json`](./metrics.json). The corresponding fresh accessibility snapshots are the twelve `*.snapshot.txt` files beside the PNGs.

## Strengths

- Responsive re-composition is solid: all accepted desktop and mobile captures have `scrollWidth == clientWidth`; no visible crop or blank/loading state was found.
- Korean headings remain readable at 390px. The long home H1 resolves into intentional phrase lines, while page headings on the secondary surfaces stay compact.
- The content surface states its small data set honestly (`전체 3`, `FAQ 1`, `건강 정보 1`, `지역 특화 1`) and the first card is immediately identifiable.
- Treatment information is unusually scannable for a 12-item dataset: one consistent bordered-row pattern, concise explanations, and clear chevrons without a hidden horizontal card rail.
- Visit actions are unambiguous and action-oriented: phone, map, and hours are grouped as the first three cards, with the phone CTA promoted in blue.

## Five key findings

1. **Hospital identity is structurally present but visually generic (medium/high).** The home, list and article all reuse one generated ultrasound-room illustration, while the site has no hospital logo, brand-color asset, or real facility/doctor photography. The doctor surface falls back to a navy-on-light monogram `장`. This keeps the system coherent, but it does not build much place-specific trust for a medical practice with zero supplied photos.

2. **The doctor mobile card repeats `대표원장` (medium).** In `doctor-mobile.png`, the label appears beside the monogram and again directly above the doctor name. It reads like duplicated metadata rather than a deliberate hierarchy and adds noise in the highest-trust section.

3. **The article table becomes a 620px horizontal scroller inside a 312px content column (medium).** The snapshot marks the table region as scrollable and DOM metrics are `scrollWidth 620 / clientWidth 312`; the page itself remains at `scrollWidth 390`. This is a valid containment choice, but there is no visible “좌우로 이동” cue in the first mobile viewport, so key comparison content can be missed.

4. **The home surface is information-rich but very long on mobile (medium).** The home document is 10,133px at 390px and repeats treatment/content/contact entry points across sections. The repeated routes are useful, yet the first meaningful visit action is separated from a large amount of duplicated navigation and metadata; a compact sticky or section index would reduce scanning cost.

5. **Visual system consistency is stronger than content variety (low/medium).** All non-home secondary pages are text-first, with no visual treatment imagery and a shared navy/blue/cream language. That preserves calm clinical tone, but 12 treatments and 3 contents rely almost entirely on typography and borders; a small set of type-specific icon/motif variants would improve wayfinding without inventing clinical photography.

## UX implications

- Keep the single-column treatment list; it behaves predictably on both viewports and handles the full 12-item dataset better than a carousel.
- Add a clear mobile table affordance or stacked-card alternative for the article’s comparison table.
- Reduce duplicate doctor metadata and make the monogram card’s role explicit (for example, a named fallback avatar plus specialty badge).
- Consider one compact “빠른 문의”/section index treatment on long home pages so repeated CTAs do not compete with one another.

## Accessibility risks and limits

- Confirmed from snapshots: semantic H1/heading hierarchy, named navigation/regions, link names, mobile navigation, and a table region are exposed. Korean line breaks did not produce visible orphan glyphs or clipped text in the accepted captures.
- Likely risk: the horizontally scrollable article table has no visible scroll instruction in the captured state. Keyboard focus order, actual horizontal keyboard operation, focus ring contrast, screen-reader announcements for the scroller, and target-size measurements were not established from screenshots alone.
- Likely risk: repeated `대표원장` is confusing when read linearly by assistive technology, even though the visual card remains understandable.
- Image semantics need implementation-level verification: home/list/article image elements have empty alt text in the snapshot, while the doctor fallback is a CSS/DOM monogram rather than an image. Whether these are decorative or informative should be made explicit.
- This is not a WCAG conformance claim; no keyboard, zoom, screen-reader, or contrast-meter pass was run.

## System implications

The current fallback system is resilient when the dataset has 3 contents, 12 treatments, 0 photos and no logo: it still produces a complete, navigable clinical hub with a stable navy/blue motif. The next system-level improvement should be a first-class “no-photo/no-logo” identity recipe (monogram, specialty mark, pattern or abstract clinical motif) and a content-type icon set, plus a standard mobile overflow affordance for tables. That would increase differentiation and comprehension without requiring unsupported hospital imagery or copy.

## Overall verdict

**REVISE — visually stable and responsive, with five bounded polish/system opportunities.** No blank, loading, failed-image, horizontal page overflow, or crop failure was found in the accepted 12-capture set.

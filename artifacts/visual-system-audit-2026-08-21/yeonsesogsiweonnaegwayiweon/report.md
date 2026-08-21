# 연세속시원내과의원 Aside 시각 리뷰

- 검토일: 2026-08-21 (Asia/Seoul)
- 대상: `https://reputation.motionlabs.kr/yeonsesogsiweonnaegwayiweon`
- 캡처 표면: home, `/contents`, 목록에서 발견한 첫 공개 콘텐츠 상세, `/doctor`, `/treatments`, `/visit`
캡처 도구: Aside REPL. 모든 표면은 snapshot을 먼저 실행한 뒤 screenshot을 저장했고, 저장 파일은 `file`/크기/sha256 확인 후 `view_image`로 검사했다.

## Overall verdict

공개 허브는 6개 콘텐츠, 6개 진료 영역, 원장 사진과 전화·길찾기 CTA를 안정적으로 노출한다. 1440px과 390px 모두 수평 오버플로·빈 상태·잘린 한국어 헤드라인은 보이지 않았다. 가장 큰 시스템 리스크는 긴 문서 길이와 반복 CTA, 그리고 초기 lazy-image placeholder가 로딩 완료 전 blank로 보이는 점이다. 정식 브랜드 로고는 없고 병원명 텍스트 워드마크와 navy/blue/white 계열만 노출된다.

## Capture inventory

| Surface | Viewport | Screenshot | Size | SHA-256 |
|---|---:|---|---:|---|
| home | 1440x900 | [home-desktop-1440x900.png](./home-desktop-1440x900.png) | 747,097 B | `b1c486b821e9dd648b0df6cbbdb9bdbae26911c2529db52937af485fa3730d2d` |
| home | 390x844 | [home-mobile-390x844.png](./home-mobile-390x844.png) | 188,032 B | `1fd7aa8bc3e466c6f06f1ba9b1395658a39c232b36c525fef6336396b3cdfdfc` |
| contents | 1440x900 | [contents-desktop-1440x900.png](./contents-desktop-1440x900.png) | 389,258 B | `3e826d4e3c07be7f009ba5764e0cd60087e59742175b2d5651163e253c09e9c0` |
| contents | 390x844 | [contents-mobile-390x844.png](./contents-mobile-390x844.png) | 152,343 B | `a73156840b7cf987528d227abbb042ee1b5e4bf4e6076629c1a1549f5d221a7d` |
| first detail | 1440x900 | [detail-desktop-1440x900.png](./detail-desktop-1440x900.png) | 354,587 B | `c61d4146aa815a0ba588cf93ff3ec4955fe45190c8ab397df393b659ba2b1731` |
| first detail | 390x844 | [detail-mobile-390x844.png](./detail-mobile-390x844.png) | 166,370 B | `43831984f17decac2f19587262c2f4c7f6aac4be0a022e91bffebd670f32eba8` |
| doctor | 1440x900 | [doctor-desktop-1440x900.png](./doctor-desktop-1440x900.png) | 317,667 B | `4416a3f6d14b4212643611d78569b9b7b8330b8bc888c6933ab20687d6f4e852` |
| doctor | 390x844 | [doctor-mobile-390x844.png](./doctor-mobile-390x844.png) | 92,279 B | `b554a3685b73706775d4451de83c5ab76698ebcd9577d9f76663f8c4f3ee3306` |
| treatments | 1440x900 | [treatments-desktop-1440x900.png](./treatments-desktop-1440x900.png) | 86,426 B | `a003bbee11b89f30c1e10b3e8a5203073f2f9d7f75062b01b753ed5eb58342be` |
| treatments | 390x844 | [treatments-mobile-390x844.png](./treatments-mobile-390x844.png) | 57,254 B | `3fe348898eb4c90e564cd864624dc23a50b0f97b9954cfb37e08df3348c0a7ca` |
| visit | 1440x900 | [visit-desktop-1440x900.png](./visit-desktop-1440x900.png) | 86,745 B | `fc7aee1a48b2dd4c2a6d2a08b3bb40819f33c00bc1b8669d1e5a1b1161322408` |
| visit | 390x844 | [visit-mobile-390x844.png](./visit-mobile-390x844.png) | 52,107 B | `01666f910a88c0d59cb30eb9e6ed2928b888204be80ead56b796d9706e5bf90b` |

## Screen notes

### Home

Desktop: H1 1, `scrollWidth/clientWidth` 1440/1440, document height 7,486px, images 6/6 loaded after lazy-load scroll. Strength: the hero communicates specialties, director, hours, address and phone immediately. UX risk: long page and repeated medical-information CTAs. Accessibility risk: the two visible director/hero images have empty `alt` values in the DOM. System implication: keep the phone-first funnel but standardize a less repetitive section/CTA hierarchy.

Mobile: H1 1, `390/390`, document height 11,612px, images 6/6 loaded after lazy-load scroll. Strength: persistent quick actions (`전화`, `진료시간`, `진료안내`, `길찾기`) make the contact task easy. UX risk: action rail and repeated contact blocks consume a large proportion of the reading experience. Accessibility risk: no explicit menu control is exposed; image alt coverage is incomplete before lazy loading. System implication: make the mobile action rail a shared component with explicit menu/focus semantics.

### Contents list

Desktop: H1 1, `1440/1440`, 2,357px, one featured image loaded after a 1.5s wait. The six-item count and type distribution are clear. The first capture showed a blank featured-image placeholder, so it was rejected and recaptured after stabilization. The list needs an explicit loading state and a scalable sort/search model.

Mobile: H1 1, `390/390`, 2,697px. Filter chips wrap to two rows without horizontal overflow and six items remain discoverable. The filter block is tall and pushes the featured card down; selected/focus semantics need non-visual verification.

### First public content detail

Discovered from `/contents`: `/contents/416fc295-cdf0-4c68-9e85-3677b6c3d793`, “경산 건강검진 진료비, 검사 목적과 개인 상태에 따라 달라지는 이유”. Desktop is 6,135px and mobile is 10,286px. Strength: source links, evidence metadata, summary answer and six-link table of contents establish trust. UX risk: the first viewport does not expose a prominent sticky TOC, especially on mobile. Accessibility risk: dense metadata and heading hierarchy require screen-reader testing. System implication: all article templates should share a visible jump-navigation pattern.

### Doctor

Desktop: H1 1, `1440/1440`, 2,460px, one director image loaded. Mobile: H1 1, `390/390`, 3,397px. Strength: director, specialties, location and all six related articles are connected. UX risk: biography is a dense paragraph and the mobile profile repeats “대표원장”. Accessibility risk: credential grouping and focus/link naming need testing. System implication: model credentials as labeled fields rather than a single text blob.

### Treatments

Desktop: H1 1, `1440/1440`, 2,493px, no images. Mobile: H1 1, `390/390`, 3,225px. Strength: all six treatment choices use a calm, consistent list and no carousel. UX risk: trailing arrow affordances are faint and only three rows appear in the first mobile viewport. Accessibility risk: row focus and hit-target size should not depend on the small arrow. System implication: use one accessible list-row component with explicit link text and focus styling.

### Visit

Desktop: H1 1, `1440/1440`, 2,263px, three gallery images loaded. Mobile: H1 1, `390/390`, 3,670px, three gallery images loaded after scrolling to the gallery. Strength: phone, directions, hours, parking, transport and preparation guidance are grouped coherently. UX risk: mobile first viewport shows the phone card and only part of directions; hours/map/preparation need considerable scroll. Accessibility risk: verify icon meaning, focus state and map-link names. System implication: keep phone primary while exposing a compact sticky address/hours summary.

## Evidence limits

This was a visual/DOM capture pass. It does not establish complete keyboard, screen-reader, color-contrast, browser-zoom, or external-link behavior. Lazy-loaded images were explicitly rechecked by scrolling their elements into view; no persistent failed image remained after that check.

## Supporting artifact

- [`metrics.json`](./metrics.json) — route, viewport, DOM metrics, CTA observations and separated strength/UX/accessibility/system implications.

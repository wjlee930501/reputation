# 병원 모노그램 아이콘 폰트

`Pretendard-Bold.subset.otf`는 `app/favicon/[slug]/route.tsx`가 병원 탭 아이콘을 그릴 때만 읽는다.
아이콘 렌더러(`next/og`)는 woff2를 읽지 못하므로 웹폰트(`app/fonts`)와 별도로 OTF를 둔다.
한글 음절·호환 자모·ASCII만 남긴 서브셋이며 `lib/clinic-favicon.ts`의 `RENDERABLE_CHAR`와 범위가 같다.

```bash
python3 -m fontTools.subset Pretendard-Bold.otf \
  --unicodes="U+0020-007E,U+3131-318E,U+AC00-D7A3" \
  --layout-features='' --no-hinting \
  --output-file=site/assets/fonts/Pretendard-Bold.subset.otf
```

Pretendard is licensed under SIL Open Font License 1.1.
Copyright (c) 2021 Kil Hyung-jin — https://github.com/orioncactus/pretendard

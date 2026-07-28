#!/usr/bin/env bash
# Competitor landing-page capture for the 2026-07-28 AEO/GEO market review.
# Screenshots stay local (artifacts/**.png is gitignored); only the written
# report is tracked.
set -u
OUT="$(cd "$(dirname "$0")" && pwd)"
VIEWPORT="1440,900"

shot() {
  local name="$1" url="$2"
  if playwright screenshot \
      --viewport-size="$VIEWPORT" \
      --full-page \
      --wait-for-timeout=4000 \
      --timeout=60000 \
      "$url" "$OUT/$name.png" >"$OUT/$name.log" 2>&1; then
    echo "OK    $name"
  else
    echo "FAIL  $name  ($(tail -1 "$OUT/$name.log" | cut -c1-90))"
  fi
}

# 병원 특화
shot kr-mahadoc-hospitalgeo   "https://hospitalgeo.com/"
shot kr-highrank-home         "https://high-rank.co.kr/"
shot kr-highrank-geoseo       "https://high-rank.co.kr/geo-seo"
shot kr-oneplan-medical       "https://oneplan.co.kr/medical-guide-2026"

# 범용 GEO 솔루션
shot kr-gpto                  "https://www.gpto.kr/"
shot kr-georank-geo           "https://georank.co.kr/service/geo"
shot kr-bizspring-geo         "https://bizspring.co.kr/geo_consulting/"
shot kr-leadgenlab            "https://lead-gen.team/"
shot kr-nextt                 "https://www.next-t.co.kr/"

# 해외 레퍼런스
shot global-profound          "https://www.tryprofound.com/"
shot global-peec              "https://peec.ai/"
shot global-otterly           "https://otterly.ai/"
shot global-athenahq          "https://www.athenahq.ai/"
shot global-scrunch           "https://www.scrunchai.com/"

echo "--- done ---"

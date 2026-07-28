#!/usr/bin/env bash
# Second capture batch — vendors surfaced by the Naver probe that Google-based
# search never returned. Hospital-specific competitors dominate this list.
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
    echo "FAIL  $name  ($(grep -oE 'Error: [^│╔]*' "$OUT/$name.log" | head -1 | cut -c1-80))"
  fi
}

shot kr2-geodoctor        "https://geodoctor.net/"
shot kr2-200pro           "https://the200pro.com/"
shot kr2-medisite-aeoseo  "https://xn--2z1bo3hi8foxh97o.kr/aeo-seo"
shot kr2-medigpto         "https://medigpto.com/"
shot kr2-ontheai          "https://onthe.ai/"
shot kr2-compl            "https://compl.co.kr/"
shot kr2-medirevive       "https://medirevive.co/"
shot kr2-brainmedi        "https://www.brainmedi.co.kr/"
shot kr2-airank           "https://airank.lol/"
shot kr2-optigeo          "https://optigeo.kr/"
shot kr2-brank            "https://brank.kr/"
shot kr2-seoaikr          "https://seo.ai.kr/"

echo "--- done ---"

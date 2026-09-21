"""Keep ordinary Korean words intact in WeasyPrint, which lacks keep-all."""

import re
from html.parser import HTMLParser


class _KoreanWords(HTMLParser):
    """Mutable HTML serialization buffer; preserve source escaping and attributes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self.raw_depth = 0
        self.table_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self.table_depth += 1
        if tag in {"style", "script", "title"}:
            self.raw_depth += 1
        self.parts.append(self.get_starttag_text())

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            self.table_depth -= 1
        self.parts.append(f"</{tag}>")
        if tag in {"style", "script", "title"}:
            self.raw_depth -= 1

    def handle_data(self, data: str) -> None:
        # Preserve natural CJK wrapping inside narrow table columns.
        if self.raw_depth or self.table_depth:
            self.parts.append(data)
            return
        self.parts.append(re.sub(r"[^\s]+", self._word, data))

    @staticmethod
    def _word(match: re.Match[str]) -> str:
        word = match.group()
        if len(word) <= 16 and re.search(r"[가-힣]", word):
            return f'<span style="white-space:nowrap">{word}</span>'
        return word

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"<!{decl}>")

    def handle_comment(self, data: str) -> None:
        self.parts.append(f"<!--{data}-->")


def keep_korean_words(html: str) -> str:
    parser = _KoreanWords()
    parser.feed(html)
    parser.close()
    return "".join(parser.parts)

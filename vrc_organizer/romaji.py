"""Minimal kana → romaji transliteration.

Built-in table only — no external dictionary. Hiragana and katakana convert
to Hepburn romaji; kanji and everything else pass through unchanged so the
result is still useful even when the text mixes scripts.

Used to surface a furigana-style readable line under Japanese asset titles in
the inspector. Not a linguistically perfect transliteration — covers the
common case (kana-heavy product names like "ぷにきゅっぷぱーかー").
"""
from __future__ import annotations

# Single-mora syllables, Hepburn. Hiragana and katakana share the keys so we
# can normalize katakana to hiragana before lookup.
_BASE: dict[str, str] = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so",
    "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
    "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
    "や": "ya", "ゆ": "yu", "よ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro",
    "わ": "wa", "を": "wo", "ん": "n",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
    "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do",
    "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
    "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o",
}

# Combinations (small ya/yu/yo glides).
_DIGRAPHS: dict[str, str] = {
    "きゃ": "kya", "きゅ": "kyu", "きょ": "kyo",
    "しゃ": "sha", "しゅ": "shu", "しょ": "sho",
    "ちゃ": "cha", "ちゅ": "chu", "ちょ": "cho",
    "にゃ": "nya", "にゅ": "nyu", "にょ": "nyo",
    "ひゃ": "hya", "ひゅ": "hyu", "ひょ": "hyo",
    "みゃ": "mya", "みゅ": "myu", "みょ": "myo",
    "りゃ": "rya", "りゅ": "ryu", "りょ": "ryo",
    "ぎゃ": "gya", "ぎゅ": "gyu", "ぎょ": "gyo",
    "じゃ": "ja", "じゅ": "ju", "じょ": "jo",
    "びゃ": "bya", "びゅ": "byu", "びょ": "byo",
    "ぴゃ": "pya", "ぴゅ": "pyu", "ぴょ": "pyo",
}


def _kata_to_hira(ch: str) -> str:
    code = ord(ch)
    if 0x30A1 <= code <= 0x30F6:
        return chr(code - 0x60)
    return ch


def _is_kana_or_kanji(ch: str) -> bool:
    c = ord(ch)
    return (
        0x3040 <= c <= 0x30FF  # hiragana + katakana
        or 0x4E00 <= c <= 0x9FFF  # CJK unified ideographs
        or 0xFF66 <= c <= 0xFF9D  # halfwidth katakana
    )


def has_japanese(text: str) -> bool:
    return any(_is_kana_or_kanji(c) for c in text)


def to_romaji(text: str) -> str:
    """Best-effort transliteration. Kana → Hepburn romaji, kanji passes
    through unchanged, ASCII passes through unchanged."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        # Long-vowel mark — repeat the previous vowel.
        if ch == "ー" and out:
            last = out[-1][-1] if out[-1] else ""
            if last in "aiueo":
                out.append(last)
                i += 1
                continue
        # Small tsu — geminates the next consonant.
        if ch in ("っ", "ッ"):
            if i + 1 < n:
                nxt = _kata_to_hira(text[i + 1])
                # Look up the next mora's romaji, double its leading consonant.
                pair = _kata_to_hira(text[i + 1])
                pair_pair = ""
                if i + 2 < n:
                    pair_pair = _kata_to_hira(text[i + 2])
                two = _DIGRAPHS.get(pair + pair_pair) if pair_pair else None
                if two:
                    out.append(two[0] + two)
                    i += 3
                    continue
                roma = _BASE.get(pair, "")
                if roma:
                    out.append(roma[0] + roma)
                    i += 2
                    continue
            i += 1
            continue
        hira = _kata_to_hira(ch)
        nxt = _kata_to_hira(text[i + 1]) if i + 1 < n else ""
        digraph = _DIGRAPHS.get(hira + nxt) if nxt else None
        if digraph:
            out.append(digraph)
            i += 2
            continue
        if hira in _BASE:
            out.append(_BASE[hira])
            i += 1
            continue
        # Pass-through (ASCII, kanji, punctuation).
        out.append(ch)
        i += 1
    return "".join(out)

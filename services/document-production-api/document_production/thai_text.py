"""Thai text helpers: amount in words (บาทถ้วน) and cluster-safe line breaking.

Spec: ``docs/superpowers/specs/2026-11-12-documents-parity.md`` (D1, D3).
Pure functions, no model calls.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import ROUND_HALF_UP, Decimal

# Above/below vowels and tone marks take no horizontal space and stay with the
# character before them; leading vowels stay with the consonant after them;
# following vowels take space but can never begin a line.
THAI_COMBINING = frozenset(chr(code) for code in (0x0E31, *range(0x0E34, 0x0E3B), *range(0x0E47, 0x0E4F)))
THAI_LEADING = frozenset(chr(code) for code in range(0x0E40, 0x0E45))
THAI_FOLLOWING = frozenset(chr(code) for code in (0x0E30, 0x0E32, 0x0E33, 0x0E45, 0x0E46))

_DIGITS = ("ศูนย์", "หนึ่ง", "สอง", "สาม", "สี่", "ห้า", "หก", "เจ็ด", "แปด", "เก้า")
_UNITS = ("", "สิบ", "ร้อย", "พัน", "หมื่น", "แสน")
_MILLION = 1_000_000


def has_thai(text: str) -> bool:
    return any("฀" <= char <= "๿" for char in text)


def _read_group(number: int, *, after_higher: bool) -> str:
    """Read 1..999,999; ``after_higher`` = a higher group (ล้าน) was read before it."""

    words = []
    digits = str(number)
    for position, char in zip(range(len(digits) - 1, -1, -1), digits, strict=True):
        digit = int(char)
        if digit == 0:
            continue
        if position == 0:
            words.append("เอ็ด" if digit == 1 and (number >= 10 or after_higher) else _DIGITS[digit])
        elif position == 1:
            words.append({1: "สิบ", 2: "ยี่สิบ"}.get(digit, _DIGITS[digit] + "สิบ"))
        else:
            words.append(_DIGITS[digit] + _UNITS[position])
    return "".join(words)


def _read(number: int, *, after_higher: bool = False) -> str:
    if number >= _MILLION:
        high, low = divmod(number, _MILLION)
        return _read(high, after_higher=after_higher) + "ล้าน" + (_read_group(low, after_higher=True) if low else "")
    return _read_group(number, after_higher=after_higher)


def baht_text(amount: Decimal) -> str:
    """Thai reading of a baht amount, like Excel ``BAHTTEXT``: ``16050`` → ``หนึ่งหมื่นหกพันห้าสิบบาทถ้วน``."""

    if amount < 0:
        raise ValueError("baht_text does not read negative amounts")
    satang_total = int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    baht, satang = divmod(satang_total, 100)
    if baht == 0 and satang == 0:
        return "ศูนย์บาทถ้วน"
    text = _read(baht) + "บาท" if baht else ""
    return text + (_read(satang) + "สตางค์" if satang else "ถ้วน")


def clusters(word: str) -> list[str]:
    """Split into pieces a line may break between (Thai clusters; other characters one by one)."""

    pieces: list[str] = []
    pending = ""
    for char in word:
        if (char in THAI_COMBINING or char in THAI_FOLLOWING) and pieces and not pending:
            pieces[-1] += char
        elif char in THAI_LEADING:
            pending += char
        else:
            pieces.append(pending + char)
            pending = ""
    if pending:
        pieces.append(pending)
    return pieces


def wrap_lines(text: str, width: float, measure: Callable[[str], float]) -> list[str]:
    """Greedy line breaking that fits ``width`` and never splits a Thai cluster.

    Breaks at spaces first (the break space is dropped), and inside a Thai run
    between clusters. A Latin word longer than the line is split by character;
    a single cluster wider than the line stays whole on its own line.
    """

    lines: list[str] = []
    line = ""
    for word in (word for word in text.split(" ") if word):
        candidate = f"{line} {word}" if line else word
        if measure(candidate) <= width:
            line = candidate
            continue
        if line and measure(word) <= width:
            # The whole word fits a fresh line: break at the space.
            lines.append(line)
            line = word
            continue
        # A word longer than what is left: fill the line cluster by cluster.
        for index, piece in enumerate(clusters(word)):
            joined = (f"{line} {piece}" if index == 0 else f"{line}{piece}") if line else piece
            if not line or measure(joined) <= width:
                line = joined
            else:
                lines.append(line)
                line = piece
    lines.append(line)
    return lines


__all__ = ["THAI_COMBINING", "THAI_FOLLOWING", "THAI_LEADING", "baht_text", "clusters", "has_thai", "wrap_lines"]

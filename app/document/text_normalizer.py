import re
import unicodedata
from dataclasses import dataclass, field


CJK_RANGE = "\u3400-\u4dbf\u4e00-\u9fff"
CJK_RE = re.compile(f"[{CJK_RANGE}]")


@dataclass
class NormalizationResult:
    text: str
    stats: dict[str, int | bool] = field(default_factory=dict)


class TextNormalizer:
    """Conservative post-OCR cleanup before table extraction and indexing."""

    def __init__(self, settings):
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.cfg("document.text_cleanup.enabled", True))

    def normalize_page_text(self, text: str) -> NormalizationResult:
        if not self.enabled:
            return NormalizationResult(text=text, stats={"enabled": False})

        stats: dict[str, int | bool] = {
            "enabled": True,
            "cjk_space_fixes": 0,
            "standard_number_fixes": 0,
            "punctuation_space_fixes": 0,
            "quote_noise_fixes": 0,
            "wrapped_line_merges": 0,
            "orphan_line_merges": 0,
        }
        cleaned_lines = []
        for line in text.splitlines():
            cleaned_line, line_stats = self._normalize_line(line)
            for key, value in line_stats.items():
                stats[key] = int(stats.get(key, 0)) + value
            if cleaned_line.strip():
                cleaned_lines.append(cleaned_line.rstrip())

        if bool(self.settings.cfg("document.text_cleanup.merge_orphan_lines", True)):
            cleaned_lines = self._merge_orphan_lines(cleaned_lines, stats)
        if bool(self.settings.cfg("document.text_cleanup.merge_wrapped_lines", True)):
            cleaned_lines = self._merge_wrapped_lines(cleaned_lines, stats)

        return NormalizationResult(text="\n".join(cleaned_lines).strip(), stats=stats)

    def _normalize_line(self, line: str) -> tuple[str, dict[str, int]]:
        stats = {
            "cjk_space_fixes": 0,
            "standard_number_fixes": 0,
            "punctuation_space_fixes": 0,
            "quote_noise_fixes": 0,
        }
        text = unicodedata.normalize("NFKC", line)
        text = text.replace("\u00a0", " ").replace("\u3000", " ")
        text = re.sub(r"[\u200b-\u200f\ufeff]", "", text)
        text = re.sub(r"[—–−]", "-", text)

        text, count = self._sub_count(rf"(?<=[{CJK_RANGE}]) (?=[{CJK_RANGE}])", "", text)
        stats["cjk_space_fixes"] += count

        text, count = self._sub_count(rf"(?<=[{CJK_RANGE}])\s+([,.;:!?，。；：？！、])", r"\1", text)
        stats["punctuation_space_fixes"] += count
        text, count = self._sub_count(rf"([,.;:!?，。；：？！、])\s+(?=[{CJK_RANGE}])", r"\1", text)
        stats["punctuation_space_fixes"] += count
        text, count = self._sub_count(rf"([“‘])\s+(?=[{CJK_RANGE}])", r"\1", text)
        stats["punctuation_space_fixes"] += count
        text, count = self._sub_count(rf"(?<=[{CJK_RANGE}])\s+([”’])", r"\1", text)
        stats["punctuation_space_fixes"] += count
        text, count = self._sub_count(rf"(?<=[{CJK_RANGE}])\s+([“‘])", r"\1", text)
        stats["punctuation_space_fixes"] += count
        text, count = self._sub_count(rf"([”’])\s+(?=[{CJK_RANGE}])", r"\1", text)
        stats["punctuation_space_fixes"] += count
        if len(text.replace(" ", "")) <= int(self.settings.cfg("document.text_cleanup.quote_noise_line_max_chars", 12)):
            text, count = self._sub_count(rf"(?<=[{CJK_RANGE}])\s*[\"'“”‘’]\s*(?=[{CJK_RANGE}])", "", text)
            stats["quote_noise_fixes"] += count

        text, count = self._sub_count(r"\b([A-Za-z]{1,8})\s*/\s*([A-Za-z])\b", r"\1/\2", text)
        stats["standard_number_fixes"] += count
        text, count = self._sub_count(r"(?<=\d)\s*\.\s*(?=\d)", ".", text)
        stats["standard_number_fixes"] += count
        text, count = self._sub_count(r"(?<=\d)\s*[一－]\s*(?=\d)", "-", text)
        stats["standard_number_fixes"] += count
        text, count = self._sub_count(r"(?<=\d)\s*-\s*(?=\d)", "-", text)
        stats["standard_number_fixes"] += count
        text, count = self._sub_count(r"(?<=\d)\s*/\s*(?=\d)", "/", text)
        stats["standard_number_fixes"] += count

        text = text.strip()
        return text, stats

    def _merge_orphan_lines(self, lines: list[str], stats: dict[str, int | bool]) -> list[str]:
        merged: list[str] = []
        for line in lines:
            if merged and self._is_orphan_cjk_line(line) and self._can_append_to_previous(merged[-1]):
                merged[-1] = f"{merged[-1]}{line}"
                stats["orphan_line_merges"] = int(stats.get("orphan_line_merges", 0)) + 1
                continue
            merged.append(line)
        return merged

    def _is_orphan_cjk_line(self, line: str) -> bool:
        stripped = line.strip()
        if len(stripped) > int(self.settings.cfg("document.text_cleanup.orphan_line_max_chars", 3)):
            return False
        return bool(CJK_RE.search(stripped)) and not re.search(r"[A-Za-z0-9|]", stripped)

    def _can_append_to_previous(self, line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        if stripped.startswith("|") or stripped.endswith("|"):
            return False
        if re.search(r"[。.!?？！:：;；]$", stripped):
            return False
        return bool(CJK_RE.search(stripped[-1]))

    def _merge_wrapped_lines(self, lines: list[str], stats: dict[str, int | bool]) -> list[str]:
        merged: list[str] = []
        for line in lines:
            if merged and self._is_wrapped_continuation(merged[-1], line):
                separator = "" if self._ends_with_cjk(merged[-1]) and self._starts_with_cjk(line) else " "
                merged[-1] = f"{merged[-1]}{separator}{line}"
                stats["wrapped_line_merges"] = int(stats.get("wrapped_line_merges", 0)) + 1
                continue
            merged.append(line)
        return merged

    def _is_wrapped_continuation(self, previous: str, current: str) -> bool:
        prev = previous.strip()
        curr = current.strip()
        if not prev or not curr:
            return False
        if len(prev) > int(self.settings.cfg("document.text_cleanup.short_line_max_chars", 24)):
            return False
        if self._is_structural_line(prev) or self._is_structural_line(curr):
            return False
        if re.search(r"[。.!?？！:：;；]$", prev):
            return False
        if re.match(r"^[,，、;；:：.!?？！)]", curr):
            return True
        return self._ends_with_cjk(prev) and self._starts_with_cjk(curr)

    def _is_structural_line(self, line: str) -> bool:
        stripped = line.strip()
        if stripped.startswith("|") or stripped.endswith("|"):
            return True
        if re.match(r"^\d+(?:\.\d+)*\s+", stripped):
            return True
        if re.match(r"^[a-zA-Z]\)", stripped):
            return True
        if re.match(r"^GB/T\s*\d+", stripped, re.IGNORECASE):
            return True
        return False

    def _starts_with_cjk(self, text: str) -> bool:
        return bool(text and CJK_RE.match(text[0]))

    def _ends_with_cjk(self, text: str) -> bool:
        return bool(text and CJK_RE.match(text[-1]))

    def _sub_count(self, pattern: str, repl: str, text: str) -> tuple[str, int]:
        return re.subn(pattern, repl, text)

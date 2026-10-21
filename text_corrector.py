"""Нормализация и правка markdown-текста (порт TextCorrector)."""

from __future__ import annotations

import os
import re
from pathlib import Path

REPLACEMENTS = {
    "simple": {
        "»": '"',
        "«": '"',
        "“": '"',
        "”": '"',
        "–": "—",
        " - ": " — ",
        " — — ": " — ",
        " -": " — ",
        "- ": " — ",
        ". .": "..",
        "..": "...",
        " .": ".",
        " ,": ",",
        " !": "!",
        " ?": "?",
        " …": "…",
        "* ": "*",
        "_ ": "_",
        ' ".\n': '".\n',
        '. "\n': '."\n',
        ' "!\n': '"!\n',
        '! "\n': '!"\n',
        ' "?\n': '"?\n',
        '? "\n': '?"\n',
        " *": "*",
        ", #": " #",
        ".…": "…",
    },
    "regex": {
        r"\.{2,}": "…",
        r"\*{4,}": "***",
        r"…{2,}": "…",
        r" {2,}": " ",
        r"…(?!\s)": "… ",
        r"^\. ": "",
        r"(?<!\n)\n(?!\n|#|\*)": "\n\n",
    },
}


def fix_line_start_spaces(content: str) -> str:
    new_lines = []
    for line in content.splitlines():
        stripped = line.lstrip()
        if line.startswith(("#", "%")) or stripped.startswith("*"):
            new_lines.append(line)
        else:
            if stripped:
                line = " " + stripped
            else:
                line = stripped
            new_lines.append(line)
    return "\n".join(new_lines)


def normalize_text(content: str, file_path: str | Path | None = None) -> str:
    """Применить все правила корректировки к content."""
    content = content or ""
    file_path = str(file_path) if file_path else "document.md"
    filename, _ = os.path.splitext(os.path.basename(file_path))
    base_name = re.sub(r" \[.*?\]", "", filename).strip().replace(".", "_")

    # Заголовок
    if content.startswith("#"):
        match = re.match(
            r"^(.*?)(?:\s*\[(.*?)\]\s*)?(\.[a-zA-Z]{2})?$", filename
        )
        title = ""
        author = ""
        lang = None
        if match:
            title = match.group(1).strip()
            if match.group(2):
                author = match.group(2).strip()
            if match.group(3):
                lang = match.group(3).strip()
        if not lang:
            content = f"% {title}\n% Автор: {author}\n\n\n{content}"
        else:
            content = (
                f"% {title}{lang.replace('.', '_')}\n"
                f"% Автор: {author}\n\n\n{content}"
            )
    elif content.startswith("\n%"):
        content = f"% {base_name}{content}"

    for old, new in REPLACEMENTS["simple"].items():
        content = content.replace(old, new)

    for pattern, repl in REPLACEMENTS["regex"].items():
        content = re.sub(pattern, repl, content, flags=re.MULTILINE)

    content = re.sub(r" \n", "\n", content)
    content = re.sub(r"\n #", "\n#", content)
    content = re.sub(r"\n %", "\n%", content)
    content = re.sub(r"\n\n%", "\n%", content)

    content = fix_line_start_spaces(content)
    return content.strip() + "\n"

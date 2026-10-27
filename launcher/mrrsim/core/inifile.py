"""Setting keys in an .ini file.

Used for .wslconfig and for Unreal's GameUserSettings.ini: other sections,
comments, only the named keys change."""

from __future__ import annotations

# === SETTING KEYS =============================================================


def _section_name(line: str) -> str | None:
    """The lower-cased name in a `[section]` header line, ignoring anything
    after the `]` (a comment). None if the line is not a header."""
    text = line.strip()
    if not text.startswith("[") or "]" not in text:
        return None
    return text[1 : text.index("]")].lower()


def set_keys(text: str, section: str, values: dict[str, object]) -> str:
    """Return `text` with `[section]` holding `values`. A key already there (any
    capitalisation) is replaced in place, missing ones are added at the end of
    the section, and the section is created if it does not exist."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if _section_name(line) == section.lower()),
        None,
    )
    if start is None:
        lines += ([""] if lines and lines[-1].strip() else []) + [f"[{section}]"]
        start = len(lines) - 1
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("[")),
        len(lines),
    )
    pending = {str(k): v for k, v in values.items()}
    for i in range(start + 1, end):
        raw = lines[i]
        key = (
            raw.split("=", 1)[0].strip().lower()
            if "=" in raw and not raw.lstrip().startswith(("#", ";"))
            else None
        )
        match = next((k for k in pending if k.lower() == key), None)
        if match:
            lines[i] = f"{match}={pending.pop(match)}"
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1  # keep the blank line that separates this section from the next
    lines[insert_at:insert_at] = [f"{k}={v}" for k, v in pending.items()]
    return newline.join(lines) + newline

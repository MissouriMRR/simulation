"""Setting keys in an .ini file while leaving everything else as it was.

Used for .wslconfig and for Unreal's GameUserSettings.ini: other sections, comments, key order and the
file's line endings (Unreal writes Windows ones) are kept; only the named keys change.
"""
from __future__ import annotations


def set_keys(text: str, section: str, values: dict[str, object]) -> str:
    """Return `text` with `[section]` holding `values`. A key already there (any capitalisation) is
    replaced in place, missing ones are added at the end of the section, and the section is created if
    it does not exist. A commented-out key is not touched."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    wanted = f"[{section}]".lower()
    start = next((i for i, line in enumerate(lines) if line.strip().lower() == wanted), None)
    if start is None:
        lines += ([""] if lines and lines[-1].strip() else []) + [f"[{section}]"]
        start = len(lines) - 1
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("[")), len(lines))
    pending = {str(k): v for k, v in values.items()}
    for i in range(start + 1, end):
        raw = lines[i]
        key = raw.split("=", 1)[0].strip().lower() if "=" in raw and not raw.lstrip().startswith(("#", ";")) else None
        match = next((k for k in pending if k.lower() == key), None)
        if match:
            lines[i] = f"{match}={pending.pop(match)}"
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1  # keep the blank line that separates this section from the next
    lines[insert_at:insert_at] = [f"{k}={v}" for k, v in pending.items()]
    return newline.join(lines) + newline

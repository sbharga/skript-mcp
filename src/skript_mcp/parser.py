from __future__ import annotations

import re
from typing import TypedDict


class SkriptError(TypedDict):
    path: str
    line: int
    message: str


class CheckResult(TypedDict):
    success: bool
    errors: list[SkriptError]
    raw_output: str


_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_MINECRAFT_FORMAT_RE = re.compile(r"§(?:x(?:§[0-9a-f]){6}|[0-9a-fk-or])", re.IGNORECASE)
_LOG_PREFIX_RE = re.compile(r"^(?:\[[^\]\r\n]+\](?::)?\s*)+")
_LINE_RE = re.compile(r"\bLine\s+(\d+):\s+\(([^)]+\.sk)\)\s*$", re.IGNORECASE)
_SUCCESS_RE = re.compile(r"\bSuccessfully reloaded\s+(.+?\.sk)\b", re.IGNORECASE)
_ERROR_SUMMARY_RE = re.compile(
    r"\bEncountered\s+(\d+)\s+errors?\s+while reloading\s+(.+?\.sk)\b",
    re.IGNORECASE,
)
_ALL_SCRIPTS_SUCCESS_RE = re.compile(
    r"\bSuccessfully reloaded\s+all scripts\b", re.IGNORECASE
)
_ALL_SCRIPTS_ERROR_RE = re.compile(
    r"\bEncountered\s+(\d+)\s+errors?\s+while reloading\s+all scripts\b",
    re.IGNORECASE,
)
_COMMAND_ERROR_RE = re.compile(
    r"(?:can't find (?:the )?script|could not find (?:the )?script|unknown command|currently disabled)",
    re.IGNORECASE,
)


def clean_line(value: str) -> str:
    value = _ANSI_RE.sub("", value)
    value = _MINECRAFT_FORMAT_RE.sub("", value)
    return _LOG_PREFIX_RE.sub("", value).strip()


def _same_script(reported: str, target: str) -> bool:
    reported = reported.replace("\\", "/").casefold()
    target = target.replace("\\", "/").casefold()
    return reported == target or reported.endswith(f"/{target}")


def _parse_errors(text: str, target: str | None) -> list[SkriptError]:
    lines = [clean_line(line) for line in text.splitlines()]
    errors: list[SkriptError] = []

    for index, line in enumerate(lines):
        match = _LINE_RE.search(line)
        if not match or (
            target is not None and not _same_script(match.group(2), target)
        ):
            continue

        message = "Skript reported an error on this line."
        for following in lines[index + 1 :]:
            if (
                _LINE_RE.search(following)
                or _SUCCESS_RE.search(following)
                or _ERROR_SUMMARY_RE.search(following)
                or _ALL_SCRIPTS_SUCCESS_RE.search(following)
                or _ALL_SCRIPTS_ERROR_RE.search(following)
            ):
                break
            if not following or following.casefold().startswith("line:"):
                continue
            message = following
            break
        errors.append(
            {
                "path": match.group(2).replace("\\", "/"),
                "line": int(match.group(1)),
                "message": message,
            }
        )

    return errors


def parse_reload_output(
    rcon_output: str,
    log_output: str,
    target: str,
    *,
    all_scripts: bool = False,
) -> CheckResult:
    """Parse command-scoped RCON output and appended Paper log output."""
    raw_output = (
        f"[rcon]\n{rcon_output.rstrip()}\n\n[log]\n{log_output.rstrip()}".rstrip()
    )
    combined = f"{rcon_output}\n{log_output}"

    errors: list[SkriptError] = []
    seen: set[tuple[str, int, str]] = set()
    for error in _parse_errors(combined, None if all_scripts else target):
        key = (error["path"], error["line"], error["message"])
        if key not in seen:
            seen.add(key)
            errors.append(error)

    cleaned = clean_line(combined)
    if all_scripts:
        success_terminal = _ALL_SCRIPTS_SUCCESS_RE.search(cleaned) is not None
        error_count = sum(
            int(match.group(1)) for match in _ALL_SCRIPTS_ERROR_RE.finditer(cleaned)
        )
    else:
        success_terminal = any(
            _same_script(match.group(1), target)
            for match in _SUCCESS_RE.finditer(cleaned)
        )
        error_count = sum(
            int(match.group(1))
            for match in _ERROR_SUMMARY_RE.finditer(cleaned)
            if _same_script(match.group(2), target)
        )

    if error_count and not errors:
        errors.append(
            {
                "path": target,
                "line": 0,
                "message": "Skript reported parse errors; inspect raw_output for the unparsed diagnostic.",
            }
        )
    elif _COMMAND_ERROR_RE.search(clean_line(rcon_output)) and not errors:
        errors.append(
            {
                "path": target,
                "line": 0,
                "message": "Skript could not reload the requested script.",
            }
        )

    return {
        "success": success_terminal and not errors and error_count == 0,
        "errors": errors,
        "raw_output": raw_output,
    }

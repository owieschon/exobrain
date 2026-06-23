#!/usr/bin/env python3
"""Client-side backend for Claude's memory tool (tool type "memory_20250818").

Claude's memory tool is client-side: the model issues memory tool calls
(view / create / str_replace / insert / delete / rename) against a virtual
``/memories`` directory, and the application executes them over storage it
controls. This module executes those commands over a real directory on disk and
returns the response strings the documented contract specifies.

For exobrain this is the cloud-optional consolidation path: point the memory tool
at a domain's files and a Claude turn can read and reorganize them as its
persistent memory — the same "review past work, update the store" loop the local
``distill.py`` does heuristically, done by the model when an API key is present.

The contract is implemented faithfully, including the path-traversal protection
the docs require (every path must resolve inside the configured root). This file
has no third-party dependencies and makes no network calls; the optional turn
that *invokes* the tool lives in ``consolidate.py``.

Reference: https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool
"""
from pathlib import Path

MEMORY_ROOT = "/memories"  # the virtual root Claude addresses
_MAX_LINES = 999_999


def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "K", "M", "G"):
        if size < 1024 or unit == "G":
            return f"{size:.0f}B" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}G"


def _numbered(text: str, start: int = 1) -> str:
    lines = text.splitlines()
    return "\n".join(f"{i:>6}\t{line}" for i, line in enumerate(lines, start))


class MemoryError(Exception):
    """Carries a contract error string back to the caller verbatim."""


class MemoryBackend:
    """Executes memory tool commands over ``root`` (the real ``/memories`` dir)."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # -- path safety -------------------------------------------------------
    def _real(self, vpath: str) -> Path:
        """Map a virtual /memories path to a real path, rejecting traversal."""
        if vpath != MEMORY_ROOT and not vpath.startswith(MEMORY_ROOT + "/"):
            raise MemoryError(f"Error: Path {vpath} is outside the allowed {MEMORY_ROOT} directory")
        rel = vpath[len(MEMORY_ROOT):].lstrip("/")
        real = (self.root / rel).resolve()
        if real != self.root and self.root not in real.parents:
            raise MemoryError(f"Error: Path {vpath} is outside the allowed {MEMORY_ROOT} directory")
        return real

    # -- commands ----------------------------------------------------------
    def handle(self, tool_input: dict) -> str:
        """Dispatch one memory tool call and return its result string.

        A malformed call (missing or wrong-typed fields) returns an error string
        rather than raising, so the model can self-correct and one bad tool call
        can't abort a whole consolidation run — the degrade-never-crash contract
        the memory tool relies on.
        """
        command = tool_input.get("command")
        handler = getattr(self, f"_cmd_{command}", None)
        if handler is None:
            return f"Error: Unknown memory command {command!r}"
        try:
            return handler(tool_input)
        except MemoryError as exc:
            return str(exc)
        except KeyError as exc:
            return f"Error: missing required parameter {exc} for command {command!r}"
        except (ValueError, TypeError) as exc:
            return f"Error: invalid parameters for command {command!r}: {exc}"

    def _cmd_view(self, t: dict) -> str:
        vpath = t["path"]
        real = self._real(vpath)
        if not real.exists():
            return f"The path {vpath} does not exist. Please provide a valid path."
        if real.is_dir():
            lines = [
                f"Here're the files and directories up to 2 levels deep in {vpath}, "
                "excluding hidden items and node_modules:",
                f"{_human_size(real.stat().st_size)}\t{vpath}",
            ]
            for child in sorted(real.rglob("*")):
                depth = len(child.relative_to(real).parts)
                if depth > 2 or child.name.startswith(".") or "node_modules" in child.parts:
                    continue
                rel = child.relative_to(real).as_posix()
                try:
                    size = _human_size(child.stat().st_size)
                except OSError:
                    continue  # skip a dangling symlink / unreadable entry rather than crash
                lines.append(f"{size}\t{vpath.rstrip('/')}/{rel}")
            return "\n".join(lines)
        text = real.read_text()
        if len(text.splitlines()) > _MAX_LINES:
            return f"File {vpath} exceeds maximum line limit of {_MAX_LINES} lines."
        view_range = t.get("view_range")
        if view_range:
            start, end = view_range
            if start < 1:
                return f"Error: Invalid `view_range` start {start}; line numbers are 1-based."
            file_lines = text.splitlines()
            if end == -1:  # documented "read to end" sentinel
                end = len(file_lines)
            if end < start:
                return f"Error: Invalid `view_range` [{start}, {end}]; end must be >= start (or -1)."
            chosen = file_lines[start - 1:end]
            body = "\n".join(f"{i:>6}\t{line}" for i, line in enumerate(chosen, start))
        else:
            body = _numbered(text)
        return f"Here's the content of {vpath} with line numbers:\n{body}"

    def _cmd_create(self, t: dict) -> str:
        vpath = t["path"]
        real = self._real(vpath)
        if real.exists():
            return f"Error: File {vpath} already exists"
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_text(t.get("file_text", ""))
        return f"File created successfully at: {vpath}"

    def _cmd_str_replace(self, t: dict) -> str:
        vpath = t["path"]
        real = self._real(vpath)
        if not real.exists() or real.is_dir():
            return f"Error: The path {vpath} does not exist. Please provide a valid path."
        old, new = t["old_str"], t["new_str"]
        text = real.read_text()
        count = text.count(old)
        if count == 0:
            return f"No replacement was performed, old_str `{old}` did not appear verbatim in {vpath}."
        if count > 1:
            hits = sorted({i for i, ln in enumerate(text.splitlines(), 1) if old in ln})
            return (
                f"No replacement was performed. {count} occurrences of old_str `{old}` "
                f"(on line(s): {', '.join(map(str, hits))}). Please ensure it is unique"
            )
        real.write_text(text.replace(old, new))
        return "The memory file has been edited.\n" + _numbered(real.read_text())

    def _cmd_insert(self, t: dict) -> str:
        vpath = t["path"]
        real = self._real(vpath)
        if not real.exists() or real.is_dir():
            return f"Error: The path {vpath} does not exist"
        lines = real.read_text().splitlines()
        at = t["insert_line"]
        if at < 0 or at > len(lines):
            return (
                f"Error: Invalid `insert_line` parameter: {at}. "
                f"It should be within the range of lines of the file: [0, {len(lines)}]"
            )
        lines[at:at] = t.get("insert_text", "").splitlines()
        real.write_text("\n".join(lines) + "\n")
        return f"The file {vpath} has been edited."

    def _cmd_delete(self, t: dict) -> str:
        vpath = t["path"]
        real = self._real(vpath)
        if not real.exists():
            return f"Error: The path {vpath} does not exist"
        if real.is_dir():
            import shutil
            shutil.rmtree(real)
        else:
            real.unlink()
        return f"Successfully deleted {vpath}"

    def _cmd_rename(self, t: dict) -> str:
        old_vpath, new_vpath = t["old_path"], t["new_path"]
        old_real, new_real = self._real(old_vpath), self._real(new_vpath)
        if not old_real.exists():
            return f"Error: The path {old_vpath} does not exist"
        if new_real.exists():
            return f"Error: The destination {new_vpath} already exists"
        new_real.parent.mkdir(parents=True, exist_ok=True)
        old_real.rename(new_real)
        return f"Successfully renamed {old_vpath} to {new_vpath}"

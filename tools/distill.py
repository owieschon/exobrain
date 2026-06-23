#!/usr/bin/env python3
"""
Transcript distillation + orphaned-session reconciliation.

Reads Claude Code session transcripts from ~/.claude/projects/,
detects orphaned/unfinished sessions, and distills durable lessons
into exobrain's raw/session-captures/ directories.

Usage:
    python3 distill.py                  # full run: baseline + reconcile + distill
    python3 distill.py --baseline-only  # just mark all existing transcripts
    python3 distill.py --dry-run        # show what would be processed
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Shared infrastructure: BRAIN_DIR / DOMAINS resolution + the Anthropic client.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import BRAIN_DIR, DOMAINS, call_anthropic

# ---------------------------------------------------------------------------
# Paths. BRAIN_DIR / DOMAINS come from common; the Claude Code transcript
# locations are under ~/.claude.
# ---------------------------------------------------------------------------

CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"
SESSIONS_DIR = CLAUDE_DIR / "sessions"
MARKER_FILE = BRAIN_DIR / "distilled-sessions.json"
PENDING_FILE = BRAIN_DIR / "pending-reconciliation.txt"

# Caps and thresholds
MAX_DISTILL_PER_RUN = 5
AGE_GRACE_SECONDS = 600          # 10 minutes
MAX_TRANSCRIPT_LINES = 500       # per-file cap when reading for distillation
MAX_MESSAGE_CHARS = 2000         # per-message cap inside transcript content
ADAPTIVE_CONTENT_THRESHOLD = 150_000  # total chars before subagent truncation kicks in
MAX_SUBAGENT_LINES = 200         # per-subagent line cap


# ---------------------------------------------------------------------------
# Marker file (distilled-sessions.json)
# ---------------------------------------------------------------------------

def load_marker() -> dict:
    """Load the distilled-sessions.json marker file (sessionId-keyed)."""
    if MARKER_FILE.exists():
        with open(MARKER_FILE) as f:
            return json.load(f)
    return {}


def save_marker(marker: dict):
    """Save the distilled-sessions.json marker file. Creates parent dir if needed."""
    MARKER_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(MARKER_FILE, "w") as f:
        json.dump(marker, f, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# Transcript discovery
# ---------------------------------------------------------------------------

def find_transcripts() -> list[Path]:
    """Find all JSONL transcript files in ~/.claude/projects/.
    Skips subagent files (those are processed alongside their parent)."""
    transcripts = []
    if not PROJECTS_DIR.exists():
        return transcripts
    for p in PROJECTS_DIR.rglob("*.jsonl"):
        if "subagents" in p.parts:
            continue
        transcripts.append(p)
    return sorted(transcripts)


def transcript_id(path: Path) -> str:
    """Stable ID for a transcript: its path relative to the projects dir."""
    try:
        return str(path.relative_to(PROJECTS_DIR))
    except ValueError:
        return str(path)


def session_id(path: Path) -> str:
    """Stable session ID: the transcript filename stem.
    Survives directory moves, unlike the full relative path."""
    return path.stem


def get_project_name(transcript_path: Path) -> str:
    """Readable project label from the transcript path."""
    try:
        rel = transcript_path.relative_to(PROJECTS_DIR)
        parts = list(rel.parts)
        if parts:
            return parts[0][:16]
    except ValueError:
        pass
    return transcript_path.stem


def find_subagent_transcripts(transcript_path: Path) -> list[Path]:
    """Find subagent JSONL files associated with a parent transcript."""
    subagents_dir = transcript_path.parent / "subagents"
    if not subagents_dir.exists():
        return []
    return sorted(subagents_dir.rglob("*.jsonl"))


# ---------------------------------------------------------------------------
# Phase 1: baseline sweep
# ---------------------------------------------------------------------------

def baseline_sweep(marker: dict, dry_run: bool = False) -> int:
    """Mark every existing transcript as baseline-skip.
    Idempotent: skips transcripts already in the marker."""
    transcripts = find_transcripts()
    count = 0
    for t in transcripts:
        sid = session_id(t)
        if sid not in marker:
            if not dry_run:
                marker[sid] = {
                    "sessionId": sid,
                    "path": transcript_id(t),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "reason": "baseline-skip",
                }
            count += 1
    if not dry_run and count > 0:
        save_marker(marker)
    return count


# ---------------------------------------------------------------------------
# Phase 2: orphan detection + reconciliation
# ---------------------------------------------------------------------------

def get_active_session_pids() -> dict[str, int]:
    """Read active session PIDs from ~/.claude/sessions/*.json.
    Returns {sessionId: pid}."""
    active: dict[str, int] = {}
    if not SESSIONS_DIR.exists():
        return active
    for f in SESSIONS_DIR.iterdir():
        if f.suffix != ".json":
            continue
        try:
            data = json.loads(f.read_text())
            sid = data.get("sessionId", f.stem)
            pid = data.get("pid")
            if pid is not None:
                active[sid] = int(pid)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return active


def is_pid_alive(pid: int) -> bool:
    """Check whether a process is still running."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def file_age_seconds(path: Path) -> float:
    """Seconds since last modification of path."""
    try:
        return datetime.now().timestamp() - path.stat().st_mtime
    except OSError:
        return float("inf")


def read_last_lines(path: Path, n: int = 30) -> list[dict]:
    """Read the last N lines of a JSONL file, parse each as JSON.
    Uses tail for efficiency on large files."""
    entries: list[dict] = []
    try:
        result = subprocess.run(
            ["tail", "-n", str(n), str(path)],
            capture_output=True, text=True, timeout=10,
        )
        for raw in result.stdout.strip().split("\n"):
            raw = raw.strip()
            if raw:
                try:
                    entries.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return entries


def _extract_content_blocks(entry: dict) -> Optional[list]:
    """Pull the content list out of a message entry, handling multiple schemas."""
    # Direct role/content
    role = entry.get("role", "")
    if role == "assistant":
        c = entry.get("content")
        if isinstance(c, list):
            return c

    # Wrapped in "message" key
    msg = entry.get("message", {})
    if isinstance(msg, dict) and msg.get("role") == "assistant":
        c = msg.get("content")
        if isinstance(c, list):
            return c

    # type-based
    if entry.get("type") == "assistant":
        c = entry.get("content") or (entry.get("message", {}) or {}).get("content")
        if isinstance(c, list):
            return c

    return None


def detect_unfinished(transcript_path: Path) -> Optional[dict]:
    """Check whether a transcript's last meaningful action is an unmatched tool_use.

    Returns {"tool_name": ..., "tool_id": ...} if unfinished, else None.
    """
    entries = read_last_lines(transcript_path, 30)
    if not entries:
        return None

    # Walk backwards looking for the last tool_use in an assistant message
    last_tool_use: Optional[dict] = None
    seen_tool_result_ids: set[str] = set()

    for entry in reversed(entries):
        # Collect tool_result IDs from any entry
        # tool_result can appear as a top-level type or inside content blocks
        if entry.get("type") == "tool_result":
            tuid = entry.get("tool_use_id", "")
            if tuid:
                seen_tool_result_ids.add(tuid)

        role = entry.get("role", "")
        if role == "user" or (entry.get("message", {}) or {}).get("role") == "user":
            c = entry.get("content") or (entry.get("message", {}) or {}).get("content")
            if isinstance(c, list):
                for block in c:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tuid = block.get("tool_use_id", "")
                        if tuid:
                            seen_tool_result_ids.add(tuid)

        # Find last assistant tool_use
        blocks = _extract_content_blocks(entry)
        if blocks:
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    candidate_id = block.get("id", "")
                    if candidate_id and candidate_id not in seen_tool_result_ids:
                        if last_tool_use is None:
                            last_tool_use = {
                                "tool_name": block.get("name", "unknown"),
                                "tool_id": candidate_id,
                            }

    return last_tool_use


def orphan_detect(marker: dict) -> list[dict]:
    """Find orphaned sessions: transcripts with no live PID, not yet processed,
    and older than the age-grace threshold."""
    active_pids = get_active_session_pids()
    transcripts = find_transcripts()

    # Session IDs with a PID that is still alive
    live_sessions: set[str] = set()
    for sid, pid in active_pids.items():
        if is_pid_alive(pid):
            live_sessions.add(sid)

    orphans: list[dict] = []
    for t in transcripts:
        sid = session_id(t)
        if sid in marker:
            continue
        age = file_age_seconds(t)
        if age < AGE_GRACE_SECONDS:
            continue

        # Check liveness against the session ID (stem)
        if sid in live_sessions:
            continue

        unfinished = detect_unfinished(t)
        orphans.append({
            "path": t,
            "sid": sid,
            "project": get_project_name(t),
            "unfinished": unfinished,
            "age_seconds": age,
        })

    return orphans


def surface_unfinished(orphans: list[dict]) -> list[str]:
    """One-line alerts for unfinished orphaned sessions."""
    alerts: list[str] = []
    for o in orphans:
        if o["unfinished"]:
            tool = o["unfinished"]["tool_name"]
            hours = int(o["age_seconds"] / 3600)
            alerts.append(
                f"[UNFINISHED] {o['project']} -- last action: {tool} (age: {hours}h)"
            )
    return alerts


def write_pending_alerts(alerts: list[str]):
    """Write (or clear) the pending-reconciliation file consumed by the session-start hook.
    Uses write-to-temp-then-rename for atomicity (prevents session-start hook
    from reading a half-written file during a reconciliation run)."""
    if alerts:
        tmp = PENDING_FILE.with_suffix(".tmp")
        tmp.write_text("\n".join(alerts) + "\n")
        os.replace(str(tmp), str(PENDING_FILE))
    elif PENDING_FILE.exists():
        PENDING_FILE.unlink()


# ---------------------------------------------------------------------------
# Phase 3: distillation
# ---------------------------------------------------------------------------

def _select_transcript_lines(path: Path, max_lines: int) -> list[str]:
    """Return raw JSONL lines for distillation.

    If the transcript fits in max_lines, return all of it. Otherwise return the
    first third plus the last two-thirds (head + tail), so both the task setup
    and its resolution survive -- durable lessons usually land near the end of a
    session, which a head-only read would miss.
    """
    def _run(cmd: list[str]) -> str:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ""

    wc = _run(["wc", "-l", str(path)]).split()
    total = int(wc[0]) if wc and wc[0].isdigit() else 0
    if total == 0 or total <= max_lines:
        return _run(["head", "-n", str(max_lines), str(path)]).split("\n")

    head_n = max_lines // 3
    tail_n = max_lines - head_n
    head = _run(["head", "-n", str(head_n), str(path)]).split("\n")
    tail = _run(["tail", "-n", str(tail_n), str(path)]).split("\n")
    return head + tail


def read_transcript_content(path: Path, max_lines: int = MAX_TRANSCRIPT_LINES) -> str:
    """Extract human-readable conversation from a JSONL transcript.
    Returns user and assistant text blocks, skipping tool IO and metadata."""
    parts: list[str] = []
    for raw in _select_transcript_lines(path, max_lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue

        role = (
            entry.get("role")
            or entry.get("type")
            or (entry.get("message", {}) or {}).get("role", "")
        )
        content = (
            entry.get("content")
            or (entry.get("message", {}) or {}).get("content", "")
        )

        if isinstance(content, list):
            text_bits: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_bits.append(block.get("text", ""))
                elif isinstance(block, str):
                    text_bits.append(block)
            content = "\n".join(text_bits)

        if content and role in ("user", "human", "assistant"):
            parts.append(f"[{role}]: {str(content)[:MAX_MESSAGE_CHARS]}")

    return "\n\n".join(parts)


def read_dreaming_config(domain_path: Path) -> Optional[str]:
    """Read a domain's dreaming.md for its watch-triggers."""
    p = domain_path / "dreaming.md"
    return p.read_text() if p.exists() else None


def build_distillation_prompt(
    transcript_content: str,
    dreaming_configs: dict[str, Optional[str]],
) -> str:
    """Build the prompt sent to the distillation model."""
    domain_enum = "|".join(DOMAINS) or "default"
    domain_section = ""
    for domain, config in dreaming_configs.items():
        if config:
            domain_section += f"\n### {domain}\n{config}\n"

    return f"""You are a session distiller for a plain-text knowledge base ("exobrain").

Your job: read a Claude Code session transcript and extract 0-N durable lessons worth capturing.

## The discriminator

Ask: "would this help in a DIFFERENT session or project?"

DURABLE (capture these):
- Principle: a rule or heuristic that transfers across projects
- Reusable pattern: an approach that worked and would work again
- Validated finding: something confirmed by practice, not just theorized
- Decision with rationale: a choice made with reasoning that informs future choices
- Gotcha that will recur: a trap or surprise that other sessions will hit

EPHEMERAL (skip these):
- Project state: what files exist, what is deployed, current ticket status
- What was done today: task completion, commits made, PRs opened
- Anything only useful for continuing THIS specific task
- Debugging steps that only apply to one specific bug
- File paths, variable names, or other implementation details with no transferable lesson

## Domain watch-triggers

Each domain watches for specific types of insights. Route each capture to the most relevant domain.
{domain_section}

## Output format

Return a JSON array. Each element:

```json
{{
  "domain": "{domain_enum}",
  "cluster": "short topic label for grouping",
  "why_it_matters": "one line on why this is worth capturing",
  "lesson": "the durable principle or finding, 2-5 sentences",
  "source_turn": "approximate description of where in the transcript this came from"
}}
```

If the session contains NO durable lessons (just routine work), return an empty array: []

Be selective. Most sessions produce 0-2 captures. A session with 5+ is suspicious.
Do not capture things the wiki probably already knows. Capture what is NEW or SURPRISING.

## Transcript

{transcript_content}"""


def call_anthropic_api(prompt: str) -> Optional[str]:
    """Run the distillation prompt through the shared Anthropic client.
    Returns the response text, or None if no key/network is available."""
    return call_anthropic(
        prompt,
        max_tokens=2000,
        timeout=60,
        error_prefix="  API error",
    )


def parse_captures(response: str) -> list[dict]:
    """Parse the LLM response into a list of capture dicts."""
    text = response.strip()

    # Strip markdown code fences
    if "```json" in text:
        match = re.search(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
        if match:
            text = match.group(1)
    elif "```" in text:
        match = re.search(r"```\s*\n(.*?)\n```", text, re.DOTALL)
        if match:
            text = match.group(1)

    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    return []


def write_capture(capture: dict, session_id: str) -> Optional[Path]:
    """Write a capture draft to the appropriate domain's raw/session-captures/."""
    domain = capture.get("domain", "")
    if domain not in DOMAINS:
        if not DOMAINS:
            return None  # no domains on disk; nothing to write to
        domain = next(iter(DOMAINS))  # fall back to the first discovered domain

    captures_dir = DOMAINS[domain] / "raw" / "session-captures"
    captures_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y-%m-%d")
    slug = re.sub(r"[^a-z0-9]+", "-", capture.get("cluster", "unknown").lower()).strip("-")[:40]
    filename = f"{date_str}-{slug}.md"
    filepath = captures_dir / filename

    # Deduplicate filename
    counter = 1
    while filepath.exists():
        filename = f"{date_str}-{slug}-{counter}.md"
        filepath = captures_dir / filename
        counter += 1

    content = (
        f"# {capture.get('cluster', 'Untitled')}\n"
        f"\n"
        f"**Why it matters:** {capture.get('why_it_matters', 'N/A')}\n"
        f"\n"
        f"**Source session:** {session_id}\n"
        f"**Source turn:** {capture.get('source_turn', 'N/A')}\n"
        f"**Suggested domain:** {domain}\n"
        f"\n"
        f"## Lesson\n"
        f"\n"
        f"{capture.get('lesson', 'No lesson extracted.')}\n"
    )

    filepath.write_text(content)
    return filepath


def distill_session(
    transcript_path: Path,
    marker: dict,
    dry_run: bool = False,
) -> Optional[int]:
    """Distill a single transcript.

    Returns the number of captures written, or None if the API was unavailable
    (so the caller can leave the session unmarked and retry it later).
    """
    sid = session_id(transcript_path)
    project = get_project_name(transcript_path)
    print(f"  Distilling: {project} ({sid})")

    # Read parent transcript
    content = read_transcript_content(transcript_path)

    # Read ALL subagent transcripts with adaptive content management
    subagent_files = find_subagent_transcripts(transcript_path)
    subagent_contents = []
    for sf in subagent_files:
        sub = read_transcript_content(sf, max_lines=MAX_SUBAGENT_LINES)
        if sub:
            subagent_contents.append(sub)

    if subagent_contents:
        total_len = len(content) + sum(len(s) for s in subagent_contents)
        if total_len > ADAPTIVE_CONTENT_THRESHOLD:
            remaining_budget = max(ADAPTIVE_CONTENT_THRESHOLD - len(content), 10_000)
            per_sub_budget = remaining_budget // len(subagent_contents)
            print(f"    Adaptive: {len(subagent_contents)} subagent(s), "
                  f"{total_len:,} chars > {ADAPTIVE_CONTENT_THRESHOLD:,} threshold; "
                  f"truncating each to ~{per_sub_budget:,} chars")
            subagent_contents = [s[:per_sub_budget] for s in subagent_contents]
        for sub in subagent_contents:
            content += f"\n\n--- Subagent transcript ---\n{sub}"

    if not content.strip():
        print("    Empty transcript, skipping")
        if not dry_run:
            marker[sid] = {
                "sessionId": sid,
                "path": transcript_id(transcript_path),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "reason": "distilled",
                "notes": "empty transcript",
            }
            save_marker(marker)
        return 0

    # Load dreaming configs
    dreaming_configs: dict[str, Optional[str]] = {}
    for domain, domain_path in DOMAINS.items():
        dreaming_configs[domain] = read_dreaming_config(domain_path)

    prompt = build_distillation_prompt(content, dreaming_configs)

    if dry_run:
        print(f"    [DRY RUN] Would call API with {len(prompt):,} char prompt")
        print(f"    Subagent files: {len(find_subagent_transcripts(transcript_path))}")
        return 0

    response = call_anthropic_api(prompt)
    if response is None:
        # API unavailable: return None (not 0) so the caller leaves this session
        # unmarked and retries it on a later run, instead of burning it.
        print("    No API key available or API call failed. Will retry later.")
        return None

    captures = parse_captures(response)
    print(f"    Extracted {len(captures)} capture(s)")

    written = 0
    for cap in captures:
        path = write_capture(cap, sid)
        if path:
            print(f"    Wrote: {path}")
            written += 1

    marker[sid] = {
        "sessionId": sid,
        "path": transcript_id(transcript_path),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reason": "distilled",
        "notes": f"{written} captures written",
    }
    save_marker(marker)
    return written


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run(baseline_only: bool = False, dry_run: bool = False):
    """Full pipeline: baseline sweep, orphan reconciliation, distillation."""
    marker = load_marker()

    # -- Phase 1: baseline sweep --
    print("=== Baseline sweep ===")
    new_count = baseline_sweep(marker, dry_run=dry_run)
    verb = "would mark" if dry_run else "marked"
    if new_count > 0:
        print(f"  {verb} {new_count} transcript(s) as baseline-skip")
    else:
        print(f"  All transcripts already tracked ({len(marker)} total)")

    if baseline_only:
        return

    # -- Phase 2: orphan detection --
    print("\n=== Orphan detection ===")
    marker = load_marker()  # reload after baseline writes
    orphans = orphan_detect(marker)

    alerts = surface_unfinished(orphans)
    if alerts:
        print(f"  Found {len(alerts)} unfinished session(s):")
        for a in alerts:
            print(f"    {a}")
        if not dry_run:
            write_pending_alerts(alerts)
    else:
        print("  No unfinished sessions found")
        if not dry_run:
            write_pending_alerts([])

    n_unfinished = sum(1 for o in orphans if o["unfinished"])
    n_finished = len(orphans) - n_unfinished
    print(f"  Total orphans: {len(orphans)} ({n_unfinished} unfinished, {n_finished} finished)")

    # -- Phase 3: distillation --
    print("\n=== Distillation ===")
    to_distill = orphans[:MAX_DISTILL_PER_RUN]
    remainder = len(orphans) - len(to_distill)

    if not to_distill:
        print("  No sessions to distill")
        return

    label = f"Processing {len(to_distill)} session(s)"
    if remainder > 0:
        label += f" ({remainder} in backlog)"
    print(f"  {label}")

    total_captures = 0
    for orphan in to_distill:
        n = distill_session(orphan["path"], marker, dry_run=dry_run)
        if n is None:
            # API was unavailable; leave the session unmarked so it retries.
            continue
        total_captures += n

        # Mark reconciled if distill_session did not already mark it
        if not dry_run and orphan["sid"] not in marker:
            marker[orphan["sid"]] = {
                "sessionId": orphan["sid"],
                "path": transcript_id(orphan["path"]),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "reason": "reconciled",
                "notes": "unfinished" if orphan["unfinished"] else "finished, no captures",
            }
            save_marker(marker)

    print(f"\n  Total captures written: {total_captures}")
    if remainder > 0:
        print(f"  Backlog remaining: {remainder} session(s)")


def main():
    args = set(sys.argv[1:])
    baseline_only = "--baseline-only" in args
    dry_run = "--dry-run" in args

    mode = "baseline-only" if baseline_only else ("dry-run" if dry_run else "full")
    print("distill.py -- transcript distillation + orphan reconciliation")
    print(f"  Brain:          {BRAIN_DIR}")
    print(f"  Claude projects: {PROJECTS_DIR}")
    print(f"  Marker:         {MARKER_FILE}")
    print(f"  Mode:           {mode}")
    print()

    run(baseline_only=baseline_only, dry_run=dry_run)


if __name__ == "__main__":
    main()

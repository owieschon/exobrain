#!/usr/bin/env python3
"""
Verification for the observability layer in common.py — the `exobrain` logger
and the local LLM-call trace (`trace_llm_call`). No API key or network needed:
the logger and trace are pure local behavior.
"""
import json
import logging
import shutil
import sys
import tempfile
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS_DIR))
import common  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{(' -- ' + detail) if detail else ''}")


def main():
    print("Verifying observability (logger + LLM-call trace)")
    print("=" * 60)
    tmp = Path(tempfile.mkdtemp(prefix="obs-verify-"))
    original_trace = common._TRACE_PATH
    try:
        # --- the logger is a single, self-contained "exobrain" logger ---
        check("logger name is 'exobrain'", common.log.name == "exobrain")
        check("logger has exactly one handler", len(common.log.handlers) == 1, str(len(common.log.handlers)))
        check("logger does not propagate to the root logger", common.log.propagate is False)
        check("re-building the logger is idempotent (no stacked handlers)",
              common._build_logger() is common.log and len(common.log.handlers) == 1)

        # --- trace writes one JSONL record with exactly the documented fields ---
        trace = tmp / "trace.jsonl"
        common._TRACE_PATH = str(trace)
        common.trace_llm_call("unit", "claude-haiku-4-5",
                              {"input_tokens": 11, "output_tokens": 7}, 123.4, "ok")
        lines = trace.read_text().splitlines()
        check("trace wrote exactly one line", len(lines) == 1, str(len(lines)))
        rec = json.loads(lines[0])
        check("record has exactly the documented fields",
              set(rec) == {"ts", "step", "model", "input_tokens", "output_tokens", "latency_ms", "outcome"},
              str(sorted(rec)))
        check("record carries the call metadata",
              rec["step"] == "unit" and rec["model"] == "claude-haiku-4-5"
              and rec["input_tokens"] == 11 and rec["output_tokens"] == 7
              and rec["latency_ms"] == 123 and rec["outcome"] == "ok", str(rec))

        # --- a second call appends; missing usage degrades to null tokens ---
        common.trace_llm_call("unit2", "m", None, 1.0, "empty")
        lines = trace.read_text().splitlines()
        check("trace appends rather than overwrites", len(lines) == 2, str(len(lines)))
        check("missing usage records null token counts",
              json.loads(lines[1])["input_tokens"] is None)

        # --- EXOBRAIN_TRACE=off disables writing entirely ---
        common._TRACE_PATH = "off"
        common.trace_llm_call("unit", "m", {}, 1.0, "ok")
        check("EXOBRAIN_TRACE=off writes nothing", len(trace.read_text().splitlines()) == 2)

        # --- tracing never raises, even when the path is unwritable ---
        common._TRACE_PATH = str(tmp / "no-such-dir" / "trace.jsonl")
        try:
            common.trace_llm_call("unit", "m", {}, 1.0, "ok")
            raised = False
        except Exception:
            raised = True
        check("a trace to an unwritable path degrades quietly (no raise)", not raised)

        # --- the JSON log formatter emits one valid JSON object ---
        log_rec = logging.LogRecord("exobrain", logging.WARNING, __file__, 1, "hi %s", ("there",), None)
        out = common._JsonLogFormatter().format(log_rec)
        parsed = json.loads(out)
        check("JSON formatter emits valid JSON with level + message",
              parsed["level"] == "WARNING" and parsed["msg"] == "hi there", out)
    finally:
        common._TRACE_PATH = original_trace
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 60)
    print(f"Results: {PASS} passed, {FAIL} failed")
    if FAIL:
        print("VERIFICATION FAILED")
        sys.exit(1)
    print("ALL TESTS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()

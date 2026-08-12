"""Unit tests for the shell-out engine loop (scripts/gw_agent.py): path
containment, reader-bash guard, and engine-chain de-duplication. No gateway."""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "gw_agent", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "gw_agent.py")
gw = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gw)


def test_safe_path_allows_inside(tmp_path):
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    assert gw._safe_path(tmp_path, "a.txt") == tmp_path / "a.txt"
    assert gw._safe_path(tmp_path, "sub/../a.txt") == tmp_path / "a.txt"


def test_safe_path_rejects_escape(tmp_path):
    for bad in ("../outside.txt", "../../etc/passwd", "/etc/passwd"):
        with pytest.raises(ValueError):
            gw._safe_path(tmp_path, bad)


def test_read_file_cannot_escape_cwd(tmp_path):
    out = gw.run_tool("read_file", {"path": "../secret"}, tmp_path, "reader")
    assert out.startswith("ERROR") and "escapes" in out


def test_write_file_cannot_escape_cwd(tmp_path):
    out = gw.run_tool("write_file", {"path": "../evil", "content": "x"}, tmp_path, "worker")
    assert out.startswith("ERROR")
    assert not (tmp_path.parent / "evil").exists()


def test_reader_bash_blocks_writes_and_network(tmp_path):
    for cmd in ("rm -rf .", "echo hi > f", "curl http://x", "mv a b", "chmod +x f"):
        out = gw.run_tool("bash", {"cmd": cmd}, tmp_path, "reader")
        assert "reader mode blocks" in out, cmd


def test_reader_bash_allows_read_commands(tmp_path):
    (tmp_path / "a.py").write_text("x\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("y\n", encoding="utf-8")
    out = gw.run_tool("bash", {"cmd": "ls *.py 2>/dev/null | wc -l"}, tmp_path, "reader")
    assert out.strip().startswith("2")


def test_worker_bash_is_unguarded(tmp_path):
    out = gw.run_tool("bash", {"cmd": "echo hi > f.txt"}, tmp_path, "worker")
    assert "blocks" not in out
    assert (tmp_path / "f.txt").read_text().strip() == "hi"


def test_engine_chain_dedup_primary_first():
    chain = gw.build_engine_chain("claude-groq-llama3", "")
    assert chain[0] == "claude-groq-llama3"
    assert len(chain) == len(set(chain))
    # a primary already in DEFAULT_FALLBACKS must not appear twice
    assert gw.build_engine_chain("claude-mistral-large", "").count("claude-mistral-large") == 1


def test_engine_chain_custom_fallback():
    assert gw.build_engine_chain("a", "b,c") == ["a", "b", "c"]

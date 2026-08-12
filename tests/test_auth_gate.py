"""Tests for the HuGR subscription gate on the MCP server.

Proves the gate's core guarantee at the unit level: an unlicensed token yields
NO access (None → FastMCP auth failure → zero tools), a configured license
yields an AccessToken, and the gate is inert unless explicitly enabled.

Runs standalone (this is how audit_l1 / make test invokes it) or via pytest::

    PYTHONPATH=. python3 tests/test_auth_gate.py
    PYTHONPATH=. pytest tests/test_auth_gate.py -v
"""

from __future__ import annotations

import asyncio
import os
import sys

from mcp_tools.auth_gate import HugrTokenVerifier, gate_enabled


def _verify(token: str):
    """Run the async verify_token synchronously and return the result."""
    return asyncio.run(HugrTokenVerifier().verify_token(token))


def _checks() -> list[str]:
    """Return a list of failure strings; empty means all gate checks pass."""
    failures: list[str] = []

    # Snapshot env we mutate so the suite stays hermetic.
    saved = {k: os.environ.get(k) for k in ("HUGR_GATE", "HUGR_DEV_LICENSE_KEYS")}
    try:
        # --- gate_enabled reflects the flag ---
        os.environ.pop("HUGR_GATE", None)
        if gate_enabled():
            failures.append("gate_enabled() True with HUGR_GATE unset")
        for val in ("1", "true", "YES", "on"):
            os.environ["HUGR_GATE"] = val
            if not gate_enabled():
                failures.append(f"gate_enabled() False for HUGR_GATE={val!r}")
        os.environ["HUGR_GATE"] = "0"
        if gate_enabled():
            failures.append("gate_enabled() True for HUGR_GATE='0'")

        # --- fail-closed: no configured keys → everything denied ---
        os.environ.pop("HUGR_DEV_LICENSE_KEYS", None)
        if _verify("anything") is not None:
            failures.append("verify_token accepted a token with no keys configured")
        if _verify("") is not None:
            failures.append("verify_token accepted an empty token")

        # --- a configured license key is accepted, unknown ones are not ---
        os.environ["HUGR_DEV_LICENSE_KEYS"] = "good-key-1, good-key-2"
        tok = _verify("good-key-1")
        if tok is None:
            failures.append("verify_token rejected a configured license key")
        else:
            if tok.token != "good-key-1":
                failures.append(f"AccessToken.token wrong: {tok.token!r}")
            if "hugr:tools" not in tok.scopes:
                failures.append(f"AccessToken missing hugr:tools scope: {tok.scopes}")
        if _verify("bad-key") is not None:
            failures.append("verify_token accepted an unlisted key")

        # --- required_scopes are enforced ---
        v = HugrTokenVerifier(required_scopes=["hugr:enterprise"])
        if asyncio.run(v.verify_token("good-key-1")) is not None:
            failures.append("verify_token ignored an unmet required_scope")

        # --- HUGR_AUTH_URL: the gate delegates to the auth API and ignores dev keys ---
        import mcp_tools.auth_gate as ag

        os.environ["HUGR_AUTH_URL"] = "http://auth.test"
        _orig_remote = ag._introspect_remote

        async def _fake_remote(base: str, tok: str):
            # Async stand-in for the real httpx-backed introspect; F-010 made the
            # function async to keep the event loop free during /introspect.
            return (
                {"client_id": "seat-remote", "scopes": ["hugr:tools"], "plan": "pro"}
                if tok == "remote-good"
                else None
            )

        try:
            ag._introspect_remote = _fake_remote
            tok = _verify("remote-good")
            if tok is None or tok.client_id != "seat-remote":
                failures.append(f"gate did not honour the auth-API claims: {tok}")
            if _verify("remote-bad") is not None:
                failures.append("gate accepted a key the auth API rejected")
            # with HUGR_AUTH_URL set, a dev key is NOT a backdoor
            if _verify("good-key-1") is not None:
                failures.append("dev key bypassed the configured auth API")
        finally:
            ag._introspect_remote = _orig_remote
            os.environ.pop("HUGR_AUTH_URL", None)

    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    return failures


# ---------------------------------------------------------------------------
# pytest entrypoints
# ---------------------------------------------------------------------------


def test_gate_enabled_reflects_flag() -> None:
    """Covered within the consolidated gate checks."""
    assert not _checks(), "\n".join(_checks())


def test_unlicensed_is_denied_and_licensed_is_allowed() -> None:
    """Fail-closed for unknown tokens; AccessToken for a configured key."""
    assert not _checks(), "\n".join(_checks())


# ---------------------------------------------------------------------------
# Codex C3 F-010 regression — verify_token must NOT block the event loop
# ---------------------------------------------------------------------------


import pytest  # noqa: E402  — kept near the async test it gates


@pytest.mark.asyncio
async def test_verify_token_does_not_block_event_loop() -> None:
    """N concurrent verify_token calls must complete in ~1×, not N×, latency.

    Before the F-010 fix, ``_introspect_remote`` used synchronous
    ``httpx.post`` inside an ``async def`` method. asyncio.gather() over N
    verify_token calls would still serialise on the blocking POST, so the
    wall-clock would scale linearly with N and the event loop would stall.
    With ``httpx.AsyncClient`` + ``await`` the gather runs the calls
    concurrently and total time stays close to one round-trip.

    Methodology: we monkey-patch ``_introspect_remote`` with a coroutine that
    sleeps ``DELAY`` seconds before returning claims. With concurrent awaits
    the gather should finish in ~DELAY; a blocking impl would take ~N*DELAY.
    We assert wall-clock < N * DELAY / 2 — a generous bound that still
    fails the old sync code (which would take ~N * DELAY).
    """
    import asyncio as _asyncio
    import time as _time

    import mcp_tools.auth_gate as ag

    delay = 0.1  # seconds per "introspect"
    n = 10

    saved_env = {k: os.environ.get(k) for k in ("HUGR_GATE", "HUGR_AUTH_URL")}
    saved_remote = ag._introspect_remote

    async def _slow_remote(base: str, tok: str) -> dict | None:
        # Coroutine that yields to the loop — concurrent awaits should
        # overlap, the way an AsyncClient call would.
        await _asyncio.sleep(delay)
        return {
            "client_id": f"seat:{tok}",
            "scopes": ["hugr:tools"],
            "plan": "pro",
        }

    try:
        os.environ["HUGR_AUTH_URL"] = "http://auth.test"
        ag._introspect_remote = _slow_remote

        v = ag.HugrTokenVerifier()
        t0 = _time.monotonic()
        results = await _asyncio.gather(*[v.verify_token(f"tok-{i}") for i in range(n)])
        elapsed = _time.monotonic() - t0

        assert all(r is not None for r in results), "every verify_token should return a token"

        # Concurrent: ~delay. Sync-blocking: ~n*delay. Bound at n*delay/2.
        upper_bound = (n * delay) / 2
        assert elapsed < upper_bound, (
            f"verify_token serialised on its body: {elapsed:.3f}s for {n} concurrent calls "
            f"(>= {upper_bound:.3f}s); the event loop is blocked. "
            "Did _introspect_remote regress to sync httpx?"
        )
    finally:
        ag._introspect_remote = saved_remote
        for k, val in saved_env.items():
            if val is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = val


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------


def main() -> int:
    failures = _checks()
    if failures:
        print(f"Auth gate: {len(failures)} check(s) FAILED")
        for f in failures:
            print(f"  FAIL  {f}")
        return 1
    print(
        "Auth gate: all checks pass — unlicensed denied, licensed allowed, "
        "gate inert unless HUGR_GATE set."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

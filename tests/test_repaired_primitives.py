"""Regression gate for nine core/venous primitives whose method bodies
referenced undefined names (missing imports / constants / helpers / exception).

`core/` is not in pytest `testpaths`, so these primitives' own tests never ran
in CI and the defects stayed latent — each raised NameError only when the fixed
method was actually invoked. This module lives under the collected `tests/`
tree and EXERCISES the previously-broken path of every repaired primitive, so a
regression re-introducing an undefined name fails here instead of silently.
"""
from __future__ import annotations

import asyncio

import pytest


def test_excel_exporter_to_bytes_uses_io() -> None:
    from core.venous.resiliency.ExcelExporter.ExcelExporter import ExcelExporter, logger

    assert logger is not None  # module-level logger was undefined before the fix

    class _FakeWorkbook:
        def save(self, buf) -> None:
            buf.write(b"xlsx-bytes")

    out = ExcelExporter().to_bytes(_FakeWorkbook())  # exercised io.BytesIO()
    assert out == b"xlsx-bytes"


def test_model_registry_register_and_get_log() -> None:
    from core.venous.resiliency.ModelRegistry.ModelRegistry import ModelRegistry

    reg = ModelRegistry()
    sentinel = object()
    reg.register("m", lambda: sentinel, version="v1")  # logger.info path
    assert reg.get("m", version="v1") is sentinel        # logger.info path


def test_retry_budget_exhaustion_logs() -> None:
    from core.venous.resiliency.RetryBudget.RetryBudget import RetryBudget

    rb = RetryBudget("svc", ratio=0.1, min_requests=10)
    for _ in range(10):
        rb.record_request()
        rb.record_retry()
    assert rb.can_retry() is False  # exercises the logger.warning branch


def test_tracing_buffer_uses_self_lock() -> None:
    from core.venous.resiliency.TracingBuffer.TracingBuffer import TracingBuffer

    tb = TracingBuffer(maxlen=8)
    tb.record({"id": "r1", "total_ms": 3})  # with self._lock
    assert tb.get_all()[0]["id"] == "r1"     # with self._lock


def test_redactor_masks_email() -> None:
    from core.venous.resiliency.Redactor.Redactor import Redactor, _redact_string

    assert _redact_string("ping alice@example.com") == "ping [REDACTED]"
    out = Redactor()(None, "info", {"msg": "card 4111 1111 1111 1111"})
    assert "[REDACTED]" in out["msg"]
    # INV_03: never raises on malformed input
    assert Redactor()(None, "info", {"n": 1, "o": object()})["n"] == 1


def test_feature_flag_cache_set_get() -> None:
    from core.venous.auth.FeatureFlagCache.FeatureFlagCache import (
        CACHE_TTL_SECONDS,
        MAX_CACHE_SIZE,
        FeatureFlagCache,
    )

    assert CACHE_TTL_SECONDS > 0 and MAX_CACHE_SIZE > 0

    async def _exercise() -> None:
        c = FeatureFlagCache()
        await c.set("flag", {"on": True})   # uses MAX_CACHE_SIZE
        assert await c.get("flag") == {"on": True}  # uses CACHE_TTL_SECONDS

    asyncio.run(_exercise())


def test_event_sourced_store_load_from_validation() -> None:
    from core.venous.events.EventSourcedStore.EventSourcedStore import (
        EventSourcedStoreInvariantError,
        InMemoryEventSourcedStore,
    )

    store = InMemoryEventSourcedStore()
    with pytest.raises(EventSourcedStoreInvariantError):
        list(store.load_from("agg-1", "not-an-int"))  # type: ignore[arg-type]
    with pytest.raises(EventSourcedStoreInvariantError):
        list(store.load_from("agg-1", -1))


def test_batch_core_run_sequential_and_parallel() -> None:
    from core.venous.api.BatchCore.BatchCore import (
        BatchCore,
        BatchItemResult,
        IsolationMode,
        ProcessingStrategy,
    )

    async def _handler(item):
        return {"echo": item}

    async def _exercise() -> None:
        bc = BatchCore(_handler, timeout_per_item_s=1.0, max_parallel=4)
        for strategy in (ProcessingStrategy.SEQUENTIAL, ProcessingStrategy.PARALLEL):
            results = await bc.run([1, 2, 3], mode=IsolationMode.BEST_EFFORT, strategy=strategy)
            assert len(results) == 3
            assert all(isinstance(r, BatchItemResult) for r in results)
            assert [r.status_code for r in results] == [201, 201, 201]

    asyncio.run(_exercise())


def test_schema_comparator_diff() -> None:
    from core.venous.extras.SchemaComparator.SchemaComparator import (
        ChangeClass,
        ComparisonResult,
        SchemaComparator,
    )

    schema = {"components": {"schemas": {"User": {"properties": {"id": {"type": "string"}}}}}}
    same = SchemaComparator().compare(schema, schema)
    assert isinstance(same, ComparisonResult)
    assert same.classification is ChangeClass.IDENTICAL  # INV_03: identity is a no-op
    assert same.breaking == []

    without_id = {"components": {"schemas": {"User": {"properties": {}}}}}
    diff = SchemaComparator().compare(schema, without_id)
    assert diff.classification is ChangeClass.BREAKING  # field removed → breaking
    assert any("removed" in v for v in diff.breaking)


def test_protocol_stubs_import_abstractmethod_when_used() -> None:
    """Every generated Protocol stub that decorates a method with
    ``@abstractmethod`` must import it — otherwise the stub NameErrors at
    import time (the decorator runs at class-definition, unlike the
    ``from __future__``-deferred annotations). Guards the wrap_shell header
    assembly against regressing for both current and future-generated stubs.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "core" / "venous"
    offenders = [
        str(p.relative_to(root))
        for p in root.rglob("*.protocol.py")
        if "@abstractmethod" in (text := p.read_text())
        and "from abc import abstractmethod" not in text
    ]
    assert offenders == [], f"protocol stubs use @abstractmethod without importing it: {offenders}"


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-q"]))

"""BEHAVIOR scenarios — gap-fill part 3 (scenarios 37-38).

Split from ``test_behavior_scenarios_gap_fill.py`` to respect the 500-LOC
cap. Shared framework lives in
``test_behavior_scenarios_gap_fill__shared.py``.

Covers:
  * SCENARIO 37 — Push Notifications (Native) + Transactional Email
  * SCENARIO 38 — Rate Limiting
"""

from __future__ import annotations

import ast as _ast
import importlib
import sys
from pathlib import Path

import pytest

from tests.test_behavior_scenarios_gap_fill__shared import (
    Scenario,
    ScenarioContext,
    _assert_scenario,
)


def _import_project_module(project_dir: Path, dotted: str):
    """Import a ``core.venous...`` module from the *generated project* copy.

    The behavior scenarios assert against what each tool ACTUALLY ships into a
    project, so we load the project's own copied primitive/adapter (not the
    Arsenal source) by putting the project dir first on ``sys.path`` and
    flushing any stale ``core``/``app`` modules.
    """
    key = str(project_dir)
    if key not in sys.path:
        sys.path.insert(0, key)
    for m in list(sys.modules):
        if m == "core" or m.startswith("core."):
            del sys.modules[m]
    return importlib.import_module(dotted)

# ===========================================================================
# SCENARIO 37 — Push Notifications (Native) + Transactional Email
# ===========================================================================


async def flow_push_and_email(ctx: ScenarioContext) -> None:
    """Push + email: service classes, lazy SDK imports, model exists, delivery tracker."""
    project_dir = ctx.project_dir

    # --- 1. PushService with send_to_device ---
    push_init = project_dir / "app" / "push" / "__init__.py"
    if push_init.exists():
        src = push_init.read_text()
        ctx.record(
            "push_service_exported",
            "PushService" in src,
            "PushService exported from app/push/__init__.py",
        )
    else:
        ctx.record("push_service_exported", False, "app/push/__init__.py not found")

    push_service_file = project_dir / "app" / "push" / "service.py"
    if push_service_file.exists():
        src = push_service_file.read_text()
        ctx.record(
            "push_service_class_present",
            "class PushService" in src,
            "PushService class in app/push/service.py",
        )
        ctx.record(
            "send_to_device_method",
            "def send_to_device" in src or "async def send_to_device" in src,
            "send_to_device method in PushService",
        )
    else:
        ctx.record("push_service_class_present", False, "app/push/service.py not found")
        ctx.record("send_to_device_method", False, "file not found")

    # --- 2. FCM provider — lazy firebase_admin import ---
    fcm_file = project_dir / "app" / "push" / "providers" / "fcm.py"
    if fcm_file.exists():
        src = fcm_file.read_text()
        ctx.record(
            "fcm_provider_exists",
            True,
            "app/push/providers/fcm.py present",
        )
        ctx.record(
            "firebase_admin_lazy_in_fcm",
            not any(
                (
                    isinstance(n, _ast.Import)
                    and any(a.name.startswith("firebase_admin") for a in n.names)
                )
                or (
                    isinstance(n, _ast.ImportFrom) and (n.module or "").startswith("firebase_admin")
                )
                for n in _ast.parse(src).body
            ),
            "firebase_admin not at module top-level in fcm.py (lazy import)",
        )
    else:
        ctx.record("fcm_provider_exists", False, "app/push/providers/fcm.py not found")
        ctx.record("firebase_admin_lazy_in_fcm", False, "file not found")

    # --- 3. APNs provider — lazy apns2 import ---
    apns_file = project_dir / "app" / "push" / "providers" / "apns.py"
    if apns_file.exists():
        src = apns_file.read_text()
        ctx.record(
            "apns_provider_exists",
            True,
            "app/push/providers/apns.py present",
        )
        ctx.record(
            "apns2_lazy_in_apns",
            not any(
                (isinstance(n, _ast.Import) and any(a.name.startswith("apns2") for a in n.names))
                or (isinstance(n, _ast.ImportFrom) and (n.module or "").startswith("apns2"))
                for n in _ast.parse(src).body
            ),
            "apns2 not at module top-level in apns.py (lazy import)",
        )
    else:
        ctx.record("apns_provider_exists", False, "app/push/providers/apns.py not found")
        ctx.record("apns2_lazy_in_apns", False, "file not found")

    # --- 4. DeviceToken model ---
    device_token_file = project_dir / "app" / "models" / "device_token.py"
    ctx.record(
        "device_token_model_exists",
        device_token_file.exists() and "DeviceToken" in device_token_file.read_text(),
        "DeviceToken model in app/models/device_token.py",
    )

    # --- 5. DeliveryTracker class in transactional email ---
    tracker_file = project_dir / "app" / "email" / "delivery_tracker.py"
    ctx.record(
        "delivery_tracker_exists",
        tracker_file.exists() and "DeliveryTracker" in tracker_file.read_text(),
        "DeliveryTracker class in app/email/delivery_tracker.py",
    )

    # --- 6. All three email provider files exist with lazy imports ---
    provider_names = ["resend_provider.py", "postmark_provider.py", "sendgrid_provider.py"]
    providers_dir = project_dir / "app" / "email" / "providers"
    for pname in provider_names:
        pfile = providers_dir / pname
        provider_key = pname.replace("_provider.py", "")
        ctx.record(
            f"{provider_key}_provider_exists",
            pfile.exists(),
            str(pfile.relative_to(project_dir) if pfile.exists() else "NOT FOUND"),
        )
        if pfile.exists():
            src = pfile.read_text()
            # SDK import for resend/postmarker/sendgrid must be lazy (not at top level)
            sdk_names = {"resend": "resend", "postmark": "postmarker", "sendgrid": "sendgrid"}
            sdk = sdk_names.get(provider_key, provider_key)
            is_lazy = not any(
                (isinstance(n, _ast.Import) and any(a.name.startswith(sdk) for a in n.names))
                or (isinstance(n, _ast.ImportFrom) and (n.module or "").startswith(sdk))
                for n in _ast.parse(src).body
            )
            ctx.record(
                f"{provider_key}_sdk_lazy_import",
                is_lazy,
                f"{sdk} not at module top-level in {pname}",
            )


PUSH_AND_EMAIL = Scenario(
    name="push_notifications_transactional_email",
    archetype="APNs+FCM push notifications + multi-provider transactional email",
    models={"User": {"email": "str", "device_token": "str"}},
    tools=[
        (
            "add_push_notifications_native",
            "adapt.extend.infrastructure.add_push_notifications_native",
        ),
        ("add_transactional_email", "adapt.extend.infrastructure.add_transactional_email"),
    ],
    flow=flow_push_and_email,
    needs_boot=False,  # APNs/FCM require real credentials; file-content checks are definitive
)


# ===========================================================================
# SCENARIO 38 — Rate Limiting
# ===========================================================================


async def flow_rate_limiting(ctx: ScenarioContext) -> None:
    """Rate limiting (CANONICAL venous shape).

    ``add_rate_limiting`` copies the framework-agnostic ``RateLimiter`` token-bucket
    primitive + the FastAPI ``RateLimiterAdapter`` into the project and emits a thin
    ``app/rate_limit.py`` caller (RATE_LIMIT_PER_SECOND / _BURST / _KEY config). This
    flow proves the SHIPPED code actually rate-limits — not that pre-refactor
    slowapi-style files exist.
    """
    project_dir = ctx.project_dir

    # --- 1. Venous shape: primitive + adapter + glue + config -------------------
    primitive = project_dir / "core" / "venous" / "resiliency" / "RateLimiter" / "RateLimiter.py"
    adapter = project_dir / "core" / "venous" / "_adapters" / "fastapi" / "RateLimiterAdapter.py"
    glue = project_dir / "app" / "rate_limit.py"
    ctx.record("rate_limiter_primitive_shipped", primitive.exists(), str(primitive))
    ctx.record("rate_limiter_adapter_shipped", adapter.exists(), str(adapter))
    ctx.record(
        "rate_limit_glue_calls_install",
        glue.exists() and "install_rate_limiting" in glue.read_text(),
        "app/rate_limit.py exposes install_rate_limiting(app)",
    )

    cfg_src = (project_dir / "app" / "core" / "config.py").read_text()
    ctx.record(
        "rate_limit_config_fields",
        all(k in cfg_src for k in ("RATE_LIMIT_PER_SECOND", "RATE_LIMIT_BURST", "RATE_LIMIT_KEY")),
        "RATE_LIMIT_PER_SECOND / _BURST / _KEY in app/core/config.py",
    )

    # --- 2. Primitive REALLY limits: token bucket admits burst, then rejects ----
    rl = _import_project_module(
        project_dir, "core.venous.resiliency.RateLimiter.RateLimiter"
    )
    limiter = rl.InMemoryRateLimiter(rate_per_second=1.0, burst=2)
    admitted = [limiter.try_acquire("ip:1.2.3.4") for _ in range(2)]
    rejected = limiter.try_acquire("ip:1.2.3.4")
    ctx.record(
        "primitive_admits_full_burst",
        admitted == [True, True],
        f"burst=2 admitted: {admitted}",
    )
    ctx.record(
        "primitive_rejects_past_burst",
        rejected is False,
        f"3rd acquire past burst rejected: {rejected!r}",
    )
    ctx.record(
        "primitive_rejection_carries_retry_after",
        limiter.events and limiter.events[-1].retry_after_ms > 0,
        f"retry_after_ms on rejection = {limiter.events[-1].retry_after_ms if limiter.events else None}",
    )
    # Distinct keys never cross-scope (RATE_INV_03).
    ctx.record(
        "primitive_keys_are_isolated",
        limiter.try_acquire("ip:9.9.9.9") is True,
        "a fresh key has its own full bucket",
    )

    # --- 3. Adapter REALLY enforces over HTTP: 200×burst then 429 + Retry-After --
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    adapter_mod = _import_project_module(
        project_dir, "core.venous._adapters.fastapi.RateLimiterAdapter"
    )
    app = FastAPI()

    @app.get("/things")
    def _things() -> dict:
        return {"ok": True}

    adapter_mod.install(app, rate_per_second=1.0, burst=2, key="ip")
    client = TestClient(app)
    codes = [client.get("/things").status_code for _ in range(5)]
    ctx.record(
        "adapter_admits_then_429s",
        codes[:2] == [200, 200] and codes[2] == 429,
        f"status sequence under burst=2: {codes}",
    )
    last = client.get("/things")
    ctx.record(
        "adapter_429_has_retry_after_header",
        last.status_code == 429 and last.headers.get("Retry-After") is not None,
        f"429 Retry-After header = {last.headers.get('Retry-After')!r}",
    )
    # Exempt paths bypass the limiter even when the bucket is empty (404 here =
    # no such route, but it proves the request was NOT shed with a 429).
    healthz = client.get("/healthz")
    ctx.record(
        "adapter_exempts_health_paths",
        healthz.status_code != 429,
        f"GET /healthz after bucket empty: {healthz.status_code} (not 429)",
    )


RATE_LIMITING = Scenario(
    name="rate_limiting",
    archetype="Venous token-bucket RateLimiter + FastAPI adapter (429 + Retry-After)",
    models={"Request": {"path": "str", "method": "str"}},
    tools=[
        ("add_rate_limiting", "adapt.extend.infrastructure.add_rate_limiting"),
    ],
    flow=flow_rate_limiting,
    needs_boot=False,  # behavior proven by exercising the shipped primitive + adapter directly
)


# ===========================================================================
# pytest integration — one parametrized test per scenario
# ===========================================================================

SCENARIOS: list[Scenario] = [
    PUSH_AND_EMAIL,
    RATE_LIMITING,
]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
@pytest.mark.asyncio
async def test_scenario(scenario: Scenario) -> None:
    """Run a behavior scenario end-to-end and assert all checks pass."""
    await _assert_scenario(scenario)

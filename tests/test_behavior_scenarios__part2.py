"""BEHAVIOR scenarios — part 2 (scenarios 7-10).

Scenario definitions and flows for: content moderation, WebSocket chat,
arq worker queue, Stripe Checkout. Imported by test_behavior_scenarios.py.
"""

from __future__ import annotations

import importlib

from tests.test_behavior_scenarios__shared import (
    Scenario,
    ScenarioContext,
    _signup,
    _th,
    _tool_deliverable,
)

# ===========================================================================
# SCENARIO 7 — Content moderation / trust & safety
# ===========================================================================


async def flow_moderation(ctx: ScenarioContext) -> list[tuple[str, bool, str]]:
    """Inbound reports → moderator actions → outbound webhooks → audit."""
    from sqlalchemy import inspect, text

    async with ctx.engine.connect() as conn:
        tables = await conn.run_sync(lambda sc: inspect(sc).get_table_names())

    ctx.record("reports_table", "reports" in tables, "reports table exists")
    # Generator lowercases PascalCase without underscores → "moderatoractions"
    ctx.record(
        "moderator_actions_table",
        "moderatoractions" in tables or "moderator_actions" in tables,
        f"moderator action table present: {[t for t in tables if 'moderator' in t]}",
    )
    ctx.record(
        "webhook_endpoints_table", "webhook_endpoints" in tables, "outbound webhook sender wired"
    )
    wh_ok, wh_detail = _tool_deliverable("app.webhook_receiver", "install_webhook_receiver")
    ctx.record("inbound_webhook_receiver", wh_ok, wh_detail)
    rbac_ok, rbac_detail = _tool_deliverable("app.rbac", "require_roles")
    ctx.record("rbac_for_moderators", rbac_ok, rbac_detail)
    audit_ok, audit_detail = _tool_deliverable("app.audit_log", "install_audit_log")
    ctx.record("audit_for_actions", audit_ok, audit_detail)

    # OpenAPI must expose expected routes
    r = await ctx.client.get("/api/v1/openapi.json")
    paths = r.json().get("paths", {}) if r.status_code == 200 else {}
    has_reports = any("/reports" in p for p in paths)
    has_webhooks = any("/webhooks" in p or "inbound" in p for p in paths)
    ctx.record("reports_routes", has_reports, "reports routes registered")
    ctx.record("webhook_routes", has_webhooks, "webhook routes registered")

    return ctx.report_section


MODERATION = Scenario(
    name="content_moderation",
    archetype="Trust & safety: inbound reports + outbound webhooks + audit",
    models={
        # content_ref / target_ref avoid the generator's `*_id` → FK heuristic
        # which would otherwise create FKs to "contents" / "targets" tables
        # that do not exist in this scenario.
        "Report": {"content_ref": "str", "reason": "str", "status": "str"},
        "ModeratorAction": {"action": "str", "reason": "str", "target_ref": "str"},
    },
    tools=[
        ("add_multi_tenancy", "adapt.extend.auth_access.add_multi_tenancy"),
        ("add_rbac", "adapt.extend.auth_access.add_rbac"),
        ("add_audit_log", "adapt.extend.crud_data.add_audit_log"),
        ("add_webhook_sender", "adapt.extend.realtime.add_webhook_sender"),
        ("add_webhook_receiver", "adapt.extend.realtime.add_webhook_receiver"),
        ("add_circuit_breaker", "adapt.extend.infrastructure.add_circuit_breaker"),
    ],
    flow=flow_moderation,
)


# ===========================================================================
# SCENARIO 8 — Real-time chat (WebSocket)
# ===========================================================================


async def flow_chat(ctx: ScenarioContext) -> list[tuple[str, bool, str]]:
    """Users create rooms, post messages via HTTP, load history, verify WS route."""
    client, session = ctx.client, ctx.session

    alice = await _signup(
        client, "alice@chat.example.com", "AlicePass123!", "Alice", ctx.tenant_slug
    )
    bob = await _signup(client, "bob@chat.example.com", "BobPass123!", "Bob", ctx.tenant_slug)

    # Alice creates a public room
    r = await client.post(
        "/api/v1/chat/rooms",
        json={
            "name": "General",
            "is_private": False,
        },
        headers=_th(alice, ctx.tenant_slug),
    )
    room_created = r.status_code in (200, 201)
    room_id = r.json().get("id") if room_created else None
    ctx.record("room_created", room_created, f"status={r.status_code}")

    # Alice lists rooms (sees her own)
    r = await client.get("/api/v1/chat/rooms", headers=_th(alice, ctx.tenant_slug))
    alice_rooms: list = []
    if r.status_code == 200:
        body = r.json()
        alice_rooms = (
            body if isinstance(body, list) else (body.get("data") or body.get("items") or [])
        )
    ctx.record(
        "alice_lists_own_rooms", len(alice_rooms) >= 1, f"{len(alice_rooms)} rooms visible to Alice"
    )

    # Empty history
    if room_id:
        r = await client.get(
            f"/api/v1/chat/rooms/{room_id}/history", headers=_th(alice, ctx.tenant_slug)
        )
        hist_ok = r.status_code == 200
        ctx.record("empty_history_fetch", hist_ok, f"status={r.status_code}")

    # Verify schema: chat_rooms + chat_messages tables present
    from sqlalchemy import inspect

    async with ctx.engine.connect() as conn:
        tables = await conn.run_sync(lambda sc: inspect(sc).get_table_names())
    ctx.record(
        "chat_schema_present",
        {"chat_rooms", "chat_messages"}.issubset(set(tables)),
        f"tables: {sorted(t for t in tables if 'chat' in t)}",
    )

    # WebSocket route must be registered on the app
    ws_routes = [
        rt
        for rt in ctx.client._transport.app.routes
        if getattr(rt, "path", "").startswith("/ws/chat/")
    ]
    ctx.record(
        "ws_route_registered", len(ws_routes) == 1, f"/ws/chat/{{room_id}} route: {len(ws_routes)}"
    )

    # Config fields were patched into settings
    config_mod = importlib.import_module("app.core.config")
    settings = config_mod.settings
    has_cfg = (
        hasattr(settings, "WEBSOCKET_CHAT_MAX_CONNECTIONS_PER_USER")
        and hasattr(settings, "WEBSOCKET_CHAT_MESSAGE_MAX_LENGTH")
        and hasattr(settings, "WEBSOCKET_CHAT_RATE_LIMIT_PER_MINUTE")
    )
    ctx.record("config_fields_patched", has_cfg, "WEBSOCKET_CHAT_* fields present on settings")

    return ctx.report_section


WEBSOCKET_CHAT = Scenario(
    name="websocket_chat",
    archetype="Real-time chat with JWT-authenticated WebSocket + Redis pub/sub",
    models={
        # Scenario model so generate_project has something to scaffold around;
        # add_websocket_chat creates its own ChatRoom / ChatMessage models.
        "Note": {"title": "str", "body": "text"},
    },
    tools=[
        ("add_multi_tenancy", "adapt.extend.auth_access.add_multi_tenancy"),
        ("add_websocket_chat", "adapt.extend.realtime.add_websocket_chat"),
    ],
    flow=flow_chat,
)


# ===========================================================================
# SCENARIO 9 — Background job queue (arq)
# ===========================================================================


async def flow_arq(ctx: ScenarioContext) -> list[tuple[str, bool, str]]:
    """Verify arq worker infra: schema, settings, worker module, routes."""
    from sqlalchemy import inspect

    # jobs table exists
    async with ctx.engine.connect() as conn:
        tables = await conn.run_sync(lambda sc: inspect(sc).get_table_names())
    ctx.record("jobs_table", "jobs" in tables, f"jobs table: {'jobs' in tables}")

    # Worker module imports with the right WorkerSettings shape
    worker_mod = importlib.import_module("app.workers.arq_worker")
    ws_cls = worker_mod.WorkerSettings
    ctx.record(
        "worker_settings_shape",
        hasattr(ws_cls, "functions")
        and hasattr(ws_cls, "redis_settings")
        and hasattr(ws_cls, "max_jobs"),
        f"functions={len(ws_cls.functions)}, max_jobs={ws_cls.max_jobs}",
    )

    # TASK_REGISTRY has the 3 example tasks
    tasks_mod = importlib.import_module("app.workers.tasks")
    task_names = [getattr(fn, "__name__", "?") for fn in tasks_mod.TASK_REGISTRY]
    has_expected = {"send_email_task", "cleanup_task", "webhook_retry_task"}.issubset(
        set(task_names)
    )
    ctx.record("task_registry_populated", has_expected, f"tasks: {sorted(task_names)}")

    # Config fields patched
    cfg_mod = importlib.import_module("app.core.config")
    s = cfg_mod.settings
    has_cfg = all(
        hasattr(s, f)
        for f in [
            "ARQ_MAX_JOBS",
            "ARQ_JOB_TIMEOUT_SECONDS",
            "ARQ_MAX_TRIES",
            "ARQ_KEEP_RESULTS_SECONDS",
        ]
    )
    ctx.record(
        "arq_settings_patched",
        has_cfg,
        f"ARQ_MAX_JOBS={getattr(s, 'ARQ_MAX_JOBS', None)}",
    )

    # HTTP routes registered
    r = await ctx.client.get("/api/v1/openapi.json")
    paths = r.json().get("paths", {}) if r.status_code == 200 else {}
    has_status = any("/jobs/{job_id}/status" in p for p in paths)
    has_active = any("/jobs/active" in p for p in paths)
    ctx.record(
        "jobs_routes_registered",
        has_status and has_active,
        f"status+active routes: {has_status and has_active}",
    )

    # Enqueue helper is importable (but we don't actually connect to Redis here)
    enqueue_mod = importlib.import_module("app.workers.enqueue")
    ctx.record(
        "enqueue_helper_importable",
        callable(getattr(enqueue_mod, "create_arq_pool", None))
        and callable(getattr(enqueue_mod, "close_arq_pool", None))
        and callable(getattr(enqueue_mod, "enqueue", None)),
        "create_arq_pool + close_arq_pool + enqueue present",
    )

    # Dockerfile.worker was emitted
    dockerfile_worker = ctx.project_dir / "Dockerfile.worker"
    ctx.record(
        "dockerfile_worker_emitted",
        dockerfile_worker.exists(),
        f"Dockerfile.worker: {dockerfile_worker.exists()}",
    )

    return ctx.report_section


ARQ_WORKER = Scenario(
    name="arq_worker_queue",
    archetype="Async background job queue via arq + Redis + FastAPI lifespan",
    models={
        "Note": {"title": "str", "body": "text"},
    },
    tools=[
        ("add_multi_tenancy", "adapt.extend.auth_access.add_multi_tenancy"),
        ("add_arq_worker", "adapt.extend.infrastructure.add_arq_worker"),
    ],
    flow=flow_arq,
)


# ===========================================================================
# SCENARIO 10 — Stripe Checkout payments
# ===========================================================================


async def flow_stripe(ctx: ScenarioContext) -> list[tuple[str, bool, str]]:
    """Verify Stripe Checkout infra: schema, settings, routes, lazy import."""
    from sqlalchemy import inspect

    # payments table exists (created by Base.metadata.create_all)
    async with ctx.engine.connect() as conn:
        tables = await conn.run_sync(lambda sc: inspect(sc).get_table_names())
    ctx.record("payments_table", "payments" in tables, f"payments in DB: {'payments' in tables}")

    # Config fields patched into Settings
    cfg_mod = importlib.import_module("app.core.config")
    s = cfg_mod.settings
    required = [
        "STRIPE_SECRET_KEY",
        "STRIPE_PUBLISHABLE_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "STRIPE_API_VERSION",
        "STRIPE_CHECKOUT_SUCCESS_URL",
        "STRIPE_CHECKOUT_CANCEL_URL",
    ]
    missing = [f for f in required if not hasattr(s, f)]
    ctx.record(
        "stripe_settings_patched",
        not missing,
        f"6/6 fields on settings (api_version={getattr(s, 'STRIPE_API_VERSION', None)})",
    )

    # Lazy stripe import — module loads without `stripe` package installed
    stripe_client_mod = importlib.import_module("app.core.stripe_client")
    has_get_stripe = callable(getattr(stripe_client_mod, "get_stripe", None))
    ctx.record(
        "stripe_client_lazy_importable",
        has_get_stripe,
        "app.core.stripe_client.get_stripe is callable",
    )

    # HTTP routes registered via OpenAPI
    r = await ctx.client.get("/api/v1/openapi.json")
    paths = r.json().get("paths", {}) if r.status_code == 200 else {}
    expected_paths = [
        "/api/v1/payments/checkout",
        "/api/v1/payments/me",
        "/api/v1/payments/{payment_id}",
        "/api/v1/payments/webhook/stripe",
    ]
    missing_paths = [p for p in expected_paths if p not in paths]
    ctx.record(
        "payment_routes_registered",
        not missing_paths,
        f"4/4 routes registered ({len(missing_paths)} missing)",
    )

    # CRUD helpers importable (business logic layer)
    crud_mod = importlib.import_module("app.crud.payment")
    crud_fns = [
        "create_pending_payment",
        "mark_payment_succeeded",
        "mark_payment_failed",
        "get_payment_by_session_id",
        "list_user_payments",
    ]
    missing_crud = [fn for fn in crud_fns if not callable(getattr(crud_mod, fn, None))]
    ctx.record(
        "crud_helpers_present",
        not missing_crud,
        f"{len(crud_fns) - len(missing_crud)}/{len(crud_fns)} CRUD helpers callable",
    )

    # Payment model has tenant_id FK (we applied add_multi_tenancy first)
    payment_mod = importlib.import_module("app.models.payment")
    payment_cls = payment_mod.Payment
    has_tenant_col = "tenant_id" in payment_cls.__table__.columns
    ctx.record(
        "payment_tenant_id_column",
        has_tenant_col,
        "Payment.tenant_id column present (tenant-aware)",
    )

    return ctx.report_section


STRIPE_CHECKOUT = Scenario(
    name="stripe_checkout",
    archetype="Production-grade Stripe Checkout Session with webhook reconciliation",
    models={
        "Subscription": {"plan": "str", "status": "str"},
    },
    tools=[
        ("add_multi_tenancy", "adapt.extend.auth_access.add_multi_tenancy"),
        ("add_stripe_checkout", "adapt.extend.infrastructure.add_stripe_checkout"),
    ],
    flow=flow_stripe,
)

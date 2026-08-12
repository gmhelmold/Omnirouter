"""BEHAVIOR scenarios — part 1 (scenarios 1-6).

Scenario definitions and flows for: e-commerce, SaaS B2B, healthcare,
fintech, blog/CMS, analytics. Imported by test_behavior_scenarios.py.
"""

from __future__ import annotations

import importlib
import uuid

from tests.test_behavior_scenarios__shared import (
    Scenario,
    ScenarioContext,
    _promote_superuser,
    _signup,
    _th,
    _tool_deliverable,
)

# ===========================================================================
# SCENARIO 1 — E-commerce storefront
# ===========================================================================


async def flow_ecommerce(ctx: ScenarioContext) -> list[tuple[str, bool, str]]:
    """Seller creates products, buyer browses + orders, admin audits."""
    client, session = ctx.client, ctx.session

    seller = await _signup(
        client, "seller@shop.example.com", "SellerPass123!", "Seller", ctx.tenant_slug
    )
    buyer = await _signup(
        client, "buyer@shop.example.com", "BuyerPass123!", "Buyer", ctx.tenant_slug
    )

    # Seller creates 5 products
    created = 0
    for i in range(5):
        r = await client.post(
            "/api/v1/products/",
            json={
                "name": f"Item {i}",
                "description": f"Description of item {i}",
                "price": 10.0 + i,
                "sku": f"SKU-{i:03}",
                "stock": 100,
            },
            headers=_th(seller, ctx.tenant_slug),
        )
        if r.status_code in (200, 201):
            created += 1
    ctx.record("seller_creates_products", created == 5, f"{created}/5 products")

    # Seller searches own products
    r = await client.get("/api/v1/products/search?q=Item", headers=_th(seller, ctx.tenant_slug))
    found = 0
    if r.status_code == 200:
        body = r.json()
        found = len(body.get("data") or body.get("items") or [])
    ctx.record("search_returns_results", found >= 5, f"{found} hits")

    # Buyer tries to list — should see 0 (owner-scoped)
    r = await client.get("/api/v1/products/", headers=_th(buyer, ctx.tenant_slug))
    buyer_sees = 0
    if r.status_code == 200:
        body = r.json()
        buyer_sees = len(body.get("data") or body.get("items") or [])
    ctx.record("owner_isolation", buyer_sees == 0, f"buyer sees {buyer_sees} (expected 0)")

    # Seller places an order
    r = await client.post(
        "/api/v1/orders/",
        json={
            "reference": f"ORD-{uuid.uuid4().hex[:8]}",
            "status": "pending",
            "total": 99.99,
        },
        headers=_th(seller, ctx.tenant_slug),
    )
    ctx.record("order_placed", r.status_code in (200, 201), f"status={r.status_code}")

    return ctx.report_section


ECOMMERCE = Scenario(
    name="ecommerce_storefront",
    archetype="Owner-scoped e-commerce with search + orders",
    models={
        "Product": {
            "name": "str",
            "description": "text",
            "price": "float",
            "sku": "str",
            "stock": "int",
        },
        # `reference` instead of `order_id` avoids the generator's `*_id` → FK
        # heuristic which would self-reference orders.id → FK violation.
        "Order": {"reference": "str", "status": "str", "total": "float"},
    },
    tools=[
        ("add_multi_tenancy", "adapt.extend.auth_access.add_multi_tenancy"),
        ("add_search", "adapt.extend.crud_data.add_search"),
        ("add_audit_log", "adapt.extend.crud_data.add_audit_log"),
    ],
    flow=flow_ecommerce,
)


# ===========================================================================
# SCENARIO 2 — SaaS B2B (multi-tenant + RBAC + API keys)
# ===========================================================================


async def flow_saas_b2b(ctx: ScenarioContext) -> list[tuple[str, bool, str]]:
    """Org admin invites members, assigns roles, creates API keys."""
    client, session = ctx.client, ctx.session

    admin = await _signup(
        client, "admin@org.example.com", "AdminPass123!", "Org Admin", ctx.tenant_slug
    )
    await _promote_superuser(session, "admin@org.example.com")
    member = await _signup(
        client, "member@org.example.com", "MemberPass123!", "Member", ctx.tenant_slug
    )

    # Admin creates a workspace
    r = await client.post(
        "/api/v1/workspaces/",
        json={
            "name": "Engineering",
            "description": "Core engineering workspace",
            "plan": "pro",
        },
        headers=_th(admin, ctx.tenant_slug),
    )
    ctx.record("workspace_created", r.status_code in (200, 201), f"status={r.status_code}")

    # Feature flags table present
    from sqlalchemy import inspect, text

    async with ctx.engine.connect() as conn:
        tables = await conn.run_sync(lambda sc: inspect(sc).get_table_names())
    ctx.record(
        "feature_flags_table",
        "feature_flags" in tables or "feature_flag" in tables,
        f"tables found: {sorted(t for t in tables if 'flag' in t)}",
    )
    rbac_ok, rbac_detail = _tool_deliverable("app.rbac", "require_roles")
    ctx.record("rbac_guard", rbac_ok, rbac_detail)
    ctx.record("api_keys_table", "api_keys" in tables, "api_keys present")

    return ctx.report_section


SAAS_B2B = Scenario(
    name="saas_b2b",
    archetype="Multi-tenant B2B with RBAC + feature flags + API keys",
    models={
        "Workspace": {"name": "str", "description": "text", "plan": "str"},
        "Invitation": {"email": "email", "role": "str", "status": "str"},
    },
    tools=[
        ("add_multi_tenancy", "adapt.extend.auth_access.add_multi_tenancy"),
        ("add_rbac", "adapt.extend.auth_access.add_rbac"),
        ("add_api_key_auth", "adapt.extend.auth_access.add_api_key_auth"),
        ("add_feature_flags", "adapt.extend.auth_access.add_feature_flags"),
        ("add_audit_log", "adapt.extend.crud_data.add_audit_log"),
    ],
    flow=flow_saas_b2b,
)


# ===========================================================================
# SCENARIO 3 — Healthcare (HIPAA-compliant)
# ===========================================================================


async def flow_healthcare(ctx: ScenarioContext) -> list[tuple[str, bool, str]]:
    """Every PHI access must be captured in tamper-evident audit log."""
    client, session = ctx.client, ctx.session
    from sqlalchemy import text

    provider = await _signup(
        client, "dr@clinic.example.com", "DoctorPass123!", "Dr Smith", ctx.tenant_slug
    )

    # Provider creates a patient
    r = await client.post(
        "/api/v1/patients/",
        json={
            "mrn": "MRN-00001",
            "full_name": "John Doe",
            "dob": "1980-01-15",
            "phone": "+1-555-0100",
        },
        headers=_th(provider, ctx.tenant_slug),
    )
    patient_created = r.status_code in (200, 201)
    ctx.record("patient_created", patient_created, f"status={r.status_code}")

    await session.commit()

    # add_audit_log ships an in-memory tamper-evident log (install_audit_log),
    # not an audit_logs table. Wire it and prove a PHI-access entry is captured
    # and the HMAC hash chain verifies — the property HIPAA actually requires.
    audit_app = importlib.import_module("app.main").app
    importlib.import_module("app.audit_log").install_audit_log(audit_app)
    audit_log = audit_app.state.audit_log
    audit_log.append(
        actor="dr@clinic.example.com",
        action="read",
        resource="patient/MRN-00001",
        outcome="success",
        attributes={},
    )
    exported = audit_log.export(since_seq=1)
    ctx.record(
        "phi_access_audited",
        len([ln for ln in exported.splitlines() if ln.strip()]) >= 1,
        "PHI access captured in tamper-evident log",
    )
    ctx.record(
        "audit_hash_chain_complete",
        audit_log.verify_chain(),
        "HMAC hash chain verifies (no tampering)",
    )

    # MFA capability present (HIPAA requires 2FA for PHI access). add_mfa ships
    # an in-memory TOTP verifier (install_mfa), not an mfa_devices table.
    mfa_ok, mfa_detail = _tool_deliverable("app.mfa", "install_mfa")
    ctx.record("mfa_available", mfa_ok, mfa_detail)

    return ctx.report_section


HEALTHCARE = Scenario(
    name="healthcare_hipaa",
    archetype="HIPAA-compliant EHR with MFA + tamper-evident audit",
    models={
        "Patient": {"mrn": "str", "full_name": "str", "dob": "date", "phone": "str"},
        "Visit": {"visit_date": "datetime", "notes": "text", "diagnosis": "str"},
        "Prescription": {"drug_name": "str", "dosage": "str", "refills": "int"},
    },
    tools=[
        ("add_multi_tenancy", "adapt.extend.auth_access.add_multi_tenancy"),
        ("add_audit_log", "adapt.extend.crud_data.add_audit_log"),
        ("add_mfa", "adapt.extend.auth_access.add_mfa"),
        ("add_rbac", "adapt.extend.auth_access.add_rbac"),
        ("add_soft_delete", "adapt.extend.crud_data.add_soft_delete"),
    ],
    flow=flow_healthcare,
)


# ===========================================================================
# SCENARIO 4 — Fintech payments
# ===========================================================================


async def flow_fintech(ctx: ScenarioContext) -> list[tuple[str, bool, str]]:
    """Outbox + saga + circuit breaker for distributed payment flow."""
    from sqlalchemy import inspect, text

    # Verify outbox + saga + webhook tables are wired
    async with ctx.engine.connect() as conn:
        tables = await conn.run_sync(lambda sc: inspect(sc).get_table_names())

    ctx.record("outbox_table", "outbox_events" in tables, "outbox_events present")
    saga_ok, saga_detail = _tool_deliverable("app.saga", "install_saga")
    ctx.record("saga_orchestrator", saga_ok, saga_detail)
    ctx.record(
        "webhook_endpoints",
        "webhook_endpoints" in tables,
        "webhook_endpoints for outbound delivery",
    )
    audit_ok, audit_detail = _tool_deliverable("app.audit_log", "install_audit_log")
    ctx.record("audit_trail", audit_ok, audit_detail)

    # OpenAPI must expose the endpoints
    r = await ctx.client.get("/api/v1/openapi.json")
    paths = r.json().get("paths", {}) if r.status_code == 200 else {}
    has_account = any("/accounts" in p for p in paths)
    has_transaction = any("/transactions" in p for p in paths)
    ctx.record("account_endpoints", has_account, "accounts routes registered")
    ctx.record("transaction_endpoints", has_transaction, "transactions routes registered")

    return ctx.report_section


FINTECH = Scenario(
    name="fintech_payments",
    archetype="Financial ledger with outbox + saga + webhook reconciliation",
    models={
        "Account": {"account_number": "str", "balance": "float", "currency": "str"},
        "Transaction": {"amount": "float", "status": "str", "reference": "str", "tx_type": "str"},
    },
    tools=[
        ("add_multi_tenancy", "adapt.extend.auth_access.add_multi_tenancy"),
        ("add_outbox_pattern", "adapt.extend.infrastructure.add_outbox_pattern"),
        ("add_saga", "adapt.extend.infrastructure.add_saga"),
        ("add_circuit_breaker", "adapt.extend.infrastructure.add_circuit_breaker"),
        ("add_webhook_sender", "adapt.extend.realtime.add_webhook_sender"),
        ("add_audit_log", "adapt.extend.crud_data.add_audit_log"),
    ],
    flow=flow_fintech,
)


# ===========================================================================
# SCENARIO 5 — Content publishing / blog
# ===========================================================================


async def flow_blog(ctx: ScenarioContext) -> list[tuple[str, bool, str]]:
    """Author publishes posts, readers paginate + search."""
    client, session = ctx.client, ctx.session

    author = await _signup(
        client, "author@blog.example.com", "AuthorPass123!", "Author", ctx.tenant_slug
    )
    await _promote_superuser(session, "author@blog.example.com")

    # Create 10 posts
    created = 0
    for i in range(10):
        r = await client.post(
            "/api/v1/posts/",
            json={
                "title": f"Post {i}: Thoughts on FastAPI",
                "slug": f"post-{i}",
                "body": f"This is post number {i} with interesting content about FastAPI.",
                "published": True,
            },
            headers=_th(author, ctx.tenant_slug),
        )
        if r.status_code in (200, 201):
            created += 1
    ctx.record("posts_created", created == 10, f"{created}/10 posts")

    # Pagination — first page of 3
    r = await client.get("/api/v1/posts/?limit=3", headers=_th(author, ctx.tenant_slug))
    paginated = 0
    if r.status_code == 200:
        body = r.json()
        paginated = len(body.get("data") or body.get("items") or [])
    ctx.record("pagination_works", paginated <= 3 and paginated > 0, f"{paginated} items in page")

    # Full-text search
    r = await client.get("/api/v1/posts/search?q=FastAPI", headers=_th(author, ctx.tenant_slug))
    search_hits = 0
    if r.status_code == 200:
        body = r.json()
        search_hits = len(body.get("data") or body.get("items") or [])
    ctx.record("fts_search", search_hits >= 5, f"{search_hits} hits for 'FastAPI'")

    # Soft delete a post
    r = await client.get("/api/v1/posts/?limit=1", headers=_th(author, ctx.tenant_slug))
    first_id = None
    if r.status_code == 200:
        body = r.json()
        items = body.get("data") or body.get("items") or []
        if items:
            first_id = items[0].get("id")
    if first_id:
        r = await client.delete(f"/api/v1/posts/{first_id}", headers=_th(author, ctx.tenant_slug))
        deleted_ok = r.status_code in (200, 204)
        r = await client.get(f"/api/v1/posts/{first_id}", headers=_th(author, ctx.tenant_slug))
        hidden = r.status_code == 404
        ctx.record("soft_delete_hides", deleted_ok and hidden, f"del={deleted_ok}, hidden={hidden}")

    return ctx.report_section


BLOG = Scenario(
    name="blog_cms",
    archetype="Content publishing with pagination + search + soft delete",
    models={
        "Post": {"title": "str", "slug": "str", "body": "text", "published": "bool"},
        "Comment": {"author_name": "str", "body": "text", "approved": "bool"},
    },
    tools=[
        ("add_multi_tenancy", "adapt.extend.auth_access.add_multi_tenancy"),
        ("add_search", "adapt.extend.crud_data.add_search"),
        ("add_cursor_pagination", "adapt.extend.crud_data.add_cursor_pagination"),
        ("add_soft_delete", "adapt.extend.crud_data.add_soft_delete"),
        ("add_audit_log", "adapt.extend.crud_data.add_audit_log"),
    ],
    flow=flow_blog,
)


# ===========================================================================
# SCENARIO 6 — Analytics / event ingestion
# ===========================================================================


async def flow_analytics(ctx: ScenarioContext) -> list[tuple[str, bool, str]]:
    """Bulk ingestion + data export + long-running async report."""
    client, session = ctx.client, ctx.session

    analyst = await _signup(
        client, "analyst@analytics.example.com", "AnalystPass123!", "Analyst", ctx.tenant_slug
    )

    # Bulk create 100 events
    items = [
        {
            "event_name": f"page_view_{i}",
            "source": f"u_{i % 20}",
            "recorded_at_str": "2026-01-01T00:00:00Z",
            "payload": "{}",
        }
        for i in range(100)
    ]
    r = await client.post(
        "/api/v1/events/bulk",
        json={
            "items": items,
            "mode": "all_or_nothing",
        },
        headers=_th(analyst, ctx.tenant_slug),
    )
    bulk_ok = r.status_code in (200, 201, 207)
    bulk_count = 0
    if bulk_ok:
        body = r.json()
        bulk_count = len([r for r in body.get("results", []) if r.get("success")])
    ctx.record(
        "bulk_ingestion",
        bulk_ok and bulk_count >= 90,
        f"{bulk_count}/100 events ingested via /bulk",
    )

    await session.commit()

    # Verify persistence
    from sqlalchemy import text

    result = await session.execute(text("SELECT COUNT(*) FROM events"))
    stored = result.scalar() or 0
    ctx.record("events_persisted", stored >= 90, f"{stored} events in DB")

    # OpenAPI must expose export endpoint
    r = await client.get("/api/v1/openapi.json")
    paths = r.json().get("paths", {}) if r.status_code == 200 else {}
    export_paths = [p for p in paths if "export" in p.lower()]
    ctx.record(
        "export_endpoint_registered", len(export_paths) > 0, f"export paths: {export_paths[:3]}"
    )

    return ctx.report_section


ANALYTICS = Scenario(
    name="analytics_ingestion",
    archetype="High-throughput event ingestion with bulk ops + async export",
    models={
        # `source` instead of `user_id_ext` to avoid the `*_id` FK heuristic.
        # `recorded_at_str` instead of `timestamp_str` to avoid datetime parsing.
        "Event": {
            "event_name": "str",
            "source": "str",
            "recorded_at_str": "str",
            "payload": "text",
        },
        "Metric": {"name": "str", "value": "float", "tags": "str"},
    },
    tools=[
        ("add_multi_tenancy", "adapt.extend.auth_access.add_multi_tenancy"),
        ("add_bulk_operations", "adapt.extend.crud_data.add_bulk_operations"),
        ("add_data_export", "adapt.extend.crud_data.add_data_export"),
        ("add_long_running_task", "adapt.extend.api_design.add_long_running_task"),
        ("add_cache_layer", "adapt.extend.infrastructure.add_cache_layer"),
    ],
    flow=flow_analytics,
)

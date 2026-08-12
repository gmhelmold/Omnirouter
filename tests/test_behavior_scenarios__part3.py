"""BEHAVIOR scenarios — part 3 (scenarios 11-12).

Scenario definitions and flows for: email templates, SQLAdmin panel.
Imported by test_behavior_scenarios.py.
"""

from __future__ import annotations

import importlib

from tests.test_behavior_scenarios__shared import Scenario, ScenarioContext

# ===========================================================================
# SCENARIO 11 — Email templates (Jinja2 + pluggable provider)
# ===========================================================================


async def flow_email(ctx: ScenarioContext) -> list[tuple[str, bool, str]]:
    """Verify email infra: schema, config, templates, rendering, provider."""
    from sqlalchemy import inspect

    # email_deliveries table exists
    async with ctx.engine.connect() as conn:
        tables = await conn.run_sync(lambda sc: inspect(sc).get_table_names())
    ctx.record(
        "email_deliveries_table",
        "email_deliveries" in tables,
        f"email_deliveries in DB: {'email_deliveries' in tables}",
    )

    # 8 email settings fields reachable
    cfg_mod = importlib.import_module("app.core.config")
    s = cfg_mod.settings
    required = [
        "EMAIL_PROVIDER",
        "EMAIL_FROM",
        "EMAIL_FROM_NAME",
        "EMAIL_REPLY_TO",
        "EMAIL_DEFAULT_LOCALE",
        "RESEND_API_KEY",
        "POSTMARK_API_KEY",
        "EMAIL_PREVIEW_ENABLED_IN_PROD",
    ]
    missing = [f for f in required if not hasattr(s, f)]
    ctx.record(
        "email_settings_patched",
        not missing,
        f"8/8 fields on settings (provider={getattr(s, 'EMAIL_PROVIDER', None)})",
    )

    # All 4 templates × 3 variants = 12 files present
    tpl_dir = ctx.project_dir / "app/email/templates/en"
    expected = {
        "welcome",
        "password_reset",
        "email_verification",
        "receipt",
    }
    missing_tpl: list[str] = []
    for name in expected:
        for ext in ("subject.txt", "html", "txt"):
            p = tpl_dir / f"{name}.{ext}"
            if not p.exists():
                missing_tpl.append(f"{name}.{ext}")
    ctx.record(
        "templates_present",
        not missing_tpl,
        f"12/12 template files ({len(missing_tpl)} missing)",
    )

    # Rendering works for all 4 templates with realistic context
    render_mod = importlib.import_module("app.email.render")
    registry_mod = importlib.import_module("app.email.registry")
    contexts = {
        registry_mod.TemplateName.WELCOME: {
            "app_name": "Acme",
            "user_name": "Alice",
            "activation_url": "https://acme.test/a?t=abc",
        },
        registry_mod.TemplateName.PASSWORD_RESET: {
            "app_name": "Acme",
            "user_name": "Bob",
            "reset_url": "https://acme.test/r?t=xyz",
            "expires_in_hours": 24,
        },
        registry_mod.TemplateName.EMAIL_VERIFICATION: {
            "app_name": "Acme",
            "user_name": "Carol",
            "verification_url": "https://acme.test/v?t=abc",
        },
        registry_mod.TemplateName.RECEIPT: {
            "app_name": "Acme",
            "user_name": "Dave",
            "amount_formatted": "$29.99",
            "item_name": "Pro Plan",
            "receipt_url": "https://acme.test/r/1",
        },
    }
    render_failures: list[str] = []
    for tpl, ctx_data in contexts.items():
        try:
            r = render_mod.render_email(tpl, ctx_data, locale="en")
            assert r.subject and r.html and r.text
        except Exception as exc:
            render_failures.append(f"{tpl.value}: {type(exc).__name__}")
    ctx.record(
        "all_templates_render",
        not render_failures,
        f"4/4 templates rendered ({render_failures})"
        if render_failures
        else "4/4 templates render",
    )

    # Missing context raises proper error
    raised_correctly = False
    try:
        render_mod.render_email(
            registry_mod.TemplateName.WELCOME,
            {"user_name": "NoApp"},  # missing app_name + activation_url
            locale="en",
        )
    except Exception as exc:
        raised_correctly = "MissingContextError" in type(exc).__name__ or "Missing" in str(exc)
    ctx.record(
        "missing_context_raises", raised_correctly, "render_email rejects incomplete context"
    )

    # Locale fallback to 'en' works
    fallback_ok = False
    try:
        r = render_mod.render_email(
            registry_mod.TemplateName.WELCOME,
            {"app_name": "X", "user_name": "Y", "activation_url": "https://z"},
            locale="pt-BR",  # not present — should fallback to 'en'
        )
        fallback_ok = r.subject.startswith("Welcome")
    except Exception:
        pass
    ctx.record("locale_fallback_works", fallback_ok, "pt-BR → en fallback chain")

    # Preview + deliveries routes registered
    r = await ctx.client.get("/api/v1/openapi.json")
    paths = r.json().get("paths", {}) if r.status_code == 200 else {}
    has_preview = any("/email/preview" in p for p in paths)
    has_deliveries = any("/email/deliveries" in p for p in paths)
    ctx.record(
        "email_routes_registered",
        has_preview and has_deliveries,
        f"preview={has_preview}, deliveries={has_deliveries}",
    )

    # Provider selector exists (lazy — does not actually import resend)
    providers_mod = importlib.import_module("app.email.providers")
    has_selector = callable(getattr(providers_mod, "get_provider", None))
    ctx.record(
        "provider_selector_present", has_selector, "app.email.providers.get_provider is callable"
    )

    return ctx.report_section


EMAIL_TEMPLATES = Scenario(
    name="email_templates",
    archetype="Transactional email with Jinja2 templates + pluggable provider layer",
    models={
        "Notification": {"kind": "str", "title": "str"},
    },
    tools=[
        ("add_multi_tenancy", "adapt.extend.auth_access.add_multi_tenancy"),
        ("add_email_templates", "adapt.extend.infrastructure.add_email_templates"),
    ],
    flow=flow_email,
)


# ===========================================================================
# SCENARIO 12 — Admin panel (SQLAdmin)
# ===========================================================================


async def flow_sqladmin(ctx: ScenarioContext) -> list[tuple[str, bool, str]]:
    """Verify admin panel infra: setup module, auth, views, config."""

    # Config fields patched into Settings
    cfg_mod = importlib.import_module("app.core.config")
    s = cfg_mod.settings
    has_cfg = all(
        hasattr(s, f)
        for f in [
            "ADMIN_PATH",
            "ADMIN_TITLE",
            "ADMIN_REQUIRE_SUPERUSER",
        ]
    )
    ctx.record("admin_settings_patched", has_cfg, f"ADMIN_PATH={getattr(s, 'ADMIN_PATH', None)}")

    # Setup module is importable with lazy sqladmin
    setup_mod = importlib.import_module("app.admin.setup")
    has_setup = callable(getattr(setup_mod, "setup_admin", None))
    ctx.record("setup_admin_callable", has_setup, "app.admin.setup.setup_admin is callable")

    # Auth backend is importable
    auth_mod = importlib.import_module("app.admin.auth")
    has_auth = hasattr(auth_mod, "AdminAuthBackend")
    ctx.record("auth_backend_present", has_auth, "AdminAuthBackend class present")

    # Views module has MODEL_ADMINS
    views_mod = importlib.import_module("app.admin.views")
    model_admins = getattr(views_mod, "MODEL_ADMINS", [])
    ctx.record(
        "model_admins_discovered",
        len(model_admins) >= 2,
        f"{len(model_admins)} ModelAdmin classes generated",
    )

    # main.py has setup_admin(app) call
    main_src = (ctx.project_dir / "app/main.py").read_text()
    ctx.record(
        "main_py_patched", "setup_admin(app)" in main_src, "setup_admin(app) present in main.py"
    )

    return ctx.report_section


SQLADMIN = Scenario(
    name="sqladmin_panel",
    archetype="FastAPI-native admin panel with SQLAdmin + auth gate",
    models={
        "Invoice": {"number": "str", "total": "float", "status": "str"},
    },
    tools=[
        ("add_multi_tenancy", "adapt.extend.auth_access.add_multi_tenancy"),
        ("add_sqladmin", "adapt.extend.infrastructure.add_sqladmin"),
    ],
    flow=flow_sqladmin,
)

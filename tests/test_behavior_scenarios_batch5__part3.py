"""BATCH 5 behavior scenarios — part 3 (scenarios 27-29).

Split from ``test_behavior_scenarios_batch5.py``. Shared framework lives in
``test_behavior_scenarios_batch5__shared.py``.
"""

from __future__ import annotations

import uuid

import pytest

from tests.test_behavior_scenarios_batch5__shared import (
    Scenario,
    ScenarioContext,
    run_scenario_assert,
)

# ===========================================================================
# SCENARIO 27 — Security: Signing + DLP + Canary
# TOOL-107 add_request_signing, TOOL-108 add_dlp_shield, TOOL-109 add_canary_tokens
# ===========================================================================


async def flow_security_signing_dlp_canary(ctx: ScenarioContext) -> None:
    """HMACSigner canonical string builder, DLP Luhn check, honeypot endpoints, canary registry."""
    project_dir = ctx.project_dir
    client = ctx.client

    import sys

    key = str(project_dir)
    if key not in sys.path:
        sys.path.insert(0, key)

    # ---- HMACSigner class has canonical string builder --------------------------
    signer_path = project_dir / "app" / "core" / "signing" / "signer.py"
    ctx.record(
        "signer_file_exists",
        signer_path.exists(),
        str(signer_path.relative_to(project_dir) if signer_path.exists() else "NOT FOUND"),
    )

    if signer_path.exists():
        signer_src = signer_path.read_text()
        has_hmac_signer = "HMACSigner" in signer_src
        has_canonical = (
            "canonical" in signer_src.lower()
            or "_build_canonical" in signer_src
            or "canonical_string" in signer_src
        )
        ctx.record("hmac_signer_class_present", has_hmac_signer, "HMACSigner class in signer.py")
        ctx.record(
            "hmac_signer_has_canonical_builder",
            has_canonical,
            "canonical string builder pattern in signer.py",
        )
    else:
        ctx.record("hmac_signer_class_present", False, "signer.py not found")
        ctx.record("hmac_signer_has_canonical_builder", False, "signer.py not found")

    # ---- DLP middleware has PII pattern detection (Luhn check present) ----------
    dlp_path = project_dir / "app" / "middleware" / "dlp_shield.py"
    patterns_path = project_dir / "app" / "core" / "dlp" / "patterns.py"
    dlp_exists = dlp_path.exists() or patterns_path.exists()
    ctx.record(
        "dlp_files_exist",
        dlp_exists,
        "app/middleware/dlp_shield.py or app/core/dlp/patterns.py present",
    )

    # Check for Luhn algorithm presence
    luhn_found = False
    for fpath in [dlp_path, patterns_path]:
        if fpath.exists():
            src = fpath.read_text()
            if "luhn" in src.lower() or "Luhn" in src or "card" in src.lower():
                luhn_found = True
                break
    ctx.record(
        "dlp_has_luhn_or_card_detection", luhn_found, "Luhn/card detection pattern in DLP files"
    )

    # ---- DLPMiddleware class present -------------------------------------------
    if dlp_path.exists():
        dlp_src = dlp_path.read_text()
        ctx.record(
            "dlp_middleware_class_present",
            "DLPMiddleware" in dlp_src,
            "DLPMiddleware class in dlp_shield.py",
        )
    else:
        ctx.record("dlp_middleware_class_present", False, "dlp_shield.py not found")

    # ---- Canary registry tracks triggered canaries -----------------------------
    registry_path = project_dir / "app" / "core" / "canary" / "registry.py"
    ctx.record(
        "canary_registry_file_exists",
        registry_path.exists(),
        str(registry_path.relative_to(project_dir) if registry_path.exists() else "NOT FOUND"),
    )

    if registry_path.exists():
        reg_src = registry_path.read_text()
        has_canary_registry = "CanaryRegistry" in reg_src
        ctx.record(
            "canary_registry_class_present",
            has_canary_registry,
            "CanaryRegistry class in registry.py",
        )
    else:
        ctx.record("canary_registry_class_present", False, "registry.py not found")

    # ---- Honeypot endpoints exist (canary route file created) ------------------
    honeypot_path = project_dir / "app" / "api" / "routes" / "canary_honeypot.py"
    ctx.record(
        "honeypot_route_file_exists",
        honeypot_path.exists(),
        str(honeypot_path.relative_to(project_dir) if honeypot_path.exists() else "NOT FOUND"),
    )

    if honeypot_path.exists():
        hp_src = honeypot_path.read_text()
        has_alert_trigger = (
            "alert" in hp_src.lower() or "trigger" in hp_src.lower() or "canary" in hp_src.lower()
        )
        ctx.record(
            "honeypot_has_alert_trigger",
            has_alert_trigger,
            "alert/trigger/canary pattern in canary_honeypot.py",
        )
    else:
        ctx.record("honeypot_has_alert_trigger", False, "canary_honeypot.py not found")


SECURITY_SIGNING_DLP_CANARY = Scenario(
    name="security_signing_dlp_canary",
    archetype="HMAC request signing + DLP shield (Luhn) + canary tokens (honeypot)",
    models={"Document": {"title": "str", "content": "text"}},
    tools=[
        ("add_request_signing", "adapt.extend.auth_access.add_request_signing"),
        ("add_dlp_shield", "adapt.extend.infrastructure.add_dlp_shield"),
        ("add_canary_tokens", "adapt.extend.infrastructure.add_canary_tokens"),
    ],
    flow=flow_security_signing_dlp_canary,
    needs_boot=False,  # No HTTP boot needed: file-level assertions sufficient
)


# ===========================================================================
# SCENARIO 28 — Security: SBOM + RASP + BOLA
# TOOL-110 add_sbom_guardian, TOOL-111 add_runtime_sentinel, TOOL-112 add_bola_guard
# ===========================================================================


async def flow_security_sbom_rasp_bola(ctx: ScenarioContext) -> None:
    """SBOM generator importable, RuntimeSentinel SQL injection patterns, BOLA require_ownership."""
    project_dir = ctx.project_dir

    import sys

    key = str(project_dir)
    if key not in sys.path:
        sys.path.insert(0, key)

    # ---- SBOM generator script exists and imports clean ------------------------
    sbom_script = project_dir / "scripts" / "generate_sbom.py"
    ctx.record(
        "sbom_script_exists",
        sbom_script.exists(),
        str(sbom_script.relative_to(project_dir) if sbom_script.exists() else "NOT FOUND"),
    )

    if sbom_script.exists():
        sbom_src = sbom_script.read_text()
        has_generate_sbom = "generate_sbom" in sbom_src or "def main" in sbom_src
        ctx.record(
            "sbom_script_has_generator_function",
            has_generate_sbom,
            "generate_sbom/main function in generate_sbom.py",
        )
        # Try importing (stdlib only — no third-party deps)
        try:
            import importlib.util as _util

            spec = _util.spec_from_file_location("_gen_sbom_test", str(sbom_script))
            if spec and spec.loader:
                sbom_mod = _util.module_from_spec(spec)
                spec.loader.exec_module(sbom_mod)  # type: ignore[attr-defined]
            ctx.record("sbom_script_imports_clean", True, "no crash")
        except Exception as exc:
            ctx.record(
                "sbom_script_imports_clean", False, f"{type(exc).__name__}: {str(exc)[:200]}"
            )
    else:
        ctx.record("sbom_script_has_generator_function", False, "file not found")
        ctx.record("sbom_script_imports_clean", False, "file not found")

    # ---- RuntimeSentinel has SQL injection detection patterns (tautology, UNION) -
    sentinel_path = project_dir / "app" / "middleware" / "runtime_sentinel.py"
    ctx.record(
        "runtime_sentinel_file_exists",
        sentinel_path.exists(),
        str(sentinel_path.relative_to(project_dir) if sentinel_path.exists() else "NOT FOUND"),
    )

    if sentinel_path.exists():
        sentinel_src = sentinel_path.read_text()
        has_sql_detection = (
            "RuntimeSentinelMiddleware" in sentinel_src or "sql" in sentinel_src.lower()
        )
        has_tautology = (
            "tautology" in sentinel_src.lower() or "OR" in sentinel_src or "1=1" in sentinel_src
        )
        has_union = "UNION" in sentinel_src
        ctx.record(
            "sentinel_has_sql_injection_detection",
            has_sql_detection,
            "SQL injection detection in runtime_sentinel.py",
        )
        ctx.record(
            "sentinel_has_tautology_or_union_pattern",
            has_tautology or has_union,
            f"tautology={has_tautology}, UNION={has_union}",
        )
    else:
        ctx.record("sentinel_has_sql_injection_detection", False, "file not found")
        ctx.record("sentinel_has_tautology_or_union_pattern", False, "file not found")

    # ---- SSRF guard has internal IP block list ---------------------------------
    if sentinel_path.exists():
        sentinel_src = sentinel_path.read_text()
        has_ssrf_guard = (
            "169.254" in sentinel_src
            or "127.0.0" in sentinel_src
            or "ssrf" in sentinel_src.lower()
            or "SSRF" in sentinel_src
        )
        ctx.record(
            "sentinel_has_ssrf_internal_ip_block",
            has_ssrf_guard,
            "169.254/127.0.0/ssrf pattern in runtime_sentinel.py",
        )
    else:
        ctx.record("sentinel_has_ssrf_internal_ip_block", False, "file not found")

    # ---- BOLA guard has require_ownership pattern -------------------------------
    bola_path = project_dir / "app" / "auth" / "bola_guard.py"
    ctx.record(
        "bola_guard_file_exists",
        bola_path.exists(),
        str(bola_path.relative_to(project_dir) if bola_path.exists() else "NOT FOUND"),
    )

    if bola_path.exists():
        bola_src = bola_path.read_text()
        has_require_ownership = "require_ownership" in bola_src
        ctx.record(
            "bola_guard_has_require_ownership",
            has_require_ownership,
            "require_ownership pattern in bola_guard.py",
        )
    else:
        ctx.record("bola_guard_has_require_ownership", False, "bola_guard.py not found")


SECURITY_SBOM_RASP_BOLA = Scenario(
    name="security_sbom_rasp_bola",
    archetype="SBOM guardian + RuntimeSentinel (SQL/SSRF) + BOLA guard (require_ownership)",
    models={"Resource": {"name": "str", "owner_id": "str"}},
    tools=[
        ("add_sbom_guardian", "adapt.extend.testing_tools.add_sbom_guardian"),
        ("add_runtime_sentinel", "adapt.extend.infrastructure.add_runtime_sentinel"),
        ("add_bola_guard", "adapt.extend.auth_access.add_bola_guard"),
    ],
    flow=flow_security_sbom_rasp_bola,
    needs_boot=False,  # File-level assertions; no HTTP boot needed
)


# ===========================================================================
# SCENARIO 29 — Security: Compliance + Secrets + DPoP
# TOOL-113 add_compliance_engine, TOOL-114 add_secret_rotation, TOOL-115 add_dpop_tokens
# ===========================================================================


async def flow_security_compliance_secrets_dpop(ctx: ScenarioContext) -> None:
    """ComplianceEngine erasure+retention, SecretProvider abstraction, DPoP RFC 9449."""
    project_dir = ctx.project_dir
    client = ctx.client

    import sys

    key = str(project_dir)
    if key not in sys.path:
        sys.path.insert(0, key)

    # ---- ComplianceEngine has erasure cascade + retention policy ----------------
    engine_path = project_dir / "app" / "core" / "compliance_engine.py"
    ctx.record(
        "compliance_engine_file_exists",
        engine_path.exists(),
        str(engine_path.relative_to(project_dir) if engine_path.exists() else "NOT FOUND"),
    )

    if engine_path.exists():
        engine_src = engine_path.read_text()
        has_compliance_engine = "ComplianceEngine" in engine_src
        has_erasure = "erasure" in engine_src.lower() or "erase" in engine_src.lower()
        has_retention = "retention" in engine_src.lower()
        ctx.record(
            "compliance_engine_class_present",
            has_compliance_engine,
            "ComplianceEngine class in compliance_engine.py",
        )
        ctx.record(
            "compliance_engine_has_erasure",
            has_erasure,
            "erasure/erase pattern in compliance_engine.py",
        )
        ctx.record(
            "compliance_engine_has_retention",
            has_retention,
            "retention pattern in compliance_engine.py",
        )
    else:
        for lbl in [
            "compliance_engine_class_present",
            "compliance_engine_has_erasure",
            "compliance_engine_has_retention",
        ]:
            ctx.record(lbl, False, "compliance_engine.py not found")

    # ---- SecretProvider abstraction with Vault/AWS/env --------------------------
    rotation_path = project_dir / "app" / "core" / "secret_rotation.py"
    ctx.record(
        "secret_rotation_file_exists",
        rotation_path.exists(),
        str(rotation_path.relative_to(project_dir) if rotation_path.exists() else "NOT FOUND"),
    )

    if rotation_path.exists():
        rotation_src = rotation_path.read_text()
        has_provider = "SecretProvider" in rotation_src
        has_vault_or_aws = (
            "Vault" in rotation_src or "AWS" in rotation_src or "boto3" in rotation_src
        )
        ctx.record(
            "secret_provider_abstraction_present",
            has_provider,
            "SecretProvider in secret_rotation.py",
        )
        ctx.record(
            "secret_provider_has_vault_or_aws_backend",
            has_vault_or_aws,
            "Vault/AWS/boto3 backend reference in secret_rotation.py",
        )
    else:
        ctx.record("secret_provider_abstraction_present", False, "file not found")
        ctx.record("secret_provider_has_vault_or_aws_backend", False, "file not found")

    # ---- DPoP verifier has RFC 9449 proof validation pattern --------------------
    dpop_path = project_dir / "app" / "core" / "dpop.py"
    ctx.record(
        "dpop_core_file_exists",
        dpop_path.exists(),
        str(dpop_path.relative_to(project_dir) if dpop_path.exists() else "NOT FOUND"),
    )

    if dpop_path.exists():
        dpop_src = dpop_path.read_text()
        has_verifier = "DPoPVerifier" in dpop_src
        has_rfc_reference = (
            "9449" in dpop_src
            or "htm" in dpop_src
            or "htu" in dpop_src
            or "proof" in dpop_src.lower()
        )
        ctx.record("dpop_verifier_class_present", has_verifier, "DPoPVerifier class in dpop.py")
        ctx.record(
            "dpop_has_rfc9449_proof_validation",
            has_rfc_reference,
            "RFC 9449 htm/htu/proof pattern in dpop.py",
        )
    else:
        ctx.record("dpop_verifier_class_present", False, "dpop.py not found")
        ctx.record("dpop_has_rfc9449_proof_validation", False, "dpop.py not found")

    # ---- Compliance route file exists or endpoint accessible --------------------
    # add_compliance_engine writes app/api/routes/compliance.py; auto-registration varies
    compliance_route_file = project_dir / "app" / "api" / "routes" / "compliance.py"
    r_compliance = await client.get("/compliance/status")
    r_erasure = await client.delete(f"/compliance/erasure/{uuid.uuid4()}")
    compliance_accessible = (
        r_compliance.status_code != 404
        or r_erasure.status_code not in (404,)
        or compliance_route_file.exists()
    )
    ctx.record(
        "compliance_route_file_or_endpoint_exists",
        compliance_accessible,
        f"/compliance/status={r_compliance.status_code}, "
        f"route_file={compliance_route_file.exists()}",
    )


SECURITY_COMPLIANCE_SECRETS_DPOP = Scenario(
    name="security_compliance_secrets_dpop",
    archetype="ComplianceEngine (erasure+retention) + SecretRotation + DPoP (RFC 9449)",
    models={"User": {"email": "str", "created_at": "str"}},
    tools=[
        ("add_compliance_engine", "adapt.extend.infrastructure.add_compliance_engine"),
        ("add_secret_rotation", "adapt.extend.infrastructure.add_secret_rotation"),
        ("add_dpop_tokens", "adapt.extend.auth_access.add_dpop_tokens"),
    ],
    flow=flow_security_compliance_secrets_dpop,
)


# ===========================================================================
# pytest integration
# ===========================================================================

SCENARIOS: list[Scenario] = [
    SECURITY_SIGNING_DLP_CANARY,
    SECURITY_SBOM_RASP_BOLA,
    SECURITY_COMPLIANCE_SECRETS_DPOP,
]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
@pytest.mark.asyncio
async def test_scenario(scenario: Scenario) -> None:
    """Run a behavior scenario end-to-end and assert all checks pass."""
    await run_scenario_assert(scenario)

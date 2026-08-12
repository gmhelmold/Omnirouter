"""Tests for module: payments"""

import ast
import pytest
import tempfile
import shutil
from pathlib import Path

from core.models import Finding, Severity


class TestPaymentsScaffold:
    """Tests for the payments scaffold tool."""

    def test_scaffold_creates_module(self):
        """Scaffold must create a payments module with expected files."""
        from modules.payments.tools.scaffold_payments import generate_payment_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_payment_module(project_dir, with_subscriptions=True)

            assert "created_files" in result
            assert "payments_path" in result
            assert result["provider"] == "stripe"
            assert result["with_subscriptions"] is True

            created = result["created_files"]
            assert any("checkout.py" in f for f in created)
            assert any("webhooks.py" in f for f in created)
            assert any("handlers.py" in f for f in created)
            assert any("subscriptions.py" in f for f in created)
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_creates_valid_python(self):
        """All generated Python files must be syntactically valid."""
        from modules.payments.tools.scaffold_payments import generate_payment_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_payment_module(project_dir, with_subscriptions=True)

            for rel_path in result["created_files"]:
                fpath = Path(project_dir) / rel_path
                if fpath.suffix == ".py":
                    source = fpath.read_text(encoding="utf-8")
                    ast.parse(source)
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_idempotent(self):
        """Running scaffold twice must be safe."""
        from modules.payments.tools.scaffold_payments import generate_payment_module

        project_dir = tempfile.mkdtemp()
        try:
            result1 = generate_payment_module(project_dir, with_subscriptions=True)
            result2 = generate_payment_module(project_dir, with_subscriptions=True)

            assert len(result1["created_files"]) == len(result2["created_files"])
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_invalid_provider_raises(self):
        """Scaffold with unsupported provider must raise ValueError."""
        from modules.payments.tools.scaffold_payments import generate_payment_module

        project_dir = tempfile.mkdtemp()
        try:
            with pytest.raises(ValueError, match="Unsupported provider"):
                generate_payment_module(project_dir, provider="paypal")
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_without_subscriptions(self):
        """Scaffold without subscriptions must not include subscriptions.py."""
        from modules.payments.tools.scaffold_payments import generate_payment_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_payment_module(project_dir, with_subscriptions=False)

            assert not any("subscriptions.py" in f for f in result["created_files"])
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_has_webhook_signature_verification(self):
        """Generated webhooks.py must include signature verification."""
        from modules.payments.tools.scaffold_payments import generate_payment_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_payment_module(project_dir)

            webhooks_path = Path(result["payments_path"]) / "webhooks.py"
            webhooks_source = webhooks_path.read_text()
            assert "signature" in webhooks_source.lower()
            assert "webhook" in webhooks_source.lower()
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_has_idempotency_keys(self):
        """Generated code must include idempotency key handling."""
        from modules.payments.tools.scaffold_payments import generate_payment_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_payment_module(project_dir)

            handlers_path = Path(result["payments_path"]) / "handlers.py"
            handlers_source = handlers_path.read_text()
            assert "idempotency" in handlers_source.lower()
        finally:
            shutil.rmtree(project_dir)

    def test_scaffold_no_raw_card_data(self):
        """Generated code must NOT handle raw card data (PCI compliance)."""
        from modules.payments.tools.scaffold_payments import generate_payment_module

        project_dir = tempfile.mkdtemp()
        try:
            result = generate_payment_module(project_dir)

            for rel_path in result["created_files"]:
                fpath = Path(project_dir) / rel_path
                if fpath.suffix == ".py":
                    source = fpath.read_text().lower()
                    assert "card_number" not in source
                    assert "cvv" not in source
                    assert "credit_card" not in source
        finally:
            shutil.rmtree(project_dir)


class TestPaymentsVerify:
    """Tests for the payments verify tool."""

    def test_verify_on_empty_project(self):
        """Verify must handle empty project gracefully."""
        from modules.payments.tools.verify_payments import verify_payment_config

        project_dir = tempfile.mkdtemp()
        try:
            findings = verify_payment_config(project_dir)
            assert isinstance(findings, list)
        finally:
            shutil.rmtree(project_dir)

    def test_verify_finds_missing_webhook_signature(self):
        """Verify must detect missing webhook signature verification."""
        from modules.payments.tools.verify_payments import verify_payment_config

        project_dir = tempfile.mkdtemp()
        try:
            bad_code = '''
import stripe
@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    event = stripe.Event.construct_from(json.loads(payload), stripe.api_key)
'''
            (Path(project_dir) / "webhooks.py").write_text(bad_code)

            findings = verify_payment_config(project_dir)
            rule_ids = [f.rule_id for f in findings]
            assert "PAY-01" in rule_ids
        finally:
            shutil.rmtree(project_dir)

    def test_verify_finds_missing_idempotency(self):
        """Verify must detect missing idempotency keys."""
        from modules.payments.tools.verify_payments import verify_payment_config

        project_dir = tempfile.mkdtemp()
        try:
            bad_code = '''
import stripe
def create_checkout_session(user_id):
    return stripe.checkout.Session.create(
        customer=user_id,
        mode="payment",
    )
'''
            (Path(project_dir) / "checkout.py").write_text(bad_code)

            findings = verify_payment_config(project_dir)
            rule_ids = [f.rule_id for f in findings]
            assert "PAY-02" in rule_ids
        finally:
            shutil.rmtree(project_dir)

    def test_verify_finds_raw_card_data(self):
        """Verify must detect raw card data handling."""
        from modules.payments.tools.verify_payments import verify_payment_config

        project_dir = tempfile.mkdtemp()
        try:
            bad_code = '''
import stripe
def process_payment(card_number, cvv, exp_month, exp_year):
    token = stripe.Token.create(card={"number": card_number, "cvv": cvv})
'''
            (Path(project_dir) / "payments.py").write_text(bad_code)

            findings = verify_payment_config(project_dir)
            rule_ids = [f.rule_id for f in findings]
            assert "PAY-03" in rule_ids
        finally:
            shutil.rmtree(project_dir)

    def test_verify_clean_project_returns_list(self):
        """Verify must return a list when run on scaffolded payments module."""
        from modules.payments.tools.verify_payments import verify_payment_config
        from modules.payments.tools.scaffold_payments import generate_payment_module

        project_dir = tempfile.mkdtemp()
        try:
            generate_payment_module(project_dir, with_subscriptions=True)
            findings = verify_payment_config(project_dir)

            assert isinstance(findings, list)
        finally:
            shutil.rmtree(project_dir)


class TestPaymentsMCPTool:
    """Tests for MCP tool registration."""

    def test_scaffold_mcp_tool_registered(self):
        """Scaffold must have MCP_TOOL dict."""
        from modules.payments.tools.scaffold_payments import MCP_TOOL

        assert "name" in MCP_TOOL
        assert "entry" in MCP_TOOL
        assert MCP_TOOL["entry"] == "generate_payment_module"

    def test_verify_mcp_tool_registered(self):
        """Verify must have MCP_TOOL dict."""
        from modules.payments.tools.verify_payments import MCP_TOOL

        assert "name" in MCP_TOOL
        assert "entry" in MCP_TOOL
        assert MCP_TOOL["entry"] == "verify_payment_config"

"""test_fk_regression.py — FK auto-detection regression tests.

Covers the two confirmed failure modes fixed in generators/database/model.py
and generators/database/alembic_migration.py:

  (a) PHANTOM TABLE: a field like ``tracking_id: "str"`` (NOT a reference to
      another model) must NOT become a ForeignKey.  It must stay a plain
      String column.

  (b) PLURALIZATION/MULTIWORD MISMATCH: a field ``vaccine_lot_id`` referencing
      model ``VaccineLot`` must produce ``ForeignKey("vaccinelots.id")`` —
      matching the ``__tablename__ = "vaccinelots"`` that ``VaccineLot`` itself
      gets — not ``ForeignKey("vaccine_lots.id")``.

Run from the skill root:
    PYTHONPATH=. .venv/bin/python -m pytest tests/test_fk_regression.py -v
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SKILL_ROOT = Path(__file__).resolve().parent.parent


def _gen(tmp_path: Path, **kwargs) -> Path:
    """Call generate_project and return the project dir."""
    sys.path.insert(0, str(_SKILL_ROOT))
    from generators.orchestrator import generate_project  # noqa: PLC0415

    project_dir = tmp_path / "proj"
    result = generate_project(output_dir=str(project_dir), **kwargs)
    assert result["total_files"] > 0, "orchestrator produced no files"
    return project_dir


# ---------------------------------------------------------------------------
# (a) Phantom-table regression: non-model *_id field stays plain column
# ---------------------------------------------------------------------------

class TestPhantomTablePrevented:
    """A *_id field whose stem is NOT a known model must NOT become a ForeignKey."""

    def test_string_tracking_id_stays_string(self, tmp_path: Path) -> None:
        """``tracking_id: 'str'`` with no Tracking model → String(255), no FK."""
        project_dir = _gen(
            tmp_path,
            name="phantom",
            models={"Widget": {"name": "str", "tracking_id": "str"}},
            owner_models={"Widget": "user"},
            with_auth=True,
            with_docker_compose=False,
            with_ci=False,
            with_otel=False,
            with_prometheus=False,
        )
        model_src = (project_dir / "app" / "models" / "widget.py").read_text()

        # Must NOT have a ForeignKey for tracking_id
        assert 'ForeignKey("trackings.id"' not in model_src, (
            "tracking_id must not become ForeignKey('trackings.id') — "
            "there is no Tracking model in this project"
        )
        # Must have a String column
        assert 'tracking_id: Mapped[str] = mapped_column(String(255))' in model_src, (
            f"tracking_id should be a plain String column.\nGot:\n{model_src}"
        )

    def test_string_microchip_id_stays_string(self, tmp_path: Path) -> None:
        """``microchip_id: 'str'`` with no Microchip model → String(255), no FK."""
        project_dir = _gen(
            tmp_path,
            name="microchip",
            models={"Pet": {"name": "str", "microchip_id": "str"}},
            owner_models={},
            with_auth=False,
            with_docker_compose=False,
            with_ci=False,
            with_otel=False,
            with_prometheus=False,
        )
        model_src = (project_dir / "app" / "models" / "pet.py").read_text()

        assert 'ForeignKey("microchips.id"' not in model_src, (
            "microchip_id must not become ForeignKey — no Microchip model exists"
        )
        assert "String(255)" in model_src, (
            "microchip_id should stay a String column"
        )

    def test_phantom_migration_has_no_phantom_fk(self, tmp_path: Path) -> None:
        """The baseline migration for a non-FK *_id field must also avoid phantom FKs."""
        project_dir = _gen(
            tmp_path,
            name="mig_phantom",
            models={"Widget": {"name": "str", "tracking_id": "str"}},
            owner_models={"Widget": "user"},
            with_auth=True,
            with_docker_compose=False,
            with_ci=False,
            with_otel=False,
            with_prometheus=False,
        )
        migration_files = list((project_dir / "alembic" / "versions").glob("*.py"))
        assert migration_files, "No migration file generated"
        migration_src = migration_files[0].read_text()

        assert 'ForeignKey("trackings.id"' not in migration_src, (
            "Migration must not emit ForeignKey('trackings.id') — "
            "no Tracking model exists"
        )
        # Should emit a plain String column instead
        assert '"tracking_id", sa.String' in migration_src, (
            f"Migration should emit tracking_id as sa.String.\nGot:\n{migration_src}"
        )


# ---------------------------------------------------------------------------
# (b) Multiword-model regression: FK target matches actual __tablename__
# ---------------------------------------------------------------------------

class TestMultiwordModelFKMatch:
    """A *_id FK to a multiword model must use the same table name as that model."""

    def test_vaccine_lot_id_fk_matches_vaccinelots_table(self, tmp_path: Path) -> None:
        """``vaccine_lot_id`` → ``ForeignKey("vaccinelots.id")`` not ``vaccine_lots.id``."""
        project_dir = _gen(
            tmp_path,
            name="vaccine",
            models={
                "VaccineLot": {"name": "str"},
                "Shipment": {"vaccine_lot_id": "str"},
            },
            owner_models={},
            with_auth=False,
            with_docker_compose=False,
            with_ci=False,
            with_otel=False,
            with_prometheus=False,
        )
        model_src = (project_dir / "app" / "models" / "shipment.py").read_text()

        # Table name for VaccineLot is pluralize("vaccinelot") = "vaccinelots"
        assert 'ForeignKey("vaccinelots.id"' in model_src, (
            f"vaccine_lot_id FK must target 'vaccinelots.id' (matches VaccineLot.__tablename__).\n"
            f"Got:\n{model_src}"
        )
        # Must NOT have the mismatched name
        assert 'ForeignKey("vaccine_lots.id"' not in model_src, (
            "FK must NOT target 'vaccine_lots.id' — that table does not exist"
        )

    def test_vaccinelot_model_tablename(self, tmp_path: Path) -> None:
        """VaccineLot model's __tablename__ must be 'vaccinelots' (not 'vaccine_lots')."""
        project_dir = _gen(
            tmp_path,
            name="vaccine_table",
            models={"VaccineLot": {"name": "str"}},
            owner_models={},
            with_auth=False,
            with_docker_compose=False,
            with_ci=False,
            with_otel=False,
            with_prometheus=False,
        )
        model_src = (project_dir / "app" / "models" / "vaccinelot.py").read_text()

        assert '__tablename__ = "vaccinelots"' in model_src, (
            f"VaccineLot.__tablename__ should be 'vaccinelots'.\nGot:\n{model_src}"
        )

    def test_multiword_migration_fk_matches_table(self, tmp_path: Path) -> None:
        """Alembic migration FK target must match the actual VaccineLot table name."""
        project_dir = _gen(
            tmp_path,
            name="vaccine_mig",
            models={
                "VaccineLot": {"name": "str"},
                "Shipment": {"vaccine_lot_id": "str"},
            },
            owner_models={},
            with_auth=False,
            with_docker_compose=False,
            with_ci=False,
            with_otel=False,
            with_prometheus=False,
        )
        migration_files = list((project_dir / "alembic" / "versions").glob("*.py"))
        assert migration_files, "No migration file generated"
        migration_src = migration_files[0].read_text()

        assert '"vaccinelots.id"' in migration_src, (
            f"Migration FK must target 'vaccinelots.id'.\nGot:\n{migration_src}"
        )
        assert '"vaccine_lots.id"' not in migration_src, (
            "Migration must NOT target 'vaccine_lots.id' — that table does not exist"
        )

    def test_order_item_fks_still_correct(self, tmp_path: Path) -> None:
        """Existing correct behavior: real cross-model FKs still work after the fix."""
        project_dir = _gen(
            tmp_path,
            name="shop",
            models={
                "Product": {"name": "str", "price": "Decimal"},
                "Order": {"status": "str", "total": "Decimal"},
            },
            owner_models={"Order": "user"},
            with_auth=True,
            with_docker_compose=False,
            with_ci=False,
            with_otel=False,
            with_prometheus=False,
        )
        # OrderItem is auto-injected — it has order_id + product_id FKs
        orderitem_src = (project_dir / "app" / "models" / "orderitem.py").read_text()

        assert 'ForeignKey("orders.id"' in orderitem_src, (
            "order_id FK must still point to orders.id"
        )
        assert 'ForeignKey("products.id"' in orderitem_src, (
            "product_id FK must still point to products.id"
        )


# ---------------------------------------------------------------------------
# (c) End-to-end: emitted project pytest passes (no NoReferencedTableError)
# ---------------------------------------------------------------------------

class TestEmittedProjectPytest:
    """The emitted project's own pytest suite must pass after the FK fix.

    This catches NoReferencedTableError at Base.metadata.create_all time,
    which is what the bug produced.
    """

    @pytest.mark.slow
    def test_multiword_nonmodel_id_project_pytest_passes(self, tmp_path: Path) -> None:
        """Generate a project with non-FK *_id fields (no matching model for the stem),
        configure its .env, install deps, run its tests — no NoReferencedTableError.

        The specific scenario tested:
        - ``tracking_id: "str"`` on Widget — no Tracking model, must stay String.
        - ``microchip_id: "str"`` on Pet — no Microchip model, must stay String.
        Before the fix, these became phantom FKs and crashed Base.metadata.create_all
        with NoReferencedTableError.
        """
        project_dir = _gen(
            tmp_path,
            name="vet",
            models={
                "Widget": {"name": "str", "tracking_id": "str"},
                "Pet": {"name": "str", "microchip_id": "str"},
            },
            owner_models={"Pet": "user"},
            with_auth=True,
            with_docker_compose=False,
            with_ci=False,
            with_otel=False,
            with_prometheus=False,
        )

        # Write a .env the emitted conftest can use (SQLite backend)
        env_file = project_dir / ".env"
        env_file.write_text(
            "SECRET_KEY=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n"
            "ENVIRONMENT=local\n"
            "FIRST_SUPERUSER_EMAIL=admin@x.com\n"
            "FIRST_SUPERUSER_PASSWORD=testpassword123\n"
            "RATE_LIMITING_ENABLED=false\n"
        )

        # Create a venv and install requirements
        venv_dir = project_dir / ".venv"
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            check=True,
            capture_output=True,
        )
        pip = venv_dir / "bin" / "pip"
        subprocess.run(
            [str(pip), "install", "-r", str(project_dir / "requirements.txt"), "-q"],
            check=True,
            capture_output=True,
            timeout=300,
        )

        # Run the generated project's pytest suite
        python = venv_dir / "bin" / "python3"
        result = subprocess.run(
            [str(python), "-m", "pytest", "tests/", "-v", "--tb=short", "--no-header"],
            cwd=str(project_dir),
            env={
                **__import__("os").environ,
                "PYTHONPATH": str(project_dir),
                "SECRET_KEY": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                "ENVIRONMENT": "local",
                "RATE_LIMITING_ENABLED": "false",
            },
            capture_output=True,
            text=True,
            timeout=180,
        )

        # The specific failure we're guarding against
        assert "NoReferencedTableError" not in result.stdout, (
            f"NoReferencedTableError in emitted project pytest!\n{result.stdout[-3000:]}"
        )
        assert "NoReferencedTableError" not in result.stderr, (
            f"NoReferencedTableError in emitted project stderr!\n{result.stderr[-1000:]}"
        )
        assert result.returncode == 0, (
            f"Emitted project pytest FAILED (rc={result.returncode})\n"
            f"--- stdout ---\n{result.stdout[-4000:]}\n"
            f"--- stderr ---\n{result.stderr[-1000:]}"
        )

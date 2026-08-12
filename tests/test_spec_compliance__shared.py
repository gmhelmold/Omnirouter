"""Shared helpers for split spec-compliance tests.

For each of the 10 critical tools, we generate a minimal project into a
temp directory, run the tool, and then grep the generated files for patterns
that prove each Section-8 Invariant is implemented in the generated code.

Tests are intentionally coarse (string-search based) so they run fast and
stay maintenance-light.  Every assertion documents which invariant it checks.
"""

from __future__ import annotations

import contextlib
import shutil
import textwrap
import uuid
from pathlib import Path

import pytest

from adapt.contracts import ToolInput

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(tmp_path: Path) -> Path:
    """Create a minimal FastAPI project scaffold in *tmp_path*.

    Produces just enough structure for every tool to find its target files:
    app/, app/models/item.py, app/crud/item.py, app/api/routes/item.py,
    app/schemas/item.py, app/main.py, alembic/versions/.
    """
    p = tmp_path / "proj"
    (p / "app" / "models").mkdir(parents=True)
    (p / "app" / "crud").mkdir(parents=True)
    (p / "app" / "schemas").mkdir(parents=True)
    (p / "app" / "api" / "routes").mkdir(parents=True)
    (p / "app" / "core").mkdir(parents=True)
    (p / "alembic" / "versions").mkdir(parents=True)
    (p / "requirements.txt").write_text("fastapi\nsqlalchemy\n")

    # alembic/versions/0001_initial.py — the no-op chain root.
    # The real scaffold (generators.database.alembic.generate_alembic) always
    # emits this so extend tools chaining off ``down_revision = "0001_initial"``
    # have a valid parent revision.  Without it, find_migration_head() raises
    # MigrationChainError ("scaffold must emit alembic/versions/0001_initial.py")
    # and every extend tool that emits a migration ERRORs during fixture build.
    (p / "alembic" / "versions" / "0001_initial.py").write_text(
        textwrap.dedent('''\
        """initial revision — chain root (no-op).

        Revision ID: 0001_initial
        Revises:
        Create Date: scaffold
        """
        from __future__ import annotations

        from typing import Sequence, Union

        # revision identifiers, used by Alembic.
        revision: str = "0001_initial"
        down_revision: Union[str, None] = None
        branch_labels: Union[str, Sequence[str], None] = None
        depends_on: Union[str, Sequence[str], None] = None


        def upgrade() -> None:
            """No-op: chain root exists solely so downstream migrations chain."""
            pass


        def downgrade() -> None:
            """No-op: nothing to undo at the chain root."""
            pass
        ''')
    )

    # Minimal base
    (p / "app" / "models" / "base.py").write_text(
        "from sqlalchemy.orm import DeclarativeBase\n\nclass Base(DeclarativeBase):\n    pass\n"
    )
    # A real model
    (p / "app" / "models" / "item.py").write_text(
        textwrap.dedent("""\
        from __future__ import annotations
        import uuid
        from sqlalchemy import String, Uuid
        from sqlalchemy.orm import Mapped, mapped_column
        from app.models.base import Base

        class Item(Base):
            __tablename__ = "items"
            id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
            title: Mapped[str] = mapped_column(String(255), nullable=False)
        """)
    )
    # app/crud/base.py — the shared CRUDBase single source of truth.
    # The real scaffold (generators.database.crud_base.generate_crud_base)
    # always emits this; add_soft_delete patches it in place and writes its
    # SOFT_DELETE_PATCH_APPLIED idempotency fingerprint here.  Without it the
    # tool can never record that it ran, so a second invocation re-applies
    # instead of returning no_op (breaks INV-SD-06).
    (p / "app" / "crud" / "base.py").write_text(
        textwrap.dedent("""\
        from __future__ import annotations
        import uuid
        from typing import Generic, TypeVar
        from sqlalchemy import func, select
        from sqlalchemy.ext.asyncio import AsyncSession

        ModelType = TypeVar("ModelType")


        class CRUDBase(Generic[ModelType]):
            def __init__(self, model: type[ModelType]) -> None:
                self.model = model

            async def get(self, session: AsyncSession, id: uuid.UUID):
                stmt = select(self.model).where(self.model.id == id)
                result = await session.execute(stmt)
                return result.scalar_one_or_none()

            async def get_multi(self, session: AsyncSession, *, skip: int = 0, limit: int = 20):
                stmt = select(self.model).offset(skip).limit(limit)
                result = await session.execute(stmt)
                return {"data": list(result.scalars().all()), "count": 0}

            async def delete(self, session: AsyncSession, id: uuid.UUID):
                obj = await self.get(session, id)
                if obj is None:
                    return None
                await session.delete(obj)
                await session.flush()
                return obj
        """)
    )
    (p / "app" / "crud" / "item.py").write_text(
        textwrap.dedent("""\
        from __future__ import annotations
        from sqlalchemy.ext.asyncio import AsyncSession

        async def get(session: AsyncSession, item_id):
            pass
        """)
    )
    (p / "app" / "api" / "routes" / "item.py").write_text(
        textwrap.dedent("""\
        from __future__ import annotations
        from fastapi import APIRouter
        from app.api.deps import CurrentUser, SessionDep

        router = APIRouter()

        @router.get("/items/")
        async def list_items(session: SessionDep, current_user: CurrentUser):
            return []
        """)
    )
    (p / "app" / "api" / "deps.py").write_text(
        textwrap.dedent("""\
        from __future__ import annotations
        from typing import Annotated
        from fastapi import Depends
        from sqlalchemy.ext.asyncio import AsyncSession

        async def get_session():
            pass

        SessionDep = Annotated[AsyncSession, Depends(get_session)]
        CurrentUser = str
        CurrentSuperuser = str
        """)
    )
    (p / "app" / "schemas" / "item.py").write_text(
        textwrap.dedent("""\
        from __future__ import annotations
        from pydantic import BaseModel

        class ItemPublic(BaseModel):
            id: str
            title: str
        """)
    )
    (p / "app" / "main.py").write_text(
        textwrap.dedent("""\
        from __future__ import annotations
        from fastapi import FastAPI
        from app.core.logging import configure_logging

        app = FastAPI()
        """)
    )
    (p / "app" / "core" / "logging.py").write_text("def configure_logging(): pass\n")
    (p / "app" / "api" / "main.py").write_text(
        "from fastapi import APIRouter\napi_router = APIRouter()\n"
    )
    return p


def _read_tree(project: Path) -> str:
    """Return concatenated text of all .py files under *project*."""
    parts: list[str] = []
    for f in sorted(project.rglob("*.py")):
        with contextlib.suppress(OSError):
            parts.append(f.read_text(errors="ignore"))
    return "\n".join(parts)

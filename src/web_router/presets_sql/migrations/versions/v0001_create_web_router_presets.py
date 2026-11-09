"""Create the presets tables: one row per preset, and one params table per tool, a typed column per param.

A param a preset leaves unset is NULL, so it reads back unset. A list of text
is a Postgres text array and JSON on SQLite, which has no array type; JSON
otherwise only for values that are structures by nature -- a schema,
a viewport, a template's inputs, ``provider_options``. Every value is written
out here rather than read from the package, so this migration keeps meaning
what it meant when it ran.

Revision ID: 0001
Revises:
Create Date: 2026-11-09
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TOOLS = ("search", "extract", "answer", "research", "browse", "run_template", "open_browser")


def _choice(name: str, *values: str) -> sa.Enum:
    # Not a native enum: a VARCHAR and a CHECK, the same on SQLite and Postgres.
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=max(map(len, values)))


def _column(name: str, kind: Any) -> sa.Column[Any]:
    return sa.Column(name, kind, nullable=True)


def _texts(name: str) -> sa.Column[Any]:
    return _column(name, sa.JSON().with_variant(postgresql.ARRAY(sa.Text()), "postgresql"))


def _params(tool: str, *columns: Any) -> None:
    table = f"web_router_{tool}_params"
    op.create_table(
        table,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        *columns,
        _column("provider_options", sa.JSON()),
    )


def upgrade() -> None:
    provider = "provider"
    _params(
        "search",
        _column(provider, sa.Text()),
        _column("max_results", sa.Integer()),
        _column("search_depth", _choice("ck_web_router_search_params_depth", "lite", "fast", "deep")),
        _texts("include_domains"),
        _texts("exclude_domains"),
        _column("country", sa.Text()),
        _column("locale", sa.Text()),
        _column("recency", _choice("ck_web_router_search_params_recency", "day", "week", "month", "year")),
        _column("effort", _choice("ck_web_router_search_params_effort", "low", "medium", "high", "max")),
        sa.CheckConstraint("max_results BETWEEN 1 AND 100", name="ck_web_router_search_params_max_results"),
    )
    _params(
        "extract",
        _column(provider, sa.Text()),
        _column("format", _choice("ck_web_router_extract_params_format", "markdown", "html", "text")),
        _column("profile", sa.Text()),
    )
    _params(
        "answer",
        _column(provider, sa.Text()),
        _texts("include_domains"),
        _texts("exclude_domains"),
        _column("country", sa.Text()),
        _column("locale", sa.Text()),
        _column("recency", _choice("ck_web_router_answer_params_recency", "day", "week", "month", "year")),
    )
    _params(
        "research",
        _column(provider, sa.Text()),
        _column("output_schema", sa.JSON()),
        _column("effort", _choice("ck_web_router_research_params_effort", "low", "medium", "high", "max")),
        _texts("include_domains"),
        _texts("exclude_domains"),
        _column("timeout", sa.Float()),
    )
    _params(
        "browse",
        _column(provider, sa.Text()),
        _column("start_url", sa.Text()),
        _column("output_schema", sa.JSON()),
        _column("max_steps", sa.Integer()),
        _texts("allowed_domains"),
        _column("timeout", sa.Float()),
        sa.CheckConstraint("max_steps BETWEEN 1 AND 10000", name="ck_web_router_browse_params_max_steps"),
    )
    _params(
        "run_template",
        _column("inputs", sa.JSON()),
        _column("format", _choice("ck_web_router_run_template_params_format", "markdown", "html", "text")),
    )
    _params(
        "open_browser",
        _column(provider, sa.Text()),
        _column("proxy_country", sa.Text()),
        _column("stealth", _choice("ck_web_router_open_browser_params_stealth", "off", "low", "high")),
        _column("solve_captchas", sa.Boolean()),
        _column("profile", sa.Text()),
        _column("persist_profile", sa.Boolean()),
        _column("timeout", sa.Float()),
        _column("idle_timeout", sa.Float()),
        _column("viewport", sa.JSON()),
        _column("headless", sa.Boolean()),
    )
    op.create_table(
        "web_router_presets",
        sa.Column("name", sa.String(100), primary_key=True),
        sa.Column("tool", _choice("ck_web_router_presets_tool", *TOOLS), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        *(
            sa.Column(
                f"{tool}_params_id",
                sa.Integer(),
                sa.ForeignKey(f"web_router_{tool}_params.id", name=f"fk_web_router_presets_{tool}_params_id"),
                nullable=True,
                unique=True,
            )
            for tool in TOOLS
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(description) <= 2000", name="ck_web_router_presets_description"),
        # The tool's params, and no other's.
        sa.CheckConstraint(
            " AND ".join(f"(tool = '{tool}') = ({tool}_params_id IS NOT NULL)" for tool in TOOLS),
            name="ck_web_router_presets_params_match_tool",
        ),
    )


def downgrade() -> None:
    op.drop_table("web_router_presets")
    for tool in reversed(TOOLS):
        op.drop_table(f"web_router_{tool}_params")

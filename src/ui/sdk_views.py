"""Render SDK responses (context usage, MCP status, server info) as Markdown.

Pure functions. Consumed by `/context`, `/mcp` and `/info` handlers.
"""

from typing import Any

from ..i18n import Translator


def _mono_table(rows: list[tuple[str, ...]], aligns: str) -> str:
    """Render rows as a fixed-width monospace block, wrapped in a fence.

    Telegram ignores the HTML `align` attribute on table cells, so a `<pre>`
    block with manual padding is the only reliable way to right-align columns.
    `aligns` is one char per column: 'r' right-justified, anything else left.
    """
    cols = len(aligns)
    widths = [max(len(r[i]) for r in rows) for i in range(cols)]
    out: list[str] = []
    for r in rows:
        cells = [
            r[i].rjust(widths[i]) if aligns[i] == "r" else r[i].ljust(widths[i])
            for i in range(cols)
        ]
        out.append("  ".join(cells).rstrip())
    return "```\n" + "\n".join(out) + "\n```"


def format_context_usage(
    usage: dict[str, Any],
    tr: Translator,
    provider: str | None = None,
    model: str | None = None,
) -> str:
    """Render the response of ClaudeSDKClient.get_context_usage() as Markdown.

    `provider`/`model` override the usage payload so the report names the
    backend the user actually selected; falls back to the payload's model.
    """
    total = int(usage.get("totalTokens") or 0)
    max_t = int(usage.get("maxTokens") or 0)
    pct = float(usage.get("percentage") or 0.0)
    model_name = model or str(usage.get("model") or "?")
    free = max(max_t - total, 0)
    summary = _mono_table(
        [
            (tr.t("context_lbl_provider"), provider or "?"),
            (tr.t("context_lbl_model"), model_name),
            (tr.t("context_lbl_fill"), f"{pct:.1f}%"),
            (tr.t("context_lbl_tokens"), f"{total:,} / {max_t:,}"),
            (tr.t("context_lbl_free"), f"{free:,}"),
        ],
        aligns="rl",
    )
    lines = [tr.t("context_title"), "", summary, "", tr.t("context_categories")]
    cats = sorted(
        (
            c
            for c in (usage.get("categories") or [])
            if int(c.get("tokens") or 0) > 0
        ),
        key=lambda c: int(c["tokens"]),
        reverse=True,
    )
    if cats:
        cat_rows: list[tuple[str, ...]] = [
            (
                tr.t("context_col_category"),
                tr.t("context_col_tokens"),
                tr.t("context_col_pct"),
            )
        ]
        for c in cats:
            tokens = int(c["tokens"])
            share = (tokens / total * 100) if total else 0.0
            cat_rows.append((str(c["name"]), f"{tokens:,}", f"{share:.0f}%"))
        lines.append(_mono_table(cat_rows, aligns="rrr"))
    else:
        lines.append(tr.t("context_cat_empty"))
    return "\n".join(lines)


_MCP_ICON: dict[str, str] = {
    "connected": "✅",
    "failed": "❌",
    "needs-auth": "🔑",
    "pending": "⏳",
    "disabled": "⏸",
}

# Render order — active first, broken last.
_MCP_GROUP_ORDER: tuple[str, ...] = (
    "connected", "needs-auth", "pending", "disabled", "failed",
)


def format_mcp_status(status: dict[str, Any], tr: Translator) -> str:
    servers = status.get("mcpServers") or []
    if not servers:
        return tr.t("mcp_empty")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for s in servers:
        st = str(s.get("status", "?"))
        grouped.setdefault(st, []).append(s)
    lines = [tr.t("mcp_header", count=len(servers))]
    seen: set[str] = set()
    ordered_keys = [k for k in _MCP_GROUP_ORDER if k in grouped] + [
        k for k in grouped if k not in _MCP_GROUP_ORDER
    ]
    for st in ordered_keys:
        items = grouped[st]
        seen.add(st)
        icon = _MCP_ICON.get(st, "•")
        title_key = f"mcp_group_{st.replace('-', '_')}"
        title = tr.t(title_key)
        if title == title_key:  # missing translation — fall back to raw status
            title = st
        lines.append("")
        lines.append(f"{icon} *{title}* ({len(items)})")
        for s in items:
            name = s.get("name", "?")
            scope = s.get("scope")
            tools = s.get("tools") or []
            extra: list[str] = []
            if scope:
                extra.append(str(scope))
            if st == "connected" and tools:
                extra.append(tr.t("mcp_tools_count", n=len(tools)))
            suffix = f" — {', '.join(extra)}" if extra else ""
            lines.append(f"   • `{name}`{suffix}")
            if s.get("error"):
                lines.append(
                    "     " + tr.t("mcp_error_line", error=str(s["error"])[:300])
                )
    return "\n".join(lines)


def format_server_info(info: dict[str, Any], tr: Translator) -> str:
    cmds = info.get("commands") or []
    style = info.get("output_style") or info.get("outputStyle") or "default"
    styles = (
        info.get("available_output_styles")
        or info.get("outputStyles")
        or []
    )
    lines = [tr.t("info_header"), tr.t("info_output_style", style=style)]
    if styles:
        lines.append(
            tr.t(
                "info_available_styles",
                styles=", ".join(str(s) for s in styles[:20]),
            )
        )
    lines.append(tr.t("info_commands_count", n=len(cmds)))
    if cmds:
        names: list[str] = []
        for c in cmds[:30]:
            n = c.get("name") if isinstance(c, dict) else str(c)
            if n:
                names.append(f"`/{n}`")
        if names:
            lines.append("\n".join(names))
    return "\n".join(lines)

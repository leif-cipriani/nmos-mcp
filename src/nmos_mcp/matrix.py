"""Crosspoint matrix rendering — the classic broadcast-router grid.

Columns are **senders** (sources), rows are **receivers** (destinations), and a cell
is marked where a receiver is subscribed to a sender. The connection data comes
straight from IS-04: each receiver advertises ``subscription = {sender_id, active}``,
so the whole grid is built from two registry queries (no per-Node IS-05 calls).

Exposed both as the ``crosspoint_matrix`` MCP tool and the ``nmos-crosspoint`` CLI.
"""

from __future__ import annotations

from typing import Any

# Cell markers.
MARK_ACTIVE = "X"      # subscribed and active (master_enable on)
MARK_INACTIVE = "o"    # subscribed to a sender but not currently active
MARK_NONE = "."        # not connected

_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_DIM = "\033[2m"
_RESET = "\033[0m"

_LABEL_WIDTH = 22  # receiver/sender label column width before truncation


def _sorted(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(resources, key=lambda r: (str(r.get("label") or "").lower(), str(r.get("id") or "")))


def _label(res: dict[str, Any]) -> str:
    return res.get("label") or "(unlabelled)"


def _truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _subscription(receiver: dict[str, Any]) -> tuple[str | None, bool]:
    sub = receiver.get("subscription") or {}
    return sub.get("sender_id"), bool(sub.get("active"))


def render_crosspoint(
    senders: list[dict[str, Any]],
    receivers: list[dict[str, Any]],
    *,
    color: bool = False,
) -> str:
    """Render an ASCII crosspoint matrix string for the given senders and receivers."""
    senders = _sorted(senders)
    receivers = _sorted(receivers)

    if not senders and not receivers:
        return "No senders or receivers found in the registry."

    scode = {s["id"]: f"S{i + 1}" for i, s in enumerate(senders)}
    rcode = {r["id"]: f"R{i + 1}" for i, r in enumerate(receivers)}

    codes = list(scode.values())
    cw = max([2] + [len(c) for c in codes])                       # column (cell) width
    rcw = max([2] + [len(c) for c in rcode.values()])             # receiver-code width
    left_w = rcw + 1 + _LABEL_WIDTH                               # width of the label gutter

    def colorize(text: str, mark: str, active: bool) -> str:
        if not color:
            return text
        if mark == MARK_ACTIVE:
            return text.replace(mark, f"{_GREEN}{mark}{_RESET}")
        if mark == MARK_INACTIVE:
            return text.replace(mark, f"{_YELLOW}{mark}{_RESET}")
        return text.replace(mark, f"{_DIM}{mark}{_RESET}")

    cells_header = " ".join(f"{c:<{cw}}" for c in codes)
    total_w = left_w + 2 + len(cells_header)

    lines: list[str] = []
    lines.append("Crosspoint matrix  (rows = receivers ↓, columns = senders →)")
    lines.append(f"  {MARK_ACTIVE} = active   {MARK_INACTIVE} = subscribed/inactive   {MARK_NONE} = not connected")
    lines.append("")
    lines.append(f"{'':<{left_w}}│ {cells_header}")
    lines.append("─" * left_w + "┼" + "─" * (total_w - left_w - 1))

    off_grid: list[tuple[str, str]] = []  # (receiver label, sender_id not shown as a column)
    active_routes = 0

    for r in receivers:
        sid, active = _subscription(r)
        left = f"{rcode[r['id']]:<{rcw}} {_truncate(_label(r), _LABEL_WIDTH):<{_LABEL_WIDTH}}"
        cells: list[str] = []
        for s in senders:
            if sid and s["id"] == sid:
                mark = MARK_ACTIVE if active else MARK_INACTIVE
                if active:
                    active_routes += 1
            else:
                mark = MARK_NONE
            cells.append(colorize(f"{mark:<{cw}}", mark, active))
        if sid and sid not in scode:
            off_grid.append((_label(r), sid))
        lines.append(f"{left}│ {' '.join(cells)}".rstrip())

    # Legends.
    lines.append("")
    lines.append("Senders (columns):")
    for s in senders:
        lines.append(f"  {scode[s['id']]:<{rcw + 1}} {_truncate(_label(s), _LABEL_WIDTH):<{_LABEL_WIDTH}} {s['id']}")
    lines.append("Receivers (rows):")
    for r in receivers:
        lines.append(f"  {rcode[r['id']]:<{rcw + 1}} {_truncate(_label(r), _LABEL_WIDTH):<{_LABEL_WIDTH}} {r['id']}")

    lines.append("")
    lines.append(f"{len(senders)} sender(s), {len(receivers)} receiver(s), {active_routes} active route(s).")
    if off_grid:
        lines.append("Note: some receivers are subscribed to a sender not present in the registry:")
        for label, sid in off_grid:
            lines.append(f"  - {label} -> {sid}")

    return "\n".join(lines)


async def build_and_render(query: Any, *, color: bool = False) -> str:
    """Fetch senders + receivers from the registry and render the matrix."""
    senders = await query.list_resources("senders")
    receivers = await query.list_resources("receivers")
    return render_crosspoint(senders, receivers, color=color)


def main() -> None:
    """``nmos-crosspoint`` CLI entry point — print the matrix and exit."""
    import argparse
    import asyncio
    import sys

    from .auth import TokenProvider
    from .config import load_settings
    from .discovery import RegistryResolver
    from .http import make_client
    from .query import QueryClient

    parser = argparse.ArgumentParser(
        prog="nmos-crosspoint",
        description="Draw an NMOS crosspoint matrix (senders × receivers) from the registry.",
    )
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colours.")
    args = parser.parse_args()

    async def run() -> None:
        settings = load_settings()
        client = make_client(settings, TokenProvider(settings))
        query = QueryClient(client, RegistryResolver(settings))
        try:
            use_color = not args.no_color and sys.stdout.isatty()
            text = await build_and_render(query, color=use_color)
        finally:
            await client.aclose()
        print(text)

    asyncio.run(run())


if __name__ == "__main__":
    main()

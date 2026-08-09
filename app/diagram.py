"""Render the translated service graph as an inline SVG diagram."""

from __future__ import annotations

from xml.sax.saxutils import escape

from app.translate import Translation

CARD_WIDTH = 168
CARD_HEIGHT = 66
GAP = 18
PADDING = 20
COLUMNS = 4
INTERNET_HEIGHT = 34

KIND_LABEL = {
    "runtime": "runtime",
    "database": "managed",
    "storage": "storage",
    "docker": "docker",
    "static": "static",
}


def grid_size(count: int) -> tuple[int, int]:
    if count == 0:
        return 0, 0
    columns = min(COLUMNS, count)
    rows = (count + columns - 1) // columns
    return columns, rows


def block_height(rows: int) -> int:
    return rows * CARD_HEIGHT + max(rows - 1, 0) * GAP


def card(x: int, y: int, name: str, subtitle: str, kind: str) -> str:
    label = KIND_LABEL.get(kind, kind)
    return f"""
  <g class="node {kind}">
    <rect x="{x}" y="{y}" width="{CARD_WIDTH}" height="{CARD_HEIGHT}" rx="8"/>
    <text class="name" x="{x + 12}" y="{y + 25}">{escape(name)}</text>
    <text class="sub"  x="{x + 12}" y="{y + 44}">{escape(subtitle)}</text>
    <text class="tag"  x="{x + CARD_WIDTH - 12}" y="{y + 20}" text-anchor="end">{escape(label)}</text>
  </g>"""


def layout(services: list, start_y: int, origin_x: int) -> tuple[str, int]:
    columns, rows = grid_size(len(services))
    parts = []
    for index, service in enumerate(services):
        column, row = index % columns, index // columns
        x = origin_x + column * (CARD_WIDTH + GAP)
        y = start_y + row * (CARD_HEIGHT + GAP)
        subtitle = service.type.split("@")[0].split(":")[0]
        if service.ports:
            subtitle += f" :{service.ports[0]}"
        parts.append(card(x, y, service.hostname, subtitle, service.kind))
    return "".join(parts), block_height(rows)


def render(translation: Translation) -> str:
    public = [s for s in translation.services if s.public]
    private = [s for s in translation.services if not s.public]

    public_columns, _ = grid_size(len(public))
    private_columns, _ = grid_size(len(private))
    columns = max(public_columns, private_columns, 1)
    width = PADDING * 2 + columns * CARD_WIDTH + (columns - 1) * GAP

    body = []
    y = PADDING

    if public:
        body.append(
            f'<text class="zone" x="{PADDING}" y="{y + 14}">INTERNET</text>'
            f'<line class="edge" x1="{PADDING}" y1="{y + 24}" x2="{width - PADDING}" y2="{y + 24}"/>'
        )
        y += INTERNET_HEIGHT
        markup, height = layout(public, y, PADDING)
        body.append(markup)
        y += height + GAP + 10

    if private:
        columns_used, rows = grid_size(len(private))
        inner_width = columns_used * CARD_WIDTH + (columns_used - 1) * GAP
        box_height = block_height(rows) + 46
        body.append(
            f'<rect class="private" x="{PADDING - 10}" y="{y}" '
            f'width="{inner_width + 20}" height="{box_height}" rx="10"/>'
            f'<text class="zone" x="{PADDING}" y="{y + 22}">PRIVATE NETWORK</text>'
        )
        markup, height = layout(private, y + 34, PADDING)
        body.append(markup)
        y += box_height

    height = y + PADDING
    return (
        f'<svg class="diagram" viewBox="0 0 {width} {height}" '
        f'width="100%" role="img" aria-label="Zerops architecture">'
        + "".join(body)
        + "</svg>"
    )

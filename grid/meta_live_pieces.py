"""Definición compartida, de solo lectura, de piezas vivas de una meta."""

TERMINAL_GRID_STATUSES = frozenset({"CLOSED", "ARCHIVED", "CANCELLED", "FAILED", "ERROR"})
OPEN_GRID_LEVEL_STATES = frozenset({"BUY_OPEN", "SELL_OPEN"})


def _field(row, name, default=None):
    if hasattr(row, "get"):
        return row.get(name, default)
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return default


def is_live_meta_grid(status, levels):
    """Incluye grids no terminales y terminales con inventario u órdenes vivas."""
    if str(status or "").upper() not in TERMINAL_GRID_STATUSES:
        return True
    return any(float(_field(level, "held_qty", 0) or 0) > 0
        or str(_field(level, "state", "")).upper() in OPEN_GRID_LEVEL_STATES
        for level in levels)


def live_meta_grid_links(core, meta_id):
    """Devuelve vínculos no liberados que aún representan exposición de la meta."""
    reader = getattr(core, "_owned_grids", None)
    if not callable(reader):
        return []
    links = reader(int(meta_id))
    db = getattr(core, "db", None)
    if db is None or not callable(getattr(db, "get_grid", None)) or not callable(
            getattr(db, "get_grid_levels", None)):
        return links
    live = []
    for link in links:
        grid = db.get_grid(int(link["grid_id"]))
        if grid is None:
            continue
        levels = db.get_grid_levels(int(grid["id"]))
        if is_live_meta_grid(grid.get("status"), levels):
            live.append(link)
    return live


def live_meta_grid_ids(core, meta_id):
    return {int(link["grid_id"]) for link in live_meta_grid_links(core, meta_id)}

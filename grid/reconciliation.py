"""Pure accounting helpers shared by account views and monitor reconciliation."""
from decimal import Decimal


RECONCILED_STATUSES = {"ACTIVE", "PAUSED", "CLOSING", "HOLDING"}


def expected_inventory_by_asset(db, grids):
    """Return registered base inventory and its contributing grid IDs."""
    quantities = {}
    grid_ids = {}
    for grid in grids:
        if str(grid.get("status", "")).upper() not in RECONCILED_STATUSES:
            continue
        asset = str(grid["symbol"]).removesuffix("USDT")
        levels = db.get_grid_levels(int(grid["id"]))
        qty = Decimal(str(grid.get("dust_qty") or 0)) + sum(
            (Decimal(str(level.get("held_qty") or 0)) for level in levels
             if str(level.get("state", "")).upper() != "DONE"), Decimal(0))
        quantities[asset] = quantities.get(asset, Decimal(0)) + qty
        grid_ids.setdefault(asset, []).append(int(grid["id"]))
    return quantities, {asset: sorted(ids) for asset, ids in grid_ids.items()}

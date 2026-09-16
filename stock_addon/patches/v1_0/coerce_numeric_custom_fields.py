# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Superseded by migrate_guards.coerce_numeric_custom_fields.

That runs as a before_migrate hook on EVERY migrate. This patch ran once
per site, so data written after it was never cleaned; it also left NULLs
alone, and a NULL is exactly what breaks a column becoming NOT NULL. Kept
only so sites that have not recorded it yet run the same, corrected logic.
"""

from stock_addon.stock_addon.migrate_guards import coerce_numeric_custom_fields


def execute():
    coerce_numeric_custom_fields()

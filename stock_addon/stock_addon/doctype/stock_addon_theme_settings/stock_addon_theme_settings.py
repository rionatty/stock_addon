# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Stock Addon Theme Settings (Single).

Colour overrides for the desk theme. See stock_addon/stock_addon/theme.py
for the field -> CSS variable contract and the shipped defaults.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from stock_addon.stock_addon.theme import DEFAULTS, FIELD_TO_VAR


class StockAddonThemeSettings(Document):
    def validate(self):
        # Colour fields accept free text, so a typo would otherwise be
        # written into every user's :root and silently kill the rule.
        for fieldname in FIELD_TO_VAR:
            value = (self.get(fieldname) or "").strip()
            if not value:
                continue
            if not _is_valid_colour(value):
                frappe.throw(
                    _("{0}: {1} is not a valid colour. Use a hex value such as #14395E.").format(
                        _(self.meta.get_label(fieldname)), value
                    )
                )
            self.set(fieldname, value)

    def on_update(self):
        # The palette rides on the session boot, which Frappe caches —
        # clear it so everyone picks the new colours up on next load.
        frappe.clear_cache()


def _is_valid_colour(value):
    if not value.startswith("#"):
        return False
    body = value[1:]
    if len(body) not in (3, 6):
        return False
    return all(c in "0123456789abcdefABCDEF" for c in body)


@frappe.whitelist()
def get_default_palette():
    """Shipped defaults — used by 'Reset to Defaults' on the form."""
    frappe.only_for(("System Manager", "Administrator"))
    return DEFAULTS

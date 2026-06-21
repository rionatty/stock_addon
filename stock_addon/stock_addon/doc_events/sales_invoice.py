# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Sales Invoice document events.

Validate hook (wired in hooks.py):
  - When is_return=1 and custom_return_reason='Expiry':
      • Halve each item's rate and recompute amount.
      • Force item warehouse to the Reparking Warehouse from Stock Addon Settings.
      • Throw a clear error if Reparking Warehouse is not configured.
  - No-op for all other cases.
"""

import frappe
from frappe import _
from frappe.utils import flt


def validate(doc, method=None):
    if not doc.is_return or not doc.custom_return_reason:
        return

    if doc.custom_return_reason == "Expiry":
        _apply_expiry_rules(doc)


def _apply_expiry_rules(doc):
    settings = frappe.get_cached_doc("Stock Addon Settings")

    if not settings.reparking_warehouse:
        frappe.throw(
            _(
                "Reparking Warehouse is not configured. "
                "Go to <b>Stock Addon Settings</b> and set the Expiry Return Warehouse "
                "before saving an Expiry credit note."
            )
        )

    reparking = settings.reparking_warehouse

    for item in doc.items:
        # Halve the rate only when the doc is new (first save).
        # On subsequent saves the rate is already the adjusted value.
        if doc.is_new():
            item.rate = flt(item.rate) / 2
            item.amount = flt(item.qty) * item.rate

        # Always enforce the reparking warehouse (idempotent).
        item.warehouse = reparking

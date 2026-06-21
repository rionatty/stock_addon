# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Sales Invoice document events.

Validate hook (wired in hooks.py):
  - Return Reason = Expiry:
      • Force item warehouse to Expiry Return Warehouse (Stock Addon Settings).
      • Halve each item's rate and recompute amount (first save only via is_new()).
      • Throw if Expiry Return Warehouse is not configured.
  - Return Reason = Damaged:
      • Force item warehouse to Damaged Return Warehouse (Stock Addon Settings).
      • Rates are NOT changed.
      • Throw if Damaged Return Warehouse is not configured.
  - All other cases: no-op.
"""

import frappe
from frappe import _
from frappe.utils import flt


def validate(doc, method=None):
    if not doc.is_return or not doc.custom_return_reason:
        return

    reason = doc.custom_return_reason

    if reason == "Expiry":
        _apply_expiry_rules(doc)
    elif reason == "Damaged":
        _apply_damaged_rules(doc)


def _apply_expiry_rules(doc):
    settings = frappe.get_cached_doc("Stock Addon Settings")

    if not settings.reparking_warehouse:
        frappe.throw(
            _(
                "Expiry Return Warehouse is not configured. "
                "Go to <b>Stock Addon Settings</b> and set the Expiry Return Warehouse "
                "before saving an Expiry credit note."
            )
        )

    warehouse = settings.reparking_warehouse

    for item in doc.items:
        # Halve the rate only on first save to avoid compounding.
        if doc.is_new():
            item.rate = flt(item.rate) / 2
            item.amount = flt(item.qty) * item.rate

        # Always enforce the configured warehouse (idempotent).
        item.warehouse = warehouse


def _apply_damaged_rules(doc):
    settings = frappe.get_cached_doc("Stock Addon Settings")

    if not settings.damaged_warehouse:
        frappe.throw(
            _(
                "Damaged Return Warehouse is not configured. "
                "Go to <b>Stock Addon Settings</b> and set the Damaged Return Warehouse "
                "before saving a Damaged credit note."
            )
        )

    warehouse = settings.damaged_warehouse

    for item in doc.items:
        # Rates remain unchanged for damaged returns.
        item.warehouse = warehouse

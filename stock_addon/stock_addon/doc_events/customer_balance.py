# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Customer balance + sales-person denormalization.

Two custom fields on Customer are kept in sync by these hooks:

  custom_primary_sales_person  — first Sales Team row's sales person.
      Allows the Customer list to filter / column by rep without a JOIN.
      Updated on every Customer save.

  custom_outstanding_balance   — SUM(outstanding_amount) from submitted
      Sales Invoices.  Updated on every Sales Invoice and Payment Entry
      submit/cancel so the list always shows a live balance.

Initial population (run once after installing):
    bench --site <site> execute \\
        stock_addon.stock_addon.doc_events.customer_balance.bulk_refresh_customer_balances
"""

import frappe


# ─── helpers ────────────────────────────────────────────────────────────────

def _outstanding_balance(customer):
    row = frappe.db.sql(
        "SELECT IFNULL(SUM(outstanding_amount), 0) "
        "FROM `tabSales Invoice` "
        "WHERE customer = %s AND docstatus = 1",
        customer,
    )
    return float((row[0][0] or 0) if row else 0)


def _set_balance(customer):
    if not customer:
        return
    try:
        frappe.db.set_value(
            "Customer", customer,
            "custom_outstanding_balance",
            _outstanding_balance(customer),
            update_modified=False,
        )
    except Exception:
        pass


# ─── doc-event handlers ─────────────────────────────────────────────────────

def sync_primary_sales_person(doc, method=None):
    """Customer on_update — stamp the first Sales Team row onto the header."""
    sp = (doc.sales_team or [None])[0]
    sp = sp.sales_person if sp else ""
    try:
        frappe.db.set_value(
            "Customer", doc.name,
            "custom_primary_sales_person", sp or "",
            update_modified=False,
        )
    except Exception:
        pass


def refresh_balance_on_invoice(doc, method=None):
    """Sales Invoice on_submit / on_cancel."""
    _set_balance(doc.get("customer"))


def refresh_balance_on_payment(doc, method=None):
    """Payment Entry on_submit / on_cancel."""
    if doc.get("party_type") == "Customer":
        _set_balance(doc.get("party"))


# ─── bulk refresh ────────────────────────────────────────────────────────────

@frappe.whitelist()
def bulk_refresh_customer_balances():
    """Recompute outstanding balance for every Customer.

    Run after first installing the custom field:
        bench --site <site> execute \\
            stock_addon.stock_addon.doc_events.customer_balance.bulk_refresh_customer_balances
    """
    customers = frappe.get_all("Customer", pluck="name")
    for name in customers:
        _set_balance(name)
    frappe.db.commit()
    return {"refreshed": len(customers)}

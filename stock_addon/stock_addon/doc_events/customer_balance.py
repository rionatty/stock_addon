# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Batch outstanding-balance lookup for the Customer list view.

No custom fields are stored — balance is computed on-demand from
submitted Sales Invoices and returned to the browser so the list view
JS can inject it as a badge on each row.
"""

import json

import frappe


@frappe.whitelist()
def get_customer_balances(customers):
    """Return {customer: outstanding_balance} for the given customer names.

    Called by public/js/customer_list.js after each list render.
    """
    if isinstance(customers, str):
        customers = json.loads(customers)
    if not customers:
        return {}

    rows = frappe.db.sql(
        """
        SELECT customer, SUM(outstanding_amount) AS balance
        FROM   `tabSales Invoice`
        WHERE  customer IN %(names)s
          AND  docstatus = 1
        GROUP  BY customer
        """,
        {"names": tuple(customers)},
        as_dict=True,
    )
    return {r.customer: float(r.balance or 0) for r in rows}

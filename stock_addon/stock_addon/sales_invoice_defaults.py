# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""The warehouse and cost center a Sales Invoice takes from its sales person.

Called by public/js/sales_invoice.js when the customer or the sales
person on a draft invoice changes.

The rule is the one the Sales Pro app already applies to every invoice it
sends: the invoice is served from the rep's own warehouse (Sales Person →
Mapped Warehouse) and posts to the cost center named after that warehouse
— each route has a cost center of the same name. Desk invoices follow the
same rule, so a route's sales land on one cost center whichever way they
were entered.

Nothing is returned that the invoice would then reject: a warehouse or
cost center of another company, a group, or a disabled one is left out,
and the form keeps whatever it already had for that field.
"""

import frappe


@frappe.whitelist()
def get_sales_person_defaults(sales_person, company):
    """{"warehouse": ..., "cost_center": ...}, with only the keys found."""
    frappe.has_permission("Sales Invoice", "write", throw=True)

    if not sales_person or not company:
        return {}

    warehouse = frappe.db.get_value("Sales Person", sales_person, "custom_mapped_warehouse")
    if not warehouse:
        return {}

    details = frappe.db.get_value(
        "Warehouse", warehouse, ["warehouse_name", "company", "is_group", "disabled"], as_dict=True
    )
    if not details or details.company != company:
        return {}

    defaults = {}
    if not details.is_group and not details.disabled:
        defaults["warehouse"] = warehouse

    cost_center = cost_center_for_warehouse(warehouse, details.warehouse_name, company)
    if cost_center:
        defaults["cost_center"] = cost_center

    return defaults


def cost_center_for_warehouse(warehouse, warehouse_name, company):
    usable = {"company": company, "is_group": 0, "disabled": 0}

    # The same full name — "Route 1 - AIL" for both — which is what the app sends.
    cost_center = frappe.db.get_value("Cost Center", dict(usable, name=warehouse))
    if cost_center:
        return cost_center

    # A numbered cost center ("101 - Route 1 - AIL") still carries the short name.
    return frappe.db.get_value("Cost Center", dict(usable, cost_center_name=warehouse_name))

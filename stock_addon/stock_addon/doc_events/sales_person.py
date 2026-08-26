# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Sales Person document events.

after_insert hook (wired in hooks.py):
  When a Sales Person is created, auto-provision the ledgers a van/route
  sales person needs, using the Sales Person's own code as the name:
    - a Cost Center  (cost_center_name  = <sales person name>)
    - a Warehouse    (warehouse_name    = <sales person name>)

  Both are created under the default company's root Cost Center / root
  Warehouse group. ERPNext appends the company abbreviation, so the final
  names are "<code> - <abbr>".

  The handler is:
    - idempotent  — skips creation if a matching record already exists;
    - group-safe  — does nothing for "Is Group" sales persons (e.g. the
      "Sales Team" parent node), which are folders, not real sales people.
"""

import frappe


def after_insert(doc, method=None):
    # Group nodes (e.g. the "Sales Team" parent) are folders, not real
    # sales people — they don't need their own ledgers.
    if getattr(doc, "is_group", 0):
        return

    # Bulk-synced sales persons (e.g. the SAP masters sync) opt out of
    # auto-provisioning to avoid creating a warehouse per synced rep.
    if doc.flags.get("skip_auto_provision"):
        return

    company = _default_company()
    if not company:
        frappe.log_error(
            "No default company configured; cannot auto-create Cost Center / "
            f"Warehouse for Sales Person {doc.name}.",
            "Sales Person auto-provision",
        )
        return

    code = (doc.sales_person_name or doc.name or "").strip()
    if not code:
        return

    _create_cost_center(code, company)
    _create_warehouse(code, company)


def _default_company():
    """Best-effort resolution of the company to hang the new records under."""
    company = frappe.defaults.get_user_default("Company")
    if not company:
        company = frappe.db.get_single_value("Global Defaults", "default_company")
    if not company:
        company = frappe.db.get_value("Company", {}, "name")
    return company


def _create_cost_center(code, company):
    abbr = frappe.get_cached_value("Company", company, "abbr")
    target_name = f"{code} - {abbr}"
    if frappe.db.exists("Cost Center", target_name):
        return

    parent = frappe.db.get_value(
        "Cost Center",
        {"company": company, "is_group": 1},
        "name",
        order_by="lft asc",  # leftmost group node = company root
    )

    cc = frappe.new_doc("Cost Center")
    cc.cost_center_name = code
    cc.company = company
    cc.is_group = 0
    if parent:
        cc.parent_cost_center = parent
    cc.flags.ignore_permissions = True
    cc.insert()


def _create_warehouse(code, company):
    abbr = frappe.get_cached_value("Company", company, "abbr")
    target_name = f"{code} - {abbr}"
    if frappe.db.exists("Warehouse", target_name):
        return

    parent = frappe.db.get_value(
        "Warehouse",
        {"company": company, "is_group": 1},
        "name",
        order_by="lft asc",  # leftmost group node = company root warehouse
    )

    wh = frappe.new_doc("Warehouse")
    wh.warehouse_name = code
    wh.company = company
    wh.is_group = 0
    if parent:
        wh.parent_warehouse = parent
    wh.flags.ignore_permissions = True
    wh.insert()

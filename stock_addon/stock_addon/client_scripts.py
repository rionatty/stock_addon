# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Install JS files as database-stored Client Scripts on every bench migrate.

Why: Frappe's doctype_js / doctype_list_js hooks serve plain asset files that
require ``bench build`` before they appear in the browser.  Storing the same
JS as Client Script records avoids the build step — the desk loads them
automatically from the database on first page render.

The canonical source stays in public/js/*.js so it's readable in the repo.
This module reads those files and upserts the corresponding Client Script
records.  The operation is idempotent: re-running migrate always brings the
database record in sync with the file.
"""

import os

import frappe


def _js_path(filename):
    app_path = frappe.get_app_path("stock_addon")
    return os.path.join(app_path, "public", "js", filename)


def _upsert(name, dt, view, filename):
    with open(_js_path(filename)) as f:
        script = f.read()

    if frappe.db.exists("Client Script", name):
        doc = frappe.get_doc("Client Script", name)
        if doc.script == script and doc.enabled:
            return  # already up-to-date
        doc.script  = script
        doc.enabled = 1
        doc.flags.ignore_permissions = True
        doc.save()
    else:
        frappe.get_doc({
            "doctype" : "Client Script",
            "name"    : name,
            "dt"      : dt,
            "view"    : view,
            "enabled" : 1,
            "script"  : script,
        }).insert(ignore_permissions=True)

    frappe.db.commit()


def install_journey_plan_form_script():
    """Push public/js/journey_plan.js into the database as a Form Client Script."""
    _upsert(
        name     = "journey-plan-form-stock-addon",
        dt       = "Journey Plan",
        view     = "Form",
        filename = "journey_plan.js",
    )


def remove_customer_list_script():
    """Delete the Customer list Client Script record if it still exists in the DB."""
    if frappe.db.exists("Client Script", "customer-list-stock-addon"):
        frappe.delete_doc("Client Script", "customer-list-stock-addon", ignore_permissions=True)
        frappe.db.commit()

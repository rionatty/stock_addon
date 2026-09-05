# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""SAP Integration Settings (Single).

Also exposes the whitelisted actions behind the buttons on this screen:
Test Connection, Sync Items, Sync Customers, Pull Van Transfers and
Retry Failed Pushes.
"""

import frappe
from frappe import _
from frappe.model.document import Document


# Auto-submit toggles and the doctype each one governs.
AUTO_SUBMIT_FIELDS = (
    ("Sales Order", "auto_submit_sales_orders"),
    ("Material Request", "auto_submit_material_requests"),
)


class SAPIntegrationSettings(Document):
    def validate(self):
        if self.enabled and not (self.service_layer_url and self.company_db and self.username):
            frappe.throw(_("To enable the SAP integration, fill in Service Layer URL, Company Database and Username."))
        if self.enabled and not self.go_live_date:
            from frappe.utils import nowdate
            self.go_live_date = nowdate()
        self._warn_about_workflows()

    def _warn_about_workflows(self):
        """A Workflow owns its document's submission.

        Auto-submit stands down when one is active, rather than fighting
        it. Say so while the setting is being changed — otherwise the
        toggle reads as switched on and nothing ever happens.
        """
        for doctype, fieldname in AUTO_SUBMIT_FIELDS:
            if (self.get(fieldname) or "Off") == "Off":
                continue
            if not frappe.db.exists("Workflow", {"document_type": doctype, "is_active": 1}):
                continue
            frappe.msgprint(
                _("{0} has an active Workflow, which decides for itself when a document is submitted. Auto-submit will stand down for it.").format(_(doctype)),
                indicator="orange",
                title=_("Auto-Submit Will Not Apply"),
            )


def _require_manager():
    frappe.only_for(("System Manager", "Administrator"))


@frappe.whitelist()
def test_connection():
    _require_manager()
    from stock_addon.stock_addon.sap_integration.connection import SAPClient
    return SAPClient().test()


@frappe.whitelist()
def sync_items_now():
    _require_manager()
    from stock_addon.stock_addon.sap_integration.masters import sync_items
    return sync_items()


@frappe.whitelist()
def sync_customers_now():
    _require_manager()
    from stock_addon.stock_addon.sap_integration.masters import sync_customers
    return sync_customers()


@frappe.whitelist()
def sync_taxes_now():
    _require_manager()
    from stock_addon.stock_addon.sap_integration.masters import sync_taxes
    return sync_taxes()


@frappe.whitelist()
def sync_currencies_now():
    _require_manager()
    from stock_addon.stock_addon.sap_integration.masters import sync_currencies
    return sync_currencies()


@frappe.whitelist()
def sync_pricing_now():
    _require_manager()
    from stock_addon.stock_addon.sap_integration.pricing import sync_pricing
    return sync_pricing()


@frappe.whitelist()
def discover_entities_now():
    _require_manager()
    from stock_addon.stock_addon.sap_integration.pricing import discover_entities
    return discover_entities()


@frappe.whitelist()
def assign_customer_codes_now():
    _require_manager()
    from stock_addon.stock_addon.sap_integration.masters import assign_customer_codes
    return assign_customer_codes()


@frappe.whitelist()
def pull_transfers_now():
    _require_manager()
    from stock_addon.stock_addon.sap_integration.stock_pull import pull_van_transfers
    return pull_van_transfers()


@frappe.whitelist()
def preview_transfers_now():
    _require_manager()
    from stock_addon.stock_addon.sap_integration.stock_pull import preview_transfers
    return preview_transfers()


@frappe.whitelist()
def check_integration_fields_now():
    _require_manager()
    from stock_addon.stock_addon.sap_integration.stock_pull import check_integration_fields
    return check_integration_fields()


@frappe.whitelist()
def pull_documents_now():
    _require_manager()
    from stock_addon.stock_addon.sap_integration.document_pull import pull_documents
    return pull_documents()


@frappe.whitelist()
def pull_from_docentry_now(docentry):
    _require_manager()
    from stock_addon.stock_addon.sap_integration.stock_pull import pull_from_docentry
    return pull_from_docentry(docentry)


@frappe.whitelist()
def push_pending_now():
    _require_manager()
    from stock_addon.stock_addon.sap_integration.transactions import push_pending
    return push_pending()


@frappe.whitelist()
def retry_failed_now():
    _require_manager()
    from stock_addon.stock_addon.sap_integration.transactions import retry_failed
    return retry_failed()

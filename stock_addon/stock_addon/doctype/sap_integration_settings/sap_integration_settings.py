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


class SAPIntegrationSettings(Document):
    def validate(self):
        if self.enabled and not (self.service_layer_url and self.company_db and self.username):
            frappe.throw(_("To enable the SAP integration, fill in Service Layer URL, Company Database and Username."))


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
def pull_transfers_now():
    _require_manager()
    from stock_addon.stock_addon.sap_integration.stock_pull import pull_van_transfers
    return pull_van_transfers()


@frappe.whitelist()
def retry_failed_now():
    _require_manager()
    from stock_addon.stock_addon.sap_integration.transactions import retry_failed
    return retry_failed()

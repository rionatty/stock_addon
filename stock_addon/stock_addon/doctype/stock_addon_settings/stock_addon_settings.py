# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

# Auto-submit toggles and the doctype each one governs.
AUTO_SUBMIT_FIELDS = (
    ("Sales Order", "auto_submit_sales_orders"),
    ("Material Request", "auto_submit_material_requests"),
)


class StockAddonSettings(Document):
    def validate(self):
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

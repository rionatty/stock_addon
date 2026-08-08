# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Quality Inspection document events.

Batch Status flow (custom_batch_status: Quarantine / Released /
On-hold / Rejected):

  - every new Batch starts as "Quarantine" (field default);
  - QC records their disposition on the Quality Inspection's own
    Batch Status field;
  - on submit (and on edits after submit — the field is
    allow_on_submit) the disposition is copied onto the linked Batch,
    so the batch's status always reflects the latest QC verdict.
"""

import frappe
from frappe import _


def sync_batch_status(doc, method=None):
    batch = doc.get("batch_no")
    status = doc.get("custom_batch_status")
    if not batch or not status:
        return
    if not frappe.db.exists("Batch", batch):
        return

    if frappe.db.get_value("Batch", batch, "custom_batch_status") != status:
        frappe.db.set_value("Batch", batch, "custom_batch_status", status)
        frappe.msgprint(
            _("Batch {0} status set to {1}").format(batch, frappe.bold(status)),
            alert=True,
            indicator="green" if status == "Released" else "orange",
        )

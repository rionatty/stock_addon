# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Work Order batch generation.

``generate_batch_for_work_order`` (whitelisted — called by the
"Generate Batch" button installed via public/js/work_order.js):

  - one batch per Work Order (re-clicking returns the existing one);
  - batch id format:  ``BAT:NNN MFG:dd.MM.yyyy <item_code>`` where NNN is
    a per-item running counter continued from the item's last batch;
  - manufacturing date = today, expiry = today + Item.shelf_life_in_days;
  - the generated id + dates are stamped on the Work Order
    (custom_batch_number / custom_batch_no / custom_mfg /
    custom_expiry_date).

The companion Stock Entry hook (doc_events/stock_entry.py →
``set_batch_from_work_order``) auto-fills this batch on the finished-item
row when the production receipt (Manufacture Stock Entry) is made.
"""

import frappe
from frappe import _
from frappe.utils import add_days, today


@frappe.whitelist()
def generate_batch_for_work_order(work_order, item):
    wo = frappe.db.get_value(
        "Work Order", work_order,
        ["custom_batch_number", "production_item", "docstatus"],
        as_dict=True,
    )
    if not wo:
        frappe.throw(_("Work Order {0} not found").format(work_order))

    # 1. One batch per Work Order — return the existing one if present.
    if wo.custom_batch_number:
        return wo.custom_batch_number

    item = item or wo.production_item

    # Batch numbers only make sense on batch-tracked items.
    if not frappe.db.get_value("Item", item, "has_batch_no"):
        frappe.throw(
            _("Item {0} is not batch-tracked. Enable 'Has Batch No' on the Item first.").format(item)
        )

    # 2. Continue the per-item counter from the last "BAT:NNN ..." batch.
    last_batch_list = frappe.db.sql(
        """
        SELECT name
        FROM `tabBatch`
        WHERE item = %s AND name LIKE 'BAT:%%'
        ORDER BY creation DESC
        """,
        (item,),
        as_dict=True,
    )

    new_num = 1
    for entry in last_batch_list:
        first_part = (entry.name.split() or [""])[0]  # expecting "BAT:004"
        if ":" not in first_part:
            continue
        prefix, num = first_part.split(":", 1)
        if prefix == "BAT" and num.isdigit():
            new_num = int(num) + 1
            break

    # 3. Compose the batch id: BAT:NNN MFG:dd.MM.yyyy <item_code>
    batch_code = f"BAT:{new_num:03d}"
    mfg_date = today()
    mfg_formatted = frappe.utils.formatdate(mfg_date, "dd.MM.yyyy")
    final_batch_no = f"{batch_code} MFG:{mfg_formatted} {item}"

    # 4. Expiry from the item's shelf life (if maintained).
    shelf_life = frappe.db.get_value("Item", item, "shelf_life_in_days") or 0
    expiry_date = add_days(mfg_date, shelf_life) if shelf_life else None

    # 5. Create the Batch.
    frappe.get_doc({
        "doctype": "Batch",
        "item": item,
        "batch_id": final_batch_no,
        "manufacturing_date": mfg_date,
        "expiry_date": expiry_date,
    }).insert(ignore_permissions=True)

    # 6. Stamp the Work Order.
    frappe.db.set_value("Work Order", work_order, {
        "custom_batch_number": final_batch_no,
        "custom_batch_no": final_batch_no,
        "custom_mfg": mfg_date,
        "custom_expiry_date": expiry_date,
    })

    return final_batch_no

# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""SAP B1 → ERPNext van stock pull.

Watches SAP Inventory Transfers (the warehouse team executing the van
request in SAP). Any new transfer whose target line-warehouse is mapped
as a VAN warehouse is mirrored into ERPNext as a submitted Stock Entry,
so the Sales Pro app immediately sees the van's stock.

Modes (SAP Integration Settings → Receipt Mode):
  - "Material Receipt"  (default): stock appears directly in the van
    warehouse — works regardless of ERPNext stock levels elsewhere.
  - "Material Transfer": moves stock from the mapped source warehouse —
    keeps ERPNext's main-warehouse balance in step, but requires that
    stock to exist in ERPNext.

De-duplication: each pulled transfer stamps its SAP DocEntry on the
Stock Entry (custom_sap_docentry); the high-water mark is kept in
Settings.last_transfer_docentry.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, nowdate

from stock_addon.stock_addon.sap_integration.connection import (
    SAPClient,
    get_settings,
    integration_enabled,
    log_sap,
)


def _ensure_batch(item_code, batch_number):
    """Return the ERPNext Batch name for a SAP batch number, creating it
    if needed."""
    existing = frappe.db.get_value("Batch", {"batch_id": batch_number, "item": item_code}, "name")
    if existing:
        return existing
    batch = frappe.get_doc({
        "doctype": "Batch",
        "item": item_code,
        "batch_id": batch_number,
    })
    batch.insert(ignore_permissions=True)
    return batch.name


def _make_stock_entry(transfer, van_lines, code_to_erp, settings):
    mode = settings.receipt_mode or "Material Receipt"
    se = frappe.new_doc("Stock Entry")
    se.stock_entry_type = mode
    se.purpose = mode
    se.posting_date = str(transfer.get("DocDate") or nowdate())[:10]
    se.set_posting_time = 1
    se.custom_sap_docentry = str(transfer["DocEntry"])

    for line in van_lines:
        item_code = line.get("ItemCode")
        if not frappe.db.exists("Item", item_code):
            raise frappe.ValidationError(
                f"Item {item_code} from SAP transfer {transfer['DocNum']} does not "
                "exist in ERPNext — run 'Sync Items' first."
            )
        target_wh = code_to_erp[line["WarehouseCode"]]
        source_wh = None
        if mode == "Material Transfer":
            source_wh = code_to_erp.get(line.get("FromWarehouseCode")) or settings.default_source_warehouse
            if not source_wh:
                raise frappe.ValidationError(
                    f"SAP transfer {transfer['DocNum']}: no ERPNext source warehouse "
                    f"mapped for SAP warehouse '{line.get('FromWarehouseCode')}' and no "
                    "Default Source Warehouse set in SAP Integration Settings."
                )

        stock_uom = frappe.db.get_value("Item", item_code, "stock_uom")
        batches = line.get("BatchNumbers") or []
        # one row per batch (or a single row when the item is unbatched)
        portions = (
            [(b.get("BatchNumber"), flt(b.get("Quantity"))) for b in batches]
            or [(None, flt(line.get("Quantity")))]
        )
        for batch_number, qty in portions:
            if not qty:
                continue
            row = se.append("items", {
                "item_code": item_code,
                "qty": qty,
                "transfer_qty": qty,
                "uom": stock_uom,
                "stock_uom": stock_uom,
                "conversion_factor": 1,
                "t_warehouse": target_wh,
                "s_warehouse": source_wh,
                "allow_zero_valuation_rate": 1,
            })
            if batch_number:
                row.batch_no = _ensure_batch(item_code, batch_number)
                if row.meta.get_field("use_serial_batch_fields"):
                    row.use_serial_batch_fields = 1

    se.flags.ignore_permissions = True
    se.insert(ignore_permissions=True)
    se.submit()
    return se


def pull_van_transfers(triggered_by="manual"):
    """Pull new SAP Inventory Transfers into mapped van warehouses.
    Returns a human-readable summary. Every manual run writes a summary
    row to the SAP Integration Log (scheduled runs only log when they
    actually pulled or failed something, to keep the log readable)."""
    if not integration_enabled("pull_van_transfers"):
        return _("Van transfer pull is disabled in SAP Integration Settings.")

    settings = get_settings()
    mappings = settings.warehouse_mappings or []
    van_codes = {m.sap_warehouse_code for m in mappings if cint(m.is_van_warehouse)}
    code_to_erp = {m.sap_warehouse_code: m.erpnext_warehouse for m in mappings}
    if not van_codes:
        return _("No van warehouses mapped — tick 'Is Van Warehouse' on at least one mapping row.")

    # single-flight lock: the 5-minute cron and the manual button must not
    # process the same window concurrently
    if frappe.cache().get_value("sap_van_pull_running"):
        return _("A van stock pull is already running — try again in a minute.")
    frappe.cache().set_value("sap_van_pull_running", 1, expires_in_sec=240)

    try:
        client = SAPClient(settings)
        last = cint(settings.last_transfer_docentry)

        if not last:
            # First run after enabling: baseline to the newest SAP transfer so
            # we never replay the company's entire historical transfer log.
            newest = client.get("InventoryTransfers", params={
                "$select": "DocEntry", "$orderby": "DocEntry desc", "$top": 1,
            }).get("value") or []
            baseline = cint(newest[0]["DocEntry"]) if newest else 0
            frappe.db.set_single_value("SAP Integration Settings", "last_transfer_docentry", baseline)
            frappe.db.commit()
            message = _(
                "Baseline set at SAP transfer DocEntry {0} — only transfers created "
                "from now on will be pulled."
            ).format(baseline)
            log_sap("Pull", "Success", "InventoryTransfers", message=message)
            return message

        transfers = client.get_all("InventoryTransfers", params={
            "$filter": f"DocEntry gt {last}",
            "$orderby": "DocEntry asc",
            "$select": "DocEntry,DocNum,DocDate,FromWarehouse,ToWarehouse,Comments,StockTransferLines",
        })

        pulled = skipped = failed = 0
        new_mark, advance = last, True
        for transfer in transfers:
            entry = cint(transfer.get("DocEntry"))
            van_lines = [
                l for l in (transfer.get("StockTransferLines") or [])
                if l.get("WarehouseCode") in van_codes
            ]
            if not van_lines or frappe.db.exists(
                "Stock Entry", {"custom_sap_docentry": str(entry)}
            ):
                skipped += 1
                if advance:
                    new_mark = entry
                continue
            try:
                se = _make_stock_entry(transfer, van_lines, code_to_erp, settings)
                log_sap("Pull", "Success", "InventoryTransfers", "Stock Entry", se.name,
                        entry, f"SAP transfer #{transfer.get('DocNum')} → {se.name}")
                pulled += 1
                frappe.db.commit()
                if advance:
                    new_mark = entry
            except Exception:
                frappe.db.rollback()
                log_sap("Pull", "Failed", "InventoryTransfers", "Stock Entry", None,
                        entry, frappe.get_traceback()[-2000:])
                failed += 1
                frappe.db.commit()
                # stop advancing the high-water mark so this transfer is
                # retried on the next run (already-pulled ones are deduped
                # by their custom_sap_docentry stamp)
                advance = False

        if new_mark > last:
            frappe.db.set_single_value("SAP Integration Settings", "last_transfer_docentry", new_mark)
            frappe.db.commit()

        summary = _("Van stock pull: {0} pulled, {1} skipped, {2} failed (checked {3} SAP transfers)").format(
            pulled, skipped, failed, len(transfers)
        )
        # manual runs always leave a visible trace; the 5-minute cron only
        # logs when something actually happened
        if triggered_by == "manual" or pulled or failed:
            log_sap("Pull", "Failed" if failed else "Success",
                    "InventoryTransfers", message=summary)
        return summary
    finally:
        frappe.cache().delete_value("sap_van_pull_running")


def scheduled_pull():
    """Every-5-minutes scheduler job."""
    if not integration_enabled("pull_van_transfers"):
        return
    try:
        pull_van_transfers(triggered_by="scheduler")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "SAP van transfer pull failed")
        log_sap("Pull", "Failed", "InventoryTransfers", message=frappe.get_traceback()[-2000:])

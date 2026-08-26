# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""ERPNext → SAP B1 transaction push.

Wired on on_submit (hooks.py), all guarded by SAP Integration Settings:

  Sales Invoice            → /Invoices        (is_return → /CreditNotes)
  Material Request (MT)    → /InventoryTransferRequests
  Payment Entry (Receive)  → /IncomingPayments
  Field Expense (Posted)   → /JournalEntries  (called from make_journal_entry)

Design rules:
  - a push failure NEVER blocks the ERPNext submission — every on_* entry
    point is wrapped in a broad try/except: the doc is stamped
    custom_sap_sync_status = Failed and the error lands in the SAP
    Integration Log;
  - every doc carries custom_sap_docentry / custom_sap_docnum once synced;
  - "Retry Failed Pushes" on the settings screen re-runs anything Failed
    (submitted/posted docs only, with a NumAtCard duplicate check for
    invoices so a timed-out push is adopted, not double-posted).
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from stock_addon.stock_addon.sap_integration.connection import (
    SAPClient,
    SAPError,
    get_settings,
    integration_enabled,
    log_sap,
)


# ------------------------------------------------------------- helpers
def _wh_map():
    settings = get_settings()
    return {m.erpnext_warehouse: m.sap_warehouse_code for m in (settings.warehouse_mappings or [])}


def _sap_warehouse(erp_warehouse, required=False, context=""):
    code = _wh_map().get(erp_warehouse)
    if required and not code:
        raise SAPError(
            f"No SAP warehouse code mapped for ERPNext warehouse '{erp_warehouse}' "
            f"({context}). Add it in SAP Integration Settings → Warehouse Mapping."
        )
    return code


def _cardcode(customer):
    code = frappe.db.get_value("Customer", customer, "custom_sap_cardcode")
    if not code:
        raise SAPError(
            f"Customer '{customer}' has no SAP CardCode. "
            "Run 'Sync Customers' in SAP Integration Settings, or set "
            "custom_sap_cardcode on the customer."
        )
    return code


def _account_code(account):
    number = frappe.db.get_value("Account", account, "account_number")
    if not number:
        raise SAPError(
            f"Account '{account}' has no Account Number. Set the SAP G/L code "
            "as the Account Number on the ERPNext account."
        )
    return number


def _stamp(doc, status, docentry=None, docnum=None):
    values = {"custom_sap_sync_status": status}
    if docentry is not None:
        values["custom_sap_docentry"] = str(docentry)
    if docnum is not None:
        values["custom_sap_docnum"] = str(docnum)
    doc.db_set(values, update_modified=False, notify=True)


def _batch_numbers_for_row(item):
    """BatchNumbers for an invoice line — handles both the plain batch_no
    field and the v15/v16 Serial and Batch Bundle."""
    qty = abs(flt(item.qty))
    if item.get("batch_no"):
        batch_id = frappe.db.get_value("Batch", item.batch_no, "batch_id") or item.batch_no
        return [{"BatchNumber": batch_id, "Quantity": qty}]

    bundle = item.get("serial_and_batch_bundle")
    if bundle:
        entries = frappe.get_all(
            "Serial and Batch Entry",
            filters={"parent": bundle, "parenttype": "Serial and Batch Bundle"},
            fields=["batch_no", "qty"],
        )
        out = []
        for entry in entries:
            if not entry.batch_no:
                continue
            batch_id = frappe.db.get_value("Batch", entry.batch_no, "batch_id") or entry.batch_no
            out.append({"BatchNumber": batch_id, "Quantity": abs(flt(entry.qty))})
        return out
    return []


def _push(doc, endpoint, payload, direction_label):
    """POST one document; stamp + log both outcomes. Returns True on success."""
    try:
        result = SAPClient().post(endpoint, payload)
        docentry, docnum = result.get("DocEntry"), result.get("DocNum")
        _stamp(doc, "Synced", docentry, docnum)
        log_sap("Push", "Success", endpoint, doc.doctype, doc.name, docentry,
                f"{direction_label} pushed to SAP (DocNum {docnum})")
        frappe.msgprint(
            _("{0} pushed to SAP as {1} #{2}").format(direction_label, endpoint, docnum or docentry),
            alert=True, indicator="green",
        )
        return True
    except Exception as e:
        _stamp(doc, "Failed")
        log_sap("Push", "Failed", endpoint, doc.doctype, doc.name, message=str(e))
        frappe.msgprint(
            _("SAP push failed for {0} — saved locally, will retry. ({1})").format(
                doc.name, str(e)[:200]),
            alert=True, indicator="orange",
        )
        return False


def _guarded(pusher, doc, endpoint):
    """Run a push handler so that NOTHING (including payload-build errors
    like a missing CardCode or warehouse mapping) can abort the
    surrounding ERPNext submission."""
    try:
        pusher(doc)
    except Exception as e:
        try:
            _stamp(doc, "Failed")
        except Exception:
            pass
        log_sap("Push", "Failed", endpoint, doc.doctype, doc.name, message=str(e))
        frappe.msgprint(
            _("SAP push failed for {0} — saved locally, will retry. ({1})").format(
                doc.name, str(e)[:200]),
            alert=True, indicator="orange",
        )


# ------------------------------------------------------- sales invoice
def push_sales_invoice_doc(doc):
    endpoint = "CreditNotes" if cint(doc.is_return) else "Invoices"
    lines = []
    for item in doc.items:
        line = {
            "ItemCode": item.item_code,
            "Quantity": abs(flt(item.qty)),
            "UnitPrice": flt(item.rate),
        }
        wh = _sap_warehouse(item.warehouse)
        if wh:
            line["WarehouseCode"] = wh
        batches = _batch_numbers_for_row(item)
        if batches:
            line["BatchNumbers"] = batches
        lines.append(line)

    payload = {
        "CardCode": _cardcode(doc.customer),
        "DocDate": str(doc.posting_date),
        "DocDueDate": str(doc.get("due_date") or doc.posting_date),
        "NumAtCard": doc.name,
        "Comments": f"ERPNext {doc.name}"[:254],
        "DocumentLines": lines,
    }
    label = _("Credit Note") if cint(doc.is_return) else _("Sales Invoice")
    return _push(doc, endpoint, payload, label)


def on_sales_invoice_submit(doc, method=None):
    flag = "push_credit_notes" if cint(doc.is_return) else "push_sales_invoices"
    if not integration_enabled(flag):
        return
    _guarded(push_sales_invoice_doc, doc,
             "CreditNotes" if cint(doc.is_return) else "Invoices")


# ---------------------------------------------------- transfer request
def push_material_request_doc(doc):
    lines = []
    for item in doc.items:
        from_wh = item.get("from_warehouse") or doc.get("set_from_warehouse")
        to_wh = item.get("warehouse") or doc.get("set_warehouse")
        lines.append({
            "ItemCode": item.item_code,
            "Quantity": flt(item.get("stock_qty") or item.qty),
            "FromWarehouseCode": _sap_warehouse(from_wh, required=True, context="source"),
            # line-level target — SAP uses WarehouseCode as the TO warehouse
            "WarehouseCode": _sap_warehouse(to_wh, required=True, context="target/van"),
        })

    payload = {
        "DocDate": str(doc.transaction_date),
        "DueDate": str(doc.schedule_date or doc.transaction_date),
        "Comments": f"ERPNext {doc.name} — {doc.get('custom_narration') or 'van stock request'}"[:250],
        "StockTransferLines": lines,
    }
    return _push(doc, "InventoryTransferRequests", payload, _("Stock Transfer Request"))


def on_material_request_submit(doc, method=None):
    if not integration_enabled("push_transfer_requests"):
        return
    if doc.material_request_type != "Material Transfer":
        return
    _guarded(push_material_request_doc, doc, "InventoryTransferRequests")


# --------------------------------------------------- incoming payments
def push_payment_entry_doc(doc):
    settings = get_settings()
    if not settings.sap_cash_account:
        raise SAPError(
            "SAP Cash G/L Account Code is not set in SAP Integration Settings."
        )

    payload = {
        "CardCode": _cardcode(doc.party),
        "DocDate": str(doc.posting_date),
        "CashAccount": settings.sap_cash_account,
        "CashSum": flt(doc.paid_amount),
        "Remarks": f"ERPNext {doc.name}"[:250],
    }

    # Apply against synced A/R invoices. Credit notes (is_return) and
    # invoices that never reached SAP are left out; if ANY reference is
    # unmappable the whole payment goes on account instead — a partial
    # PaymentInvoices list would silently mis-apply the difference.
    invoices, unmappable = [], False
    for ref in doc.get("references", []):
        if ref.reference_doctype != "Sales Invoice":
            unmappable = True
            continue
        docentry, is_return = frappe.db.get_value(
            "Sales Invoice", ref.reference_name, ["custom_sap_docentry", "is_return"]
        ) or (None, 0)
        if cint(is_return):
            unmappable = True
            continue
        if docentry:
            invoices.append({
                "DocEntry": cint(docentry),
                "SumApplied": flt(ref.allocated_amount),
                "InvoiceType": "it_Invoice",
            })
        else:
            unmappable = True
    if invoices and not unmappable:
        payload["PaymentInvoices"] = invoices

    return _push(doc, "IncomingPayments", payload, _("Incoming Payment"))


def on_payment_entry_submit(doc, method=None):
    if not integration_enabled("push_incoming_payments"):
        return
    if doc.payment_type != "Receive" or doc.party_type != "Customer":
        return
    _guarded(push_payment_entry_doc, doc, "IncomingPayments")


# ------------------------------------------------------ expense as JV
def push_field_expense_doc(doc):
    # SAP B1 Memo/LineMemo columns are nvarchar(50) — hard limit.
    lines, total = [], 0.0
    for row in doc.expense_items:
        if flt(row.amount) <= 0:  # mirror make_journal_entry's > 0 filter
            continue
        lines.append({
            "AccountCode": _account_code(row.expense_account),
            "Debit": flt(row.amount),
            "Credit": 0,
            "LineMemo": (row.description or row.expense_type or doc.name)[:50],
        })
        total += flt(row.amount)
    if not lines:
        raise SAPError(f"Field Expense {doc.name} has no positive expense lines to post.")
    lines.append({
        "AccountCode": _account_code(doc.paid_from_account),
        "Debit": 0,
        "Credit": total,
        "LineMemo": f"Field Expense {doc.name}"[:50],
    })

    payload = {
        "ReferenceDate": str(doc.expense_date),
        "Memo": f"ERPNext FE {doc.name}"[:50],
        "JournalEntryLines": lines,
    }
    return _push(doc, "JournalEntries", payload, _("Expense Journal"))


def on_field_expense_posted(doc):
    """Called from field_expense.make_journal_entry after posting."""
    if not integration_enabled("push_expense_journals"):
        return
    _guarded(push_field_expense_doc, doc, "JournalEntries")


# --------------------------------------------------------------- retry
PUSHERS = {
    # doctype: (pusher, extra filters — never re-push cancelled/unposted docs)
    "Sales Invoice": (push_sales_invoice_doc, {"docstatus": 1}),
    "Material Request": (push_material_request_doc, {"docstatus": 1}),
    "Payment Entry": (push_payment_entry_doc, {"docstatus": 1}),
    "Field Expense": (push_field_expense_doc, {"status": "Posted"}),
}


def _adopt_existing_invoice(doc):
    """A timed-out push may have committed in SAP. Before re-POSTing an
    invoice/credit note, look it up by NumAtCard and adopt it if found.
    Returns True when adopted."""
    endpoint = "CreditNotes" if cint(doc.is_return) else "Invoices"
    try:
        rows = SAPClient().get(endpoint, params={
            "$select": "DocEntry,DocNum",
            "$filter": f"NumAtCard eq '{doc.name}'",
            "$top": 1,
        }).get("value") or []
    except Exception:
        return False
    if not rows:
        return False
    _stamp(doc, "Synced", rows[0].get("DocEntry"), rows[0].get("DocNum"))
    log_sap("Push", "Success", endpoint, doc.doctype, doc.name, rows[0].get("DocEntry"),
            "Adopted existing SAP document found by NumAtCard (earlier push had timed out)")
    return True


def retry_failed():
    """Re-push every document whose last push failed. Returns a summary."""
    results = []
    for doctype, (pusher, extra_filters) in PUSHERS.items():
        filters = {"custom_sap_sync_status": "Failed"}
        filters.update(extra_filters)
        names = frappe.get_all(doctype, filters=filters, pluck="name", limit=50)
        ok = 0
        for name in names:
            try:
                doc = frappe.get_doc(doctype, name)
                if doctype == "Sales Invoice" and _adopt_existing_invoice(doc):
                    ok += 1
                    continue
                if pusher(doc):
                    ok += 1
            except Exception as e:
                log_sap("Push", "Failed", "retry", doctype, name, message=str(e))
        if names:
            results.append(f"{doctype}: {ok}/{len(names)} pushed")
    frappe.db.commit()
    return ", ".join(results) or _("Nothing to retry — no failed pushes found.")

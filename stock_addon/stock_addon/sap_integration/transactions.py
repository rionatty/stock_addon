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


def _account_code(account, required=True):
    """The SAP G/L code for an ERPNext account.

    "SAP G/L Account" on the account wins; the ERPNext Account Number is
    the fallback for sites where the two charts already use the same
    codes. Everything posted to SAP as a ledger line — incoming payments,
    banking, expense journals — resolves its account through here.
    """
    if not account:
        if required:
            raise SAPError("No account given to resolve a SAP G/L code for.")
        return None

    values = frappe.db.get_value(
        "Account", account, ["custom_sap_gl_account", "account_number"], as_dict=True
    ) or {}
    code = (values.get("custom_sap_gl_account") or values.get("account_number") or "").strip()
    if not code and required:
        raise SAPError(
            f"Account '{account}' has no SAP G/L code. Set 'SAP G/L Account' on "
            "the ERPNext account (or fill its Account Number to reuse that)."
        )
    return code or None


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


def _is_tax_inclusive(doc):
    """Is the ERPNext rate the price the customer pays (VAT included)?

    SAP always computes VAT itself from the line's tax code, so sending a
    gross price as UnitPrice makes SAP add the tax a second time — the
    document then totals more than the ERPNext invoice.

    Read from the document where possible:
      - tax rows flagged "included in print rate" -> rate is gross
      - ordinary tax rows on top                  -> rate is net
      - no tax rows at all                        -> ambiguous, so the
        "Prices Include Tax" setting decides (default on: retail/van
        prices quote VAT-inclusive, and SAP still applies its own
        default tax code to the line).
    """
    tax_rows = doc.get("taxes") or []
    if tax_rows:
        return any(cint(t.get("included_in_print_rate")) for t in tax_rows)
    return cint(get_settings().get("prices_include_tax"))


def _vat_group_for(doc):
    """SAP VatGroup for the invoice's ERPNext tax template, via the
    Settings → Tax Mapping table. None when unmapped (SAP then applies
    the item/BP default tax)."""
    template = doc.get("taxes_and_charges")
    if not template:
        return None
    for m in (get_settings().get("tax_mappings") or []):
        if m.erpnext_tax_template == template:
            return m.sap_tax_code
    return None


# ------------------------------------------------------- sales invoice
def push_sales_invoice_doc(doc):
    endpoint = "CreditNotes" if cint(doc.is_return) else "Invoices"
    vat_group = _vat_group_for(doc)
    tax_inclusive = _is_tax_inclusive(doc)
    lines = []
    for item in doc.items:
        line = {
            "ItemCode": item.item_code,
            "Quantity": abs(flt(item.qty)),
        }
        if tax_inclusive:
            # PriceAfterVAT is SAP's gross unit price: it back-computes the
            # net and the VAT, so the SAP document totals exactly what the
            # ERPNext invoice does.
            line["PriceAfterVAT"] = flt(item.rate)
        else:
            # net price — SAP adds the tax on top, mirroring ERPNext
            line["UnitPrice"] = flt(item.net_rate or item.rate)
        if vat_group:
            line["VatGroup"] = vat_group
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

    # The account the money actually landed in — each route/van has its
    # own cash account and banking has its own bank account, so the SAP
    # posting must follow the ERPNext account rather than one global
    # code. The setting is only the fallback for accounts not yet mapped.
    account_code = _account_code(doc.get("paid_to"), required=False) or settings.sap_cash_account
    if not account_code:
        raise SAPError(
            f"Account '{doc.get('paid_to')}' has no SAP G/L code, and no fallback "
            "is set in SAP Integration Settings (SAP Cash G/L Account Code)."
        )

    # SAP splits the payment by instrument: cash goes in CashAccount,
    # anything through a bank goes in TransferAccount with the date and
    # amount, or the document simply will not post.
    is_bank = frappe.db.get_value("Account", doc.get("paid_to"), "account_type") == "Bank"
    payload = {
        "CardCode": _cardcode(doc.party),
        "DocDate": str(doc.posting_date),
        "Remarks": f"ERPNext {doc.name}"[:250],
    }
    if is_bank:
        payload["TransferAccount"] = account_code
        payload["TransferSum"] = flt(doc.paid_amount)
        payload["TransferDate"] = str(doc.get("reference_date") or doc.posting_date)
        if doc.get("reference_no"):
            payload["TransferReference"] = str(doc.reference_no)[:50]
    else:
        payload["CashAccount"] = account_code
        payload["CashSum"] = flt(doc.paid_amount)

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
    # doctype: (pusher, extra filters — never re-push cancelled/unposted
    # docs, and only docs of the kinds the on_submit hooks would push)
    "Sales Invoice": (push_sales_invoice_doc, {"docstatus": 1}),
    "Material Request": (push_material_request_doc,
                         {"docstatus": 1, "material_request_type": "Material Transfer"}),
    "Payment Entry": (push_payment_entry_doc,
                      {"docstatus": 1, "payment_type": "Receive", "party_type": "Customer"}),
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


# which settings toggle authorizes pushing each doctype
PUSH_FLAGS = {
    "Material Request": "push_transfer_requests",
    "Payment Entry": "push_incoming_payments",
    "Field Expense": "push_expense_journals",
}


def push_pending(limit=100):
    """Push everything not yet in SAP: docs whose sync status is Failed,
    plus never-attempted docs created on/after the go-live date (so a
    brownfield site's history is never mass-posted). Respects the master
    switch and every per-flow toggle, exactly like the on_submit hooks."""
    if not integration_enabled():
        return _("SAP integration is disabled — enable it in SAP Integration Settings first.")

    settings = get_settings()
    go_live = settings.get("go_live_date")

    results = []
    for doctype, (pusher, extra_filters) in PUSHERS.items():
        if doctype in PUSH_FLAGS and not cint(settings.get(PUSH_FLAGS[doctype])):
            continue
        if doctype == "Sales Invoice" and not (
            cint(settings.get("push_sales_invoices")) or cint(settings.get("push_credit_notes"))
        ):
            continue

        names = _pending_names(doctype, extra_filters, go_live, limit)

        ok = 0
        for name in names:
            try:
                doc = frappe.get_doc(doctype, name)
                if doctype == "Sales Invoice":
                    flag = "push_credit_notes" if cint(doc.is_return) else "push_sales_invoices"
                    if not cint(settings.get(flag)):
                        continue
                    if _adopt_existing_invoice(doc):
                        ok += 1
                        continue
                if pusher(doc):
                    ok += 1
            except Exception as e:
                log_sap("Push", "Failed", "push_pending", doctype, name, message=str(e))
        if names:
            results.append(f"{doctype}: {ok}/{len(names)} pushed")
    frappe.db.commit()
    return ", ".join(results) or _("Nothing pending — everything already in SAP.")


def _pending_names(doctype, extra_filters, go_live, limit):
    """Failed docs (any age) + never-attempted docs from go-live onward."""
    failed = frappe.get_all(
        doctype,
        filters=dict(extra_filters, custom_sap_sync_status="Failed"),
        pluck="name", limit=limit,
    )
    unsent = []
    if go_live:
        unsent = frappe.get_all(
            doctype,
            filters=dict(extra_filters, creation=(">=", str(go_live))),
            or_filters=[
                ["custom_sap_sync_status", "is", "not set"],
                ["custom_sap_sync_status", "=", ""],
            ],
            pluck="name", limit=limit,
        )
    seen, out = set(), []
    for name in failed + unsent:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out[:limit]


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

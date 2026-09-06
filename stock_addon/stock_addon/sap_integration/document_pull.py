# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""SAP B1 → ERPNext document pull: invoices and customer payments.

Order selling runs the opposite way round from van selling. The order is
pushed to SAP, SAP raises the delivery note against it, and SAP raises
the invoice and takes the money. ERPNext still has to show what each
customer owes and what they have paid, so those two documents come back:

    SAP /Invoices          -> Sales Invoice  (submitted)
    SAP /IncomingPayments  -> Payment Entry  (submitted, and allocated
                              against the invoices SAP says it paid)

Nothing that started here is pulled back. An invoice pushed from ERPNext
carries its ERPNext name in NumAtCard and its SAP DocEntry on the ERPNext
document; either is enough to recognise a round trip. Every pulled
document is stamped with its DocEntry, so the next run skips it — and
transactions.came_from_sap() stops that stamp from being pushed back,
which would otherwise close the loop into an endless pair of duplicates.

Selection deliberately scans SAP's most recent documents rather than
filtering on a date server-side: OData date-literal syntax differs
between /b1s/v1 and /b1s/v2 and a wrong guess is a 400, while the scan
behaves the same on both. Documents older than the Pull Documents From
date are dropped here instead.

Stock is NOT moved by a pulled invoice unless asked for. In this flow
SAP's delivery note is what takes the goods out; mirroring both the
delivery note and the invoice would relieve the same stock twice.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from stock_addon.stock_addon.sap_integration.connection import (
    SAPClient,
    get_settings,
    integration_enabled,
    log_sap,
    pushed_from_here,
    single_flight,
)

DEFAULT_SCAN = 100


# ------------------------------------------------------------- lookups
def _since(settings):
    """Oldest document date worth importing.

    Falls back to the go-live date so switching this on does not drag in
    years of SAP history on the first run.
    """
    value = (settings.get("document_pull_from_date")
             or settings.get("go_live_date")
             or nowdate())
    return getdate(value)


def _scan_limit(settings):
    return cint(settings.get("pull_scan_limit")) or DEFAULT_SCAN


def _origin_reference(text):
    """The ERPNext document name a SAP remark was stamped with.

    The push writes "ERPNext <name>" — anything else is a remark somebody
    typed in SAP and names nothing here.
    """
    words = (text or "").strip().split()
    if len(words) >= 2 and words[0] == "ERPNext":
        return words[1].strip()
    return None


def _item_for(sap_code):
    """One definition of "which ERPNext item is this", shared with the
    pricing sync — see masters.resolve_item."""
    from stock_addon.stock_addon.sap_integration.masters import resolve_item
    return resolve_item(sap_code)


def _item_group_filter_note():
    """Why an item might never have arrived, in the words of the setting
    that decided it."""
    groups = (get_settings().get("sap_item_group_codes") or "").strip()
    if groups:
        return (f"'Sync Items' only brings SAP items that are sales items AND in item "
                f"group(s) {groups} — an item outside that filter never arrives. Widen "
                "'SAP Item Group Codes' in SAP Integration Settings, or create the item here")
    return ("'Sync Items' only brings SAP items flagged as sales items — one that is not "
            "will never arrive. Flag it in SAP, or create the item here")


def _customer_for(card_code):
    code = (card_code or "").strip()
    if not code:
        return None
    return (frappe.db.get_value("Customer", {"custom_sap_cardcode": code}, "name")
            or frappe.db.get_value("Customer", code, "name"))


def _warehouse_for(sap_code):
    """ERPNext warehouse for a SAP warehouse code, from the mapping table."""
    code = (sap_code or "").strip()
    if not code:
        return None
    for row in get_settings().get("warehouse_mappings") or []:
        if (row.sap_warehouse_code or "").strip() == code:
            return row.erpnext_warehouse
    return None


def _account_for(sap_code):
    """ERPNext account for a SAP G/L code — the reverse of _account_code."""
    code = (sap_code or "").strip()
    if not code:
        return None
    return (frappe.db.get_value("Account", {"custom_sap_gl_account": code}, "name")
            or frappe.db.get_value("Account", {"account_number": code}, "name"))


def _tax_template_for(vat_group):
    code = (vat_group or "").strip()
    if not code:
        return None
    for row in get_settings().get("tax_mappings") or []:
        if (row.sap_tax_code or "").strip() == code:
            return row.erpnext_tax_template
    return None


def _company():
    return (frappe.defaults.get_user_default("Company")
            or frappe.db.get_single_value("Global Defaults", "default_company")
            or frappe.db.get_value("Company", {}, "name"))


def _recent(client, entity, limit):
    """SAP's most recent documents of one kind, oldest first.

    Fetched newest-first so the scan window is the recent end of the
    ledger, then reversed so they are created here in the order SAP
    created them.
    """
    rows = client.get_all(entity, params={"$orderby": "DocEntry desc", "$top": limit})
    return list(reversed(rows))


def _too_old(row, since):
    date = str(row.get("DocDate") or "")[:10]
    if not date:
        return False
    try:
        return getdate(date) < since
    except Exception:
        return False


# ------------------------------------------------------------ invoices
def pull_sap_invoices():
    """Mirror invoices raised in SAP as submitted Sales Invoices."""
    with single_flight("sap_invoice_pull") as lock:
        if not lock.acquired:
            return _("An invoice pull is already running — try again in a moment.")
        return _pull_invoices()


def _handle_invoice(row, settings):
    """Import one SAP invoice.

    Returns (outcome, reason) where outcome is "created", "skipped" —
    nothing here will ever import it — or "failed", meaning it could
    still succeed once somebody fixes the cause.
    """
    docentry = cint(row.get("DocEntry"))

    if _too_old(row, _since(settings)):
        return "skipped", _("older than the Pull Documents From date")
    if row.get("Cancelled") == "tYES":
        return "skipped", _("cancelled in SAP")
    if frappe.db.exists("Sales Invoice", {"custom_sap_docentry": str(docentry)}):
        return "skipped", _("already pulled")
    # NumAtCard carries the ERPNext name on anything pushed from here
    origin = (row.get("NumAtCard") or "").strip()
    if origin and frappe.db.exists("Sales Invoice", origin):
        return "skipped", _("originated in ERPNext")
    # The same question again, asked where an answer exists BEFORE the
    # pushing request commits. The app posts invoices with docstatus 1, so
    # the insert and the SAP push share one request: for the length of
    # that round trip SAP has the invoice and the database does not, and
    # the two checks above can only say "never seen it".
    if pushed_from_here("Invoices", docentry, origin):
        return "skipped", _("pushed from here, still committing")

    customer = _customer_for(row.get("CardCode"))
    if not customer:
        return "failed", _("no ERPNext customer for CardCode {0}").format(row.get("CardCode"))

    try:
        name = _make_sales_invoice(row, customer, cint(settings.get("pull_invoice_updates_stock")))
    except Exception as e:
        log_sap("Pull", "Failed", "Invoices", message=(
            f"SAP invoice DocEntry {docentry} (DocNum {row.get('DocNum')}) "
            f"could not be created: {str(e)[:500]}"))
        return "failed", _("failed — see the log")

    _check_total(name, row)
    return "created", None


def _pull_invoices():
    settings = get_settings()
    client = SAPClient(settings)
    rows = _recent(client, "Invoices", _scan_limit(settings))
    message = _summarise("Invoices", rows, settings, _handle_invoice)
    log_sap("Pull", "Success", "Invoices", message=message)
    frappe.db.commit()
    return message


def _summarise(entity, rows, settings, handler):
    """Run the handler over a scanned page and describe what happened."""
    created = skipped = 0
    reasons = {}
    for row in rows:
        outcome, reason = handler(row, settings)
        if outcome == "created":
            created += 1
            continue
        skipped += 1
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
    detail = ", ".join(f"{count} {reason}" for reason, count in sorted(reasons.items()))
    return _("SAP {0}: {1} created, {2} skipped ({3}) out of {4} examined").format(
        entity, created, skipped, detail or _("none"), len(rows))


def _make_sales_invoice(row, customer, moves_stock):
    doc = frappe.new_doc("Sales Invoice")
    doc.customer = customer
    doc.company = _company()
    doc.posting_date = str(row.get("DocDate"))[:10]
    doc.set_posting_time = 1
    doc.due_date = str(row.get("DocDueDate") or row.get("DocDate"))[:10]

    currency = (row.get("DocCurrency") or "").strip()
    if currency and len(currency) == 3:
        doc.currency = currency
        if flt(row.get("DocRate")):
            doc.conversion_rate = flt(row.get("DocRate"))

    # SAP's delivery note is what relieves stock in this flow; moving it
    # again here would take the same goods out twice.
    doc.update_stock = 1 if moves_stock else 0

    vat_group = None
    for line in row.get("DocumentLines") or []:
        code = (line.get("ItemCode") or "").strip()
        item = _item_for(code)
        if not item:
            raise ValueError(
                f"item '{code}' is not in ERPNext. {_item_group_filter_note()}")
        vat_group = vat_group or (line.get("VatGroup") or "").strip()
        doc.append("items", {
            "item_code": item,
            "qty": flt(line.get("Quantity")),
            # net price: VAT is added by the mapped tax template, exactly
            # as it is for an invoice typed here
            "rate": flt(line.get("Price")),
            "warehouse": _warehouse_for(line.get("WarehouseCode")),
        })

    if not doc.get("items"):
        raise ValueError("no usable lines")

    template = _tax_template_for(vat_group)
    if template:
        doc.taxes_and_charges = template
        for tax in frappe.get_doc("Sales Taxes and Charges Template", template).taxes:
            doc.append("taxes", {
                "charge_type": tax.charge_type,
                "account_head": tax.account_head,
                "description": tax.description,
                "rate": tax.rate,
                "included_in_print_rate": tax.included_in_print_rate,
                "cost_center": tax.cost_center,
            })

    doc.custom_sap_docentry = str(cint(row.get("DocEntry")))
    doc.custom_sap_docnum = str(row.get("DocNum") or "")
    doc.custom_sap_sync_status = "Synced"
    doc.remarks = _("From SAP invoice #{0}").format(row.get("DocNum") or row.get("DocEntry"))

    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    doc.submit()
    return doc.name


def _check_total(name, row):
    """Say so when the ERPNext total does not match SAP's.

    A missing or wrong tax mapping produces a perfectly valid invoice for
    the wrong money, and nothing else would ever mention it.
    """
    sap_total = flt(row.get("DocTotal"))
    if not sap_total:
        return
    ours = flt(frappe.db.get_value("Sales Invoice", name, "grand_total"))
    if abs(ours - sap_total) <= 1:
        return
    log_sap("Pull", "Warning", "Invoices",
            reference_doctype="Sales Invoice", reference_name=name, message=(
                f"{name} totals {ours}, but SAP invoice {row.get('DocNum')} totals "
                f"{sap_total} (SAP VAT {flt(row.get('VatSum'))}). Usually the line's VAT "
                "group has no entry in the Tax Mapping table, so no tax was applied here — "
                "run 'Sync Taxes' and link the code to a Sales Taxes template."))


# ------------------------------------------------------------ payments
def pull_sap_payments():
    """Mirror customer payments taken in SAP as submitted Payment Entries."""
    with single_flight("sap_payment_pull") as lock:
        if not lock.acquired:
            return _("A payment pull is already running — try again in a moment.")
        return _pull_payments()


def _handle_payment(row, settings):
    """Import one SAP incoming payment. Same contract as _handle_invoice."""
    docentry = cint(row.get("DocEntry"))

    if _too_old(row, _since(settings)):
        return "skipped", _("older than the Pull Documents From date")
    if row.get("Cancelled") == "tYES":
        return "skipped", _("cancelled in SAP")
    if (row.get("DocType") or "rCustomer") != "rCustomer":
        return "skipped", _("not a customer payment")
    if frappe.db.exists("Payment Entry", {"custom_sap_docentry": str(docentry)}):
        return "skipped", _("already pulled")
    # A payment has no NumAtCard; the push writes "ERPNext <name>" into
    # Remarks, which is the only thing on the SAP side that names its
    # origin. Same two answers as the invoice: the durable one, then the
    # one that works while the pushing request is still open.
    origin = _origin_reference(row.get("Remarks"))
    if origin and frappe.db.exists("Payment Entry", origin):
        return "skipped", _("originated in ERPNext")
    if pushed_from_here("IncomingPayments", docentry, origin):
        return "skipped", _("pushed from here, still committing")

    customer = _customer_for(row.get("CardCode"))
    if not customer:
        return "failed", _("no ERPNext customer for CardCode {0}").format(row.get("CardCode"))

    try:
        _make_payment_entry(row, customer)
    except Exception as e:
        log_sap("Pull", "Failed", "IncomingPayments", message=(
            f"SAP payment DocEntry {docentry} (DocNum {row.get('DocNum')}) "
            f"could not be created: {str(e)[:500]}"))
        return "failed", _("failed — see the log")

    return "created", None


def _pull_payments():
    settings = get_settings()
    client = SAPClient(settings)
    rows = _recent(client, "IncomingPayments", _scan_limit(settings))
    message = _summarise("IncomingPayments", rows, settings, _handle_payment)
    log_sap("Pull", "Success", "IncomingPayments", message=message)
    frappe.db.commit()
    return message


def _payment_account(row, settings):
    """The ERPNext account the money landed in.

    SAP splits the amount across cash, cheque and transfer; whichever is
    non-zero names the account. The G/L account SAP actually posted to is
    preferred, with the settings fallback behind it.
    """
    for amount_field, account_field in (
        ("CashSum", "CashAccount"),
        ("TransferSum", "TransferAccount"),
        ("CheckSum", "CashAccount"),
    ):
        if flt(row.get(amount_field)):
            account = _account_for(row.get(account_field))
            if account:
                return account
    return _account_for(settings.get("sap_cash_account"))


def _make_payment_entry(row, customer):
    settings = get_settings()
    amount = (flt(row.get("CashSum")) + flt(row.get("TransferSum"))
              + flt(row.get("CheckSum")))
    if amount <= 0:
        raise ValueError("payment has no cash, cheque or transfer amount")

    account = _payment_account(row, settings)
    if not account:
        raise ValueError(
            "no ERPNext account carries the SAP G/L code this payment posted to — "
            "set 'SAP G/L Account' on the bank/cash account, or fill the fallback "
            "in SAP Integration Settings")

    doc = frappe.new_doc("Payment Entry")
    doc.payment_type = "Receive"
    doc.company = _company()
    doc.posting_date = str(row.get("DocDate"))[:10]
    doc.party_type = "Customer"
    doc.party = customer
    doc.paid_to = account
    doc.paid_amount = amount
    doc.received_amount = amount
    doc.reference_no = str(row.get("DocNum") or row.get("DocEntry"))
    doc.reference_date = str(row.get("DocDate"))[:10]

    currency = (row.get("DocCurrency") or "").strip()
    if currency and len(currency) == 3:
        doc.paid_from_account_currency = currency

    # Allocate against the invoices SAP applied the money to, so the
    # customer's outstanding here matches theirs. An unallocated payment
    # would leave both the invoice and the payment showing as open.
    for applied in row.get("PaymentInvoices") or []:
        invoice = frappe.db.get_value(
            "Sales Invoice",
            {"custom_sap_docentry": str(cint(applied.get("DocEntry"))), "docstatus": 1},
            "name",
        )
        if not invoice:
            continue
        doc.append("references", {
            "reference_doctype": "Sales Invoice",
            "reference_name": invoice,
            "allocated_amount": flt(applied.get("SumApplied")) or None,
        })

    doc.custom_sap_docentry = str(cint(row.get("DocEntry")))
    doc.custom_sap_docnum = str(row.get("DocNum") or "")
    doc.custom_sap_sync_status = "Synced"
    doc.remarks = _("From SAP payment #{0}").format(row.get("DocNum") or row.get("DocEntry"))

    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    doc.submit()
    return doc.name


# ----------------------------------------------------- incremental poll
#
# The manual pull above scans SAP's recent documents, which is the right
# shape for a catch-up you asked for. It is the wrong shape to run every
# minute. These do the same work incrementally: ask what the newest
# DocEntry is (one row), and fetch only what is above the last one
# handled.
#
# The high-water mark advances only across documents that were imported
# or permanently skipped. One that failed for a fixable reason — a
# customer not synced yet, an item missing — holds the mark where it is
# and is retried on the next tick, while documents after it are still
# imported from the same fetch. A document that keeps failing would
# otherwise anchor the window for good, so after MAX_ATTEMPTS it is
# logged by name and stepped over.

MAX_ATTEMPTS = 5


def _watermark(field):
    return cint(get_settings().get(field))


def _set_watermark(field, value):
    if cint(value) == _watermark(field):
        return
    frappe.db.set_single_value("SAP Integration Settings", field, cint(value))
    frappe.clear_cache(doctype="SAP Integration Settings")


def _newest_docentry(client, entity):
    """The highest DocEntry SAP holds — one row, the cheapest question
    that can be asked of a document table."""
    rows = client.get_all(entity, params={
        "$select": "DocEntry", "$orderby": "DocEntry desc", "$top": 1,
    })
    return cint(rows[0].get("DocEntry")) if rows else 0


def _attempts(entity, docentry):
    key = f"sap_pull_attempts:{entity}:{docentry}"
    count = cint(frappe.cache().get_value(key)) + 1
    frappe.cache().set_value(key, count, expires_in_sec=86400)
    return count


def _poll(entity, field, handler):
    """Shared incremental poll. Returns the number of documents created."""
    settings = get_settings()
    client = SAPClient(settings)
    mark = _watermark(field)

    newest = _newest_docentry(client, entity)
    if not newest:
        return 0

    if not mark:
        # First run: baseline to what SAP already has rather than
        # importing its whole history. Use the manual pull for anything
        # older that is genuinely wanted.
        _set_watermark(field, newest)
        log_sap("Pull", "Success", entity, message=(
            f"Live sync started at DocEntry {newest}. Documents raised in SAP from now on "
            "arrive automatically; use 'Pull SAP Documents' for anything before this."))
        return 0

    if newest <= mark:
        return 0                      # nothing new — the common case

    rows = client.get_all(entity, params={
        "$filter": f"DocEntry gt {mark}",       # numeric: same syntax on v1 and v2
        "$orderby": "DocEntry asc",
        "$top": _scan_limit(settings),
    })

    created = 0
    advance_to = mark
    contiguous = True
    for row in rows:
        entry = cint(row.get("DocEntry"))
        outcome = handler(row, settings)
        if outcome == "created":
            created += 1
        if outcome == "failed" and _attempts(entity, entry) < MAX_ATTEMPTS:
            contiguous = False        # hold the mark; retry next tick
        elif contiguous:
            advance_to = entry

    _set_watermark(field, advance_to)
    if created:
        frappe.db.commit()
    return created


def poll_invoices():
    with single_flight("sap_invoice_pull") as lock:
        if not lock.acquired:
            return 0
        return _poll("Invoices", "last_invoice_docentry", _handle_invoice)


def poll_payments():
    with single_flight("sap_payment_pull") as lock:
        if not lock.acquired:
            return 0
        return _poll("IncomingPayments", "last_payment_docentry", _handle_payment)


# --------------------------------------------------------------- entry
@frappe.whitelist()
def pull_documents():
    """Both pulls, in order: invoices first so the payments that settle
    them have something to allocate against."""
    frappe.only_for(("System Manager", "Administrator"))
    parts = []
    if integration_enabled("pull_sap_invoices"):
        parts.append(pull_sap_invoices())
    if integration_enabled("pull_sap_payments"):
        parts.append(pull_sap_payments())
    if not parts:
        return _("Neither invoice nor payment pull is switched on in SAP Integration Settings.")
    return "\n".join(parts)


def scheduled_document_pull():
    """Kept as an entry point. Invoices and payments are now polled by
    realtime_sync.tick along with transfers, so a stale scheduler entry
    pointing here lands in the same place rather than running a second,
    heavier scan beside it."""
    from stock_addon.stock_addon.sap_integration.realtime_sync import tick
    tick()

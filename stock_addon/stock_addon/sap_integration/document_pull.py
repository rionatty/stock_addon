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


def _pull_invoices():
    settings = get_settings()
    client = SAPClient(settings)
    since = _since(settings)
    moves_stock = cint(settings.get("pull_invoice_updates_stock"))

    created = skipped = 0
    reasons = {}

    def skip(reason):
        nonlocal skipped
        skipped += 1
        reasons[reason] = reasons.get(reason, 0) + 1

    rows = _recent(client, "Invoices", _scan_limit(settings))
    for row in rows:
        docentry = cint(row.get("DocEntry"))
        if _too_old(row, since):
            skip(_("older than the Pull Documents From date"))
            continue
        if row.get("Cancelled") == "tYES":
            skip(_("cancelled in SAP"))
            continue
        if frappe.db.exists("Sales Invoice", {"custom_sap_docentry": str(docentry)}):
            skip(_("already pulled"))
            continue
        # NumAtCard carries the ERPNext name on anything pushed from here
        origin = (row.get("NumAtCard") or "").strip()
        if origin and frappe.db.exists("Sales Invoice", origin):
            skip(_("originated in ERPNext"))
            continue

        customer = _customer_for(row.get("CardCode"))
        if not customer:
            skip(_("no ERPNext customer for CardCode {0}").format(row.get("CardCode")))
            continue

        try:
            name = _make_sales_invoice(row, customer, moves_stock)
        except Exception as e:
            log_sap("Pull", "Failed", "Invoices", message=(
                f"SAP invoice DocEntry {docentry} (DocNum {row.get('DocNum')}) "
                f"could not be created: {str(e)[:500]}"))
            skip(_("failed — see the log"))
            continue

        created += 1
        _check_total(name, row)

    detail = ", ".join(f"{count} {reason}" for reason, count in sorted(reasons.items()))
    message = _("SAP invoices: {0} created, {1} skipped ({2}) out of {3} examined").format(
        created, skipped, detail or _("none"), len(rows))
    log_sap("Pull", "Success", "Invoices", message=message)
    frappe.db.commit()
    return message


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
        if not code or not frappe.db.exists("Item", code):
            raise ValueError(
                f"item '{code}' does not exist in ERPNext — run Sync Items first")
        vat_group = vat_group or (line.get("VatGroup") or "").strip()
        doc.append("items", {
            "item_code": code,
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


def _pull_payments():
    settings = get_settings()
    client = SAPClient(settings)
    since = _since(settings)

    created = skipped = 0
    reasons = {}

    def skip(reason):
        nonlocal skipped
        skipped += 1
        reasons[reason] = reasons.get(reason, 0) + 1

    rows = _recent(client, "IncomingPayments", _scan_limit(settings))
    for row in rows:
        docentry = cint(row.get("DocEntry"))
        if _too_old(row, since):
            skip(_("older than the Pull Documents From date"))
            continue
        if row.get("Cancelled") == "tYES":
            skip(_("cancelled in SAP"))
            continue
        if (row.get("DocType") or "rCustomer") != "rCustomer":
            skip(_("not a customer payment"))
            continue
        if frappe.db.exists("Payment Entry", {"custom_sap_docentry": str(docentry)}):
            skip(_("already pulled"))
            continue

        customer = _customer_for(row.get("CardCode"))
        if not customer:
            skip(_("no ERPNext customer for CardCode {0}").format(row.get("CardCode")))
            continue

        try:
            _make_payment_entry(row, customer)
        except Exception as e:
            log_sap("Pull", "Failed", "IncomingPayments", message=(
                f"SAP payment DocEntry {docentry} (DocNum {row.get('DocNum')}) "
                f"could not be created: {str(e)[:500]}"))
            skip(_("failed — see the log"))
            continue
        created += 1

    detail = ", ".join(f"{count} {reason}" for reason, count in sorted(reasons.items()))
    message = _("SAP payments: {0} created, {1} skipped ({2}) out of {3} examined").format(
        created, skipped, detail or _("none"), len(rows))
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
    """Hourly job. Each half checks its own switch, so one can run
    without the other."""
    if not integration_enabled():
        return
    try:
        if integration_enabled("pull_sap_invoices"):
            pull_sap_invoices()
        if integration_enabled("pull_sap_payments"):
            pull_sap_payments()
    except Exception:
        log_sap("Pull", "Failed", "Documents", message=frappe.get_traceback()[:5000])

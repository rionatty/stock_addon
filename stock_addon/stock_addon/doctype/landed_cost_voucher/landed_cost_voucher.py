import frappe
from frappe.utils import flt, now_datetime
from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import PurchaseInvoice
from frappe import _

# The series of every invoice created here — purchase_invoice_override.py
# exempts it from the PO/PR requirement, and cancelling a voucher uses it to
# find the invoices that voucher made.
SERVICES_SERIES = "PINV-SERVICES-.###.-.YY."


@frappe.whitelist()
def create_purchase_invoice_from_landed_cost_voucher_taxes(doc, method):
    # Group the charge rows by supplier so each supplier gets exactly ONE
    # Purchase Invoice; every charge stays as its own tax line inside it.
    supplier_charges = {}
    for tax_row in doc.taxes:
        supplier = tax_row.custom_supplier or getattr(doc, "supplier", None)
        if not supplier:
            frappe.throw(
                _("Applicable Charges row #{0} ({1}): please set a Supplier on the charge line.").format(
                    tax_row.idx, tax_row.description or tax_row.expense_account
                )
            )
        supplier_charges.setdefault(supplier, []).append(tax_row)

    series_counter = 1
    for supplier, charge_rows in supplier_charges.items():
        total_amount = sum(flt(r.amount) for r in charge_rows)

        pi = frappe.get_doc({
            "doctype": "Purchase Invoice",
            "purchase_invoice_type": "Landed Cost Voucher",
            "naming_series": SERVICES_SERIES,
            "supplier": supplier,
            "grand_total": total_amount,
            "posting_date": now_datetime(),
            "bill_no": f"LCV-{doc.name}-{series_counter}",
            "bill_date": now_datetime(),
            "ignore_pr_validation": 1,
            "ignore_po_validation": 1,
            "custom_landed_cost_voucher_reference": doc.name,
        })

        for item_row in doc.items:
            pi.append("items", {
                "item_code": item_row.item_code,
                "qty": item_row.qty,
                "rate": 0,
                "amount": 0,
                "base_rate": 0,
                "base_amount": 0,
                "price_list_rate": 0,
                "allow_zero_valuation_rate": 1
            })

        # One tax line per charge row — lines are kept separate, only the
        # invoice (header) is shared per supplier.
        for tax_row in charge_rows:
            pi.append("taxes", {
                "charge_type": "Actual",
                "account_head": tax_row.expense_account,
                "description": tax_row.description,
                "tax_amount": tax_row.amount,
                "total": tax_row.amount
            })

        # Set flags before inserting
        pi.flags.ignore_pr_validation = True
        pi.flags.ignore_po_validation = True
        pi.flags.ignore_mandatory = True  # Ensures doc.insert() skips certain mandatory validations
        frappe.local.form_dict["_lcv_invoice_doc"] = pi
        pi.insert(ignore_permissions=True)
        frappe.local.form_dict.pop("_lcv_invoice_doc", None)  # Clean up after insert

        amount = frappe.format_value(total_amount, {"fieldtype": "Currency"})
        refused = _submit_created(pi)
        if refused:
            frappe.msgprint(
                _("Created Purchase Invoice {0} for {1} with {2} charge line(s), total {3}, but it could not be submitted and is saved as a draft: {4}").format(
                    pi.name, supplier, len(charge_rows), amount, refused
                ),
                indicator="orange",
            )
        else:
            frappe.msgprint(
                _("Created and submitted Purchase Invoice {0} for {1} with {2} charge line(s), total {3}").format(
                    pi.name, supplier, len(charge_rows), amount
                )
            )

        series_counter += 1


def _submit_created(created):
    """Submit a Purchase Invoice or Journal Entry this voucher has just created.

    Returns None once it is submitted, or ERPNext's reason for refusing. A
    refused document stays a draft and the voucher still submits: a problem
    with one supplier's invoice or the VAT entry never holds the landed cost
    back from the stock valuation.
    """
    savepoint = "lcv_created_document_submit"
    messages_before = len(frappe.local.message_log)
    frappe.db.savepoint(savepoint)
    try:
        created.submit()  # insert(ignore_permissions=True) left that flag set for the submit too
    except Exception as e:
        # Undo only the half-finished submit; the draft inserted above stays.
        frappe.db.rollback(save_point=savepoint)
        # ERPNext queued its refusal as an error popup as well; the caller
        # reports it once, beside the document it concerns.
        del frappe.local.message_log[messages_before:]
        frappe.log_error(title=f"Landed Cost Voucher: {created.name} left as a draft")
        return str(e) or e.__class__.__name__
    return None


def cancel_purchase_invoices_from_landed_cost_voucher(doc, method=None):
    """Cancel the submitted Purchase Invoices this voucher created.

    They are submitted automatically, so without this they would stay posted
    after the voucher is cancelled, and the amended voucher would bill the
    same charges a second time. If ERPNext refuses to cancel one (a payment
    already allocated to it, say), the voucher is not cancelled either and
    ERPNext's message says why.
    """
    invoices = frappe.get_all(
        "Purchase Invoice",
        filters={
            "custom_landed_cost_voucher_reference": doc.name,
            "naming_series": SERVICES_SERIES,
            "docstatus": 1,
        },
        pluck="name",
    )
    for name in invoices:
        pi = frappe.get_doc("Purchase Invoice", name)
        pi.flags.ignore_permissions = True
        pi.cancel()
        frappe.msgprint(_("Cancelled Purchase Invoice {0}").format(name))


@frappe.whitelist()
def get_landed_cost_defaults(company):
    """What a new voucher starts with (public/js/landed_cost_voucher.js).

    accounts: every account ticked "Landed Cost Account" in this company's
    chart, in chart order; the form adds a line for each, to be priced.
    vat_accounts: the Prepaid VAT and VAT Control accounts the company's last
    submitted voucher used, so they are chosen once, not on every import.
    """
    frappe.has_permission("Landed Cost Voucher", "create", throw=True)
    if not company:
        return {"accounts": [], "vat_accounts": {}}

    accounts = frappe.get_all(
        "Account",
        filters={"company": company, "custom_is_landed_cost_account": 1, "is_group": 0, "disabled": 0},
        fields=["name", "account_name"],
        order_by="lft",
    )
    last = frappe.get_all(
        "Landed Cost Voucher",
        filters={
            "company": company,
            "docstatus": 1,
            "custom_prepaid_vat_account": ["is", "set"],
            "custom_vat_control_account": ["is", "set"],
        },
        fields=["custom_prepaid_vat_account", "custom_vat_control_account"],
        order_by="posting_date desc, creation desc",
        limit=1,
    )
    return {"accounts": accounts, "vat_accounts": last[0] if last else {}}


def remove_uncharged_lines(doc, method=None):
    """Drop Landed Cost lines with no amount before the voucher is validated.

    A new voucher starts with a line for every landed cost account; only the
    ones actually charged belong in the valuation, the supplier invoices and
    the saved voucher. The form drops them too, but vouchers saved any other
    way come through here. With nothing charged the table ends up empty, and
    ERPNext's own "Landed Cost is mandatory" stops the save.
    """
    lines = doc.get("taxes") or []
    charged = [row for row in lines if flt(row.amount)]
    if len(charged) == len(lines):
        return

    doc.set("taxes", charged)
    for idx, row in enumerate(doc.taxes, start=1):
        row.idx = idx


def validate_vat_paid(doc, method=None):
    """VAT Paid goes to its own two accounts, never into item cost."""
    if not flt(doc.get("custom_vat_paid")):
        return

    prepaid = doc.get("custom_prepaid_vat_account")
    control = doc.get("custom_vat_control_account")
    if not prepaid or not control:
        frappe.throw(_("Set both the Prepaid VAT Account and the VAT Control Account for the VAT Paid."))
    if prepaid == control:
        frappe.throw(_("The Prepaid VAT Account and the VAT Control Account must be different accounts."))

    company_currency = frappe.get_cached_value("Company", doc.company, "default_currency")
    for account in (prepaid, control):
        details = frappe.get_cached_value("Account", account, ["company", "is_group", "account_currency"], as_dict=True)
        if details.company != doc.company:
            frappe.throw(_("{0} belongs to company {1}, not {2}.").format(
                frappe.bold(account), details.company, doc.company))
        if details.is_group:
            frappe.throw(_("{0} is a group account; choose an account under it.").format(frappe.bold(account)))
        if details.account_currency and details.account_currency != company_currency:
            # VAT Paid is entered in company currency, and posted as it is.
            frappe.throw(_("{0} is kept in {1}; the VAT accounts must be in {2}.").format(
                frappe.bold(account), details.account_currency, company_currency))

    for row in doc.get("taxes") or []:
        if row.expense_account in (prepaid, control):
            frappe.throw(
                _("Landed Cost row {0}: {1} is a VAT account. VAT Paid is posted on its own and must not "
                  "be added to item cost, so take this line out of the Landed Cost table.").format(
                    row.idx, frappe.bold(row.expense_account))
            )


def post_vat_journal_entry(doc, method=None):
    """Move the VAT Paid out of Prepaid VAT and into the VAT control account.

    Import VAT is input VAT to be claimed back, not a cost of the goods, so it
    goes through a Journal Entry (Dr VAT Control, Cr Prepaid VAT) and never
    through the Landed Cost table that is spread over the items. Submitted
    straight away like the supplier invoices; if ERPNext refuses, it stays a
    draft and the message says why.
    """
    vat = flt(doc.get("custom_vat_paid"))
    if not vat:
        return

    je = frappe.get_doc({
        "doctype": "Journal Entry",
        "voucher_type": "Journal Entry",
        "company": doc.company,
        "posting_date": doc.posting_date,
        "user_remark": _("VAT paid on imports, Landed Cost Voucher {0}").format(doc.name),
        "accounts": [
            {"account": doc.custom_vat_control_account, "debit_in_account_currency": vat},
            {"account": doc.custom_prepaid_vat_account, "credit_in_account_currency": vat},
        ],
    })
    je.insert(ignore_permissions=True)
    doc.db_set("custom_vat_journal_entry", je.name)

    amount = frappe.format_value(vat, {"fieldtype": "Currency"})
    refused = _submit_created(je)
    if refused:
        frappe.msgprint(
            _("Created Journal Entry {0} for VAT Paid of {1}, but it could not be submitted and is saved as a draft: {2}").format(
                je.name, amount, refused
            ),
            indicator="orange",
        )
    else:
        frappe.msgprint(
            _("Posted Journal Entry {0}: VAT Paid of {1} moved from {2} to {3}").format(
                je.name, amount, doc.custom_prepaid_vat_account, doc.custom_vat_control_account
            )
        )


def cancel_vat_journal_entry(doc, method=None):
    """Cancel the VAT Journal Entry with its voucher, as the invoices are."""
    name = doc.get("custom_vat_journal_entry")
    if not name or frappe.db.get_value("Journal Entry", name, "docstatus") != 1:
        return

    je = frappe.get_doc("Journal Entry", name)
    je.flags.ignore_permissions = True
    je.cancel()
    frappe.msgprint(_("Cancelled Journal Entry {0}").format(name))

# Patch validate to skip PO/PR for LCV
original_validate = PurchaseInvoice.validate

def custom_validate(self):
    if getattr(self, "purchase_invoice_type", None) == "Landed Cost Voucher":
        self.ignore_po_validation = True
        self.ignore_pr_validation = True

def patched_validate(self):
    custom_validate(self)
    original_validate(self)

PurchaseInvoice.validate = patched_validate


@frappe.whitelist()
def get_receipt_document_details(receipt_document_type, receipt_document):
    frappe.log_error(f"get_receipt_document_details called with type={receipt_document_type}, doc={receipt_document}", "DEBUG LCV DETAILS")
    if receipt_document_type in [
        "Purchase Invoice",
        "Purchase Receipt",
        "Subcontracting Receipt",
    ]:
        fields = ["supplier", "posting_date", "supplier_delivery_note"]
        if receipt_document_type == "Subcontracting Receipt":
            fields.append("total as grand_total")
        else:
            fields.append("base_grand_total as grand_total")
    elif receipt_document_type == "Stock Entry":
        fields = ["total_incoming_value as grand_total"]

    result = frappe.db.get_value(
        receipt_document_type,
        receipt_document,
        fields,
        as_dict=True,
    )
    frappe.log_error(f"get_receipt_document_details result: {result}", "DEBUG LCV DETAILS")
    return result

@frappe.whitelist()
def purchase_receipt_query(doctype, txt, searchfield, start, page_len, filters):
    frappe.log_error(f"purchase_receipt_query called with txt={txt}", "DEBUG LCV QUERY")
    return frappe.db.sql("""
        SELECT name, supplier_delivery_note
        FROM `tabPurchase Receipt`
        WHERE docstatus = 1
          AND (name LIKE %(txt)s OR supplier_delivery_note LIKE %(txt)s)
        ORDER BY modified DESC
        LIMIT 20
    """, {"txt": f"%{txt}%"})



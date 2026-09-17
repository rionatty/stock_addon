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
        refused = _submit_purchase_invoice(pi)
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


def _submit_purchase_invoice(pi):
    """Submit an invoice this voucher has just created.

    Returns None once it is submitted, or ERPNext's reason for refusing. A
    refused invoice stays a draft, as every one did before, and the voucher
    still submits: a problem with one supplier's invoice never holds the
    landed cost back from the stock valuation.
    """
    savepoint = "lcv_purchase_invoice_submit"
    messages_before = len(frappe.local.message_log)
    frappe.db.savepoint(savepoint)
    try:
        pi.submit()  # insert(ignore_permissions=True) left that flag set for the submit too
    except Exception as e:
        # Undo only the half-finished submit; the draft inserted above stays.
        frappe.db.rollback(save_point=savepoint)
        # ERPNext queued its refusal as an error popup as well; the caller
        # reports it once, beside the invoice it concerns.
        del frappe.local.message_log[messages_before:]
        frappe.log_error(title=f"Landed Cost Voucher: {pi.name} left as a draft")
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



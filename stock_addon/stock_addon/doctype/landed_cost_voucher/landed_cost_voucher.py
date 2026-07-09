import frappe
from frappe.utils import flt, now_datetime
from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import PurchaseInvoice
from frappe import _

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
            "naming_series": "PINV-SERVICES-.###.-.YY.",
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
        frappe.msgprint(
            _("Created Purchase Invoice {0} for {1} with {2} charge line(s), total {3}").format(
                pi.name, supplier, len(charge_rows), frappe.format_value(total_amount, {"fieldtype": "Currency"})
            )
        )

        series_counter += 1

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



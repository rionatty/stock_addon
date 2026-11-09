import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, flt

# def on_submit(self):
#     self.create_lc()
# def on_submit(self, method):
#     # if self.custom_create_landed_cost_ == "Yes":
#         create_lc(self)

    
def create_lc(doc, method):
    """on_submit: with Create Landed Cost = Yes, a draft Landed Cost Voucher for this receipt, its first charge line
    on the receipt's own company's duties account for Finance to complete.

    The account used to be one company's ("Duties and Taxes - SAH"), so on any other company the receipt itself
    could not be submitted. Making the voucher never stops the receipt now: if it cannot be made, the receipt
    goes through and says why.
    """
    if doc.get("custom_create_landed_cost_") != "Yes":
        return
    savepoint = "receipt_landed_cost_voucher"
    messages_before = len(frappe.local.message_log)
    frappe.db.savepoint(savepoint)
    try:
        lc = frappe.new_doc("Landed Cost Voucher")
        lc.company = doc.company

        # Append a new Purchase Receipt row to the Landed Cost Voucher
        pr = lc.append('purchase_receipts')
        pr.receipt_document_type = "Purchase Receipt"
        pr.receipt_document = doc.name
        pr.supplier = doc.supplier
        pr.grand_total = doc.grand_total

        # Append taxes to the Landed Cost Voucher
        account = duties_account(doc.company)
        taxes = lc.append('taxes')
        taxes.expense_account = account
        taxes.description = account or _("Duties and Taxes")
        taxes.amount = 1

        # Save the Landed Cost Voucher
        lc.save()
    except Exception as e:
        frappe.db.rollback(save_point=savepoint)
        # the refusal was queued as an error popup as well; it is said once, below
        del frappe.local.message_log[messages_before:]
        frappe.log_error(title=f"Purchase Receipt {doc.name}: Landed Cost Voucher not made")
        frappe.msgprint(
            _("The Landed Cost Voucher for {0} was not made: {1}").format(doc.name, str(e) or e.__class__.__name__),
            indicator="orange",
        )


def duties_account(company):
    """The company's own ledger for duties: the one named Duties and Taxes, else its only ledger for expenses included
    in valuation, else none for Finance to choose on the voucher."""
    account = frappe.db.get_value(
        "Account", {"company": company, "account_name": "Duties and Taxes", "is_group": 0, "disabled": 0}, "name"
    )
    if account:
        return account
    accounts = frappe.get_all(
        "Account",
        filters={"company": company, "account_type": "Expenses Included In Valuation", "is_group": 0, "disabled": 0},
        pluck="name",
    )
    return accounts[0] if len(accounts) == 1 else None
        
# create outward gate pass from purchase receipt when stock is returned
@frappe.whitelist()
def create_outward_gate_pass_from_purchase_receipt(doc, method):
    # "Outward Gate Pass" is a site-built doctype that is NOT shipped with
    # this app. On sites that don't have it, skip quietly — submitting the
    # Purchase Receipt must never fail because of a missing optional doctype.
    if not frappe.db.exists("DocType", "Outward Gate Pass"):
        return

    if doc.docstatus == 1 and doc.is_return == 1:
        frappe.msgprint("Creating Outward Gate Pass")
        # Create the Outward Gate Pass document
        ogp = frappe.get_doc({
            "doctype": "Outward Gate Pass",
            "ogp_type": "Non-Inventory",
            "type": "Non-Returnable",
            "out_to": "Supplier",
            "document":"Purchase Receipt",
            "voucher": doc.name,
            "supplier": doc.supplier,
            "vehicle_number": "",
            "driver_name": "",
            "driver_contact": "",
            "non_inventory": [],
            "creation_date": now_datetime()
        })

        # Add items from Delivery Note to the child table
        for item in doc.items:
            ogp.append("non_inventory", {
                "item": item.item_code,
                "qty": abs(item.qty),
                "uom": item.uom
            })

        ogp.insert()
        frappe.msgprint("Outward Gate Pass created")

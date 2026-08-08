import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime, flt

# def on_submit(self):
#     self.create_lc()
# def on_submit(self, method):
#     # if self.custom_create_landed_cost_ == "Yes":
#         create_lc(self)

    
def create_lc(doc, method):
    if doc.get("custom_create_landed_cost_") == "Yes":
            lc = frappe.new_doc("Landed Cost Voucher")
            
            # Append a new Purchase Receipt row to the Landed Cost Voucher
            pr = lc.append('purchase_receipts')
            pr.receipt_document_type = "Purchase Receipt"
            pr.receipt_document = doc.name
            pr.supplier = doc.supplier
            pr.grand_total = doc.grand_total
            
            # Append taxes to the Landed Cost Voucher
            taxes = lc.append('taxes')
            taxes.expense_account = "Duties and Taxes - SAH"
            taxes.description = "Duties and Taxes - SAH"
            taxes.amount = 1
            
            # Save the Landed Cost Voucher
            lc.save()
        
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

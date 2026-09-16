import frappe
from frappe.model.document import Document
from frappe.utils import flt

frappe.whitelist()
def calculate_total_qty(doc, method):
	total_qty = 0
	for item in doc.items:
		# flt, not raw addition: a row the app left blank arrives as None,
		# and adding None raises inside validate.
		total_qty += flt(item.qty)
	doc.custom_total_qty = total_qty
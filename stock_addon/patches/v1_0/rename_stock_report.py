# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Rename the 'Stock Report' script report to 'Summarized Stock Report'
and repoint any workspace links/shortcuts that referenced the old name."""

import frappe

OLD = "Stock Report"
NEW = "Summarized Stock Report"


def execute():
	if frappe.db.exists("Report", OLD) and not frappe.db.exists("Report", NEW):
		frappe.rename_doc("Report", OLD, NEW, force=True, ignore_permissions=True)

	# Workspace links/shortcuts store the report name as plain data — fix them up
	for table in ("Workspace Link", "Workspace Shortcut"):
		frappe.db.sql(
			f"""UPDATE `tab{table}`
			SET link_to = %(new)s, label = %(new)s
			WHERE link_to = %(old)s""",
			{"old": OLD, "new": NEW},
		)

# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Put the integration number field name into settings that predate it.

A DocType default only applies to new records. SAP Integration Settings is
a Single that already exists on every site running this app, so shipping
`integration_number_udf` with a default left it *blank* there — the push
stamped nothing, the number stayed empty in SAP, and there was nothing on
either side to say why.

Sending an unknown property makes Service Layer reject the whole document,
so filling this in is only safe because the push now checks the field
against SAP first and logs a warning instead of sending it blind.

Runs once, and only on a blank value, so a site that deliberately cleared
or renamed the field keeps its own answer.
"""

import frappe

DEFAULT_UDF = "U_IntegrationNumber"


def execute():
    if not frappe.db.exists("DocType", "SAP Integration Settings"):
        return
    if frappe.db.get_single_value("SAP Integration Settings", "integration_number_udf"):
        return
    frappe.db.set_single_value("SAP Integration Settings", "integration_number_udf", DEFAULT_UDF)

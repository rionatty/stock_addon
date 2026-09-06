# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Custom fields on doctypes owned by OTHER apps.

Created on migrate rather than shipped as fixtures, and only when the
doctype is actually there. A Custom Field fixture names its doctype in a
Link field, so a fixture for Employee Checkin aborts the whole migrate on
any site without HRMS installed — including the pre-sap branch, which is
deployed somewhere this app has no say over. A missing app should cost
the feature, not the deployment.

Employee Checkin (HRMS) gets two photos: one from each camera, taken
when the rep checks in or out. One record is one event — log_type is IN
or OUT — so two fields cover both ends of the day, giving four pictures
across a shift.

HRMS already records latitude, longitude and a map on the same doctype,
so nothing of ours is added for location: the photos sit directly beneath
what is already there.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

CHECKIN_FIELDS = {
    "Employee Checkin": [
        {
            "fieldname": "custom_checkin_photos_section",
            "label": "Photos",
            "fieldtype": "Section Break",
            # after HRMS's own location block, so the evidence sits together
            "insert_after": "geolocation",
            "description": "Taken by the app at the moment of the check-in or check-out "
                           "this record is for — log type IN or OUT above says which.",
        },
        {
            "fieldname": "custom_photo_front",
            "label": "Photo (Front Camera)",
            "fieldtype": "Attach Image",
            "insert_after": "custom_checkin_photos_section",
            "description": "The selfie — who is checking in.",
        },
        {
            "fieldname": "custom_photo_back",
            "label": "Photo (Back Camera)",
            "fieldtype": "Attach Image",
            "insert_after": "custom_photo_front",
            "description": "What they are looking at — where they are checking in from.",
        },
    ],
}


def ensure_hr_fields():
    """Add the Employee Checkin photo fields, if HRMS is installed."""
    if not frappe.db.exists("DocType", "Employee Checkin"):
        return
    create_custom_fields(CHECKIN_FIELDS, update=True)

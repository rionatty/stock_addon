# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""One call on one customer, as recorded by the rep in Sales Pro.

Not submittable: the app posts a plain document and there is nothing to
approve. A visit either happened or it did not.

The location fields are the same pair every other document here carries,
and they earn their place on a visit more than anywhere else — the map
draws the visit beside the customer's registered address with a line
between them, and the distance says in a number how far apart they were.
A visit logged from somewhere the customer is not is the thing this
record exists to make visible.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime


class CustomerVisit(Document):
    def validate(self):
        self._validate_times()

    def _validate_times(self):
        """A visit cannot end before it began.

        Checked rather than assumed: the two timestamps come from a phone
        clock, and a visit that reads as lasting minus twenty minutes
        would quietly poison any report of time spent per customer.
        """
        if not (self.check_in and self.check_out):
            return
        if get_datetime(self.check_out) < get_datetime(self.check_in):
            frappe.throw(frappe._("Check Out ({0}) is before Check In ({1}).").format(
                self.check_out, self.check_in))

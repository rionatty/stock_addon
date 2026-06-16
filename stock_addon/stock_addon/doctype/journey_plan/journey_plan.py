# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""Journey Plan — assigns customers to a sales rep's weekly call schedule.

Each row in the customers table carries:
  customer   : Link → Customer
  visit_day  : Monday–Saturday
  frequency  : Weekly | Bi-Weekly
  week       : Week 1 | Week 2  (Bi-Weekly rows only)

Bi-weekly cycle rule (universal, no per-plan anchoring):
  odd ISO week number  → Week 1
  even ISO week number → Week 2

On submit the plan goes Active and the mobile-app-facing custom fields on each
Customer record are stamped so the app can resolve today's route with a single
Customer list fetch instead of joining across Journey Plan tables.

On cancel those custom fields are cleared.
"""

import datetime

import frappe
from frappe import _
from frappe.model.document import Document


class JourneyPlan(Document):
	def validate(self):
		self._validate_bi_weekly_rows()
		self.set_status()

	def on_submit(self):
		self.db_set("status", "Active")
		self._sync_customer_fields(clear=False)

	def on_cancel(self):
		self.db_set("status", "Inactive")
		self._sync_customer_fields(clear=True)

	def set_status(self):
		if self.docstatus == 0:
			self.status = "Draft"

	def _validate_bi_weekly_rows(self):
		for row in self.customers:
			if row.frequency == "Bi-Weekly" and not row.week:
				frappe.throw(
					_("Row {0}: choose Week 1 or Week 2 for Bi-Weekly customer {1}.").format(
						row.idx, frappe.bold(row.customer or "")
					)
				)
			if row.frequency == "Weekly":
				row.week = ""  # clear silently — no week needed for weekly visits

	def _sync_customer_fields(self, clear=False):
		"""Stamp (or clear) visit_day and journey_plan on each Customer row.

		Keeps the mobile app's customer list self-sufficient: the app can
		filter today's calls using only the Customer doctype fields without
		an extra API call to Journey Plan.
		"""
		for row in self.customers:
			if not row.customer:
				continue
			frappe.db.set_value(
				"Customer",
				row.customer,
				{
					"custom_visit_day": "" if clear else (row.visit_day or ""),
					"custom_journey_plan": "" if clear else self.name,
				},
				update_modified=False,
			)


@frappe.whitelist()
def get_customers_for_sales_person(sales_person):
	"""Return all customers that have *sales_person* in their Sales Team.

	Used by the 'Get Customers' button on the Journey Plan form to bulk-populate
	the customers child table with the rep's full customer list.
	"""
	return frappe.db.sql(
		"""
		SELECT DISTINCT
		    c.name          AS customer,
		    c.customer_name AS customer_name
		FROM
		    `tabCustomer` c
		    JOIN `tabSales Team` st ON st.parent     = c.name
		                           AND st.parenttype = 'Customer'
		WHERE
		    st.sales_person = %(sp)s
		ORDER BY c.customer_name
		""",
		{"sp": sales_person},
		as_dict=True,
	)


@frappe.whitelist()
def get_todays_journey(sales_person):
	"""Return the customer visit list for *sales_person* on today's date.

	A customer is included when ALL of the following hold:
	  1. They belong to an Active (submitted) Journey Plan for this sales person.
	  2. Their visit_day matches today's weekday name.
	  3. frequency = 'Weekly'  OR
	     frequency = 'Bi-Weekly' AND week matches the current ISO-week cycle
	     (odd week → Week 1, even week → Week 2).

	Returns::

	    {
	        "weekday":   "Monday",
	        "iso_week":  23,
	        "cycle":     "Week 1",
	        "customers": [
	            {"customer": "C10075", "customer_name": "Mummy Nisha",
	             "visit_day": "Monday", "frequency": "Weekly", "week": ""},
	            ...
	        ]
	    }
	"""
	today = datetime.date.today()
	weekday_name = today.strftime("%A")        # "Monday", "Tuesday", …
	iso_week = today.isocalendar()[1]
	current_cycle = "Week 1" if iso_week % 2 == 1 else "Week 2"

	customers = frappe.db.sql(
		"""
		SELECT
		    jpc.customer,
		    jpc.customer_name,
		    jpc.visit_day,
		    jpc.frequency,
		    jpc.week
		FROM
		    `tabJourney Plan Customer` jpc
		    JOIN `tabJourney Plan` jp ON jp.name = jpc.parent
		WHERE
		    jp.sales_person  = %(sp)s
		    AND jp.status    = 'Active'
		    AND jp.docstatus = 1
		    AND jpc.visit_day = %(day)s
		    AND (
		        jpc.frequency = 'Weekly'
		        OR (jpc.frequency = 'Bi-Weekly' AND jpc.week = %(cycle)s)
		    )
		ORDER BY jpc.idx
		""",
		{"sp": sales_person, "day": weekday_name, "cycle": current_cycle},
		as_dict=True,
	)

	return {
		"weekday": weekday_name,
		"iso_week": iso_week,
		"cycle": current_cycle,
		"customers": customers,
	}

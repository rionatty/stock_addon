# Copyright (c) 2026, mohtashim and contributors
# For license information, please see license.txt

"""The party balance shown on a Payment Entry as Total Outstanding.

It is the LEDGER balance — every debit less every credit posted against
the party — which is the figure the customer's own dashboard shows as
Total Unpaid. Not the sum of open invoice amounts: the two part company
whenever a payment was posted without being matched to an invoice, and
then the invoice sum overstates what is owed. A customer who owes 750 on
one invoice but has 10,840 sitting unallocated on account is 10,090 in
CREDIT, and a cashier looking only at the invoice would ask them to pay.

The query is the one erpnext.accounts.party.get_dashboard_info runs.
That function is not whitelisted, so it is repeated here rather than
called; it also only reports companies the party has an invoice in,
which would hide the balance of a customer who has so far only paid in
advance.
"""

import frappe
from frappe.utils import flt


@frappe.whitelist()
def get_party_balance(party_type, party, company):
    """Signed balance for one party in one company.

    Customer: positive is what they owe, negative is credit in their
    favour. Supplier: flipped, as the dashboard flips it, so positive is
    what is owed to them.
    """
    if party_type not in ("Customer", "Supplier") or not party or not company:
        return 0

    frappe.has_permission(party_type, "read", doc=party, throw=True)

    balance = frappe.db.sql(
        """
        SELECT SUM(debit_in_account_currency) - SUM(credit_in_account_currency)
        FROM `tabGL Entry`
        WHERE party_type = %s AND party = %s AND company = %s AND is_cancelled = 0
        """,
        (party_type, party, company),
    )[0][0]

    balance = flt(balance)
    return -balance if party_type == "Supplier" else balance

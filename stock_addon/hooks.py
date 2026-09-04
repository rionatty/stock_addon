app_name = "stock_addon"
app_title = "Stock Addon"
app_publisher = "mohtashim"
app_description = "app for stock addon customization"
app_email = "shoaibmohtashim973@gmail.com"
app_license = "mit"
# required_apps = []

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# SAP Business One navy desk theme (ported from rionatty/Agricalt `twiga`).
# Bundle file — requires `bench build --app stock_addon` after deploy.
app_include_css = "stock_addon.bundle.css"
# Desk-wide scripts (plain asset paths — no bench build needed):
#  - general_ledger_report.js: uniform "Print PDF" button on the STANDARD
#    General Ledger query report (ERPNext owns that report's js, so we
#    watch the route instead)
#  - stock_addon_theme.js: status indicator colours (twiga port)
#  - form_sidebar_toggle.js: collapse/expand the right-hand form panel
app_include_js = [
    "/assets/stock_addon/js/general_ledger_report.js",
    "/assets/stock_addon/js/stock_addon_theme.js",
    "/assets/stock_addon/js/form_sidebar_toggle.js",
    "/assets/stock_addon/js/sap_send_button.js",
]

# Ship the desk colour overrides ("Stock Addon Theme Settings") with the
# session boot so they are applied before first paint.
extend_bootinfo = "stock_addon.stock_addon.theme.boot_session"

# include js, css files in header of web template
# web_include_css = "/assets/stock_addon/css/stock_addon.css"
# web_include_js = "/assets/stock_addon/js/stock_addon.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "stock_addon/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {
    # "Item" : "public/js/item.js",
    "Purchase Receipt" : "public/js/purchase_receipt.js ",
    "Stock Entry" : "public/js/stock_entry.js",
    "Landed Cost Voucher" : "public/js/landed_cost_voucher.js",
    "Delivery Note" : "public/js/delivery_note.js",
    "Sales Invoice" : "public/js/sales_invoice.js",
    # Journey Plan JS is installed as a Client Script via after_migrate (no bench build needed)
    }
doctype_list_js = {
    "Material Request" : "public/js/material_request_list.js",
    "Purchase Receipt" : "public/js/purchase_receipt_list.js",
    "Purchase Order" : "public/js/purchase_order_list.js",
    "Bin" : "public/js/bin_list.js",
    # Customer list JS is installed as a Client Script via after_migrate (no bench build needed)
    }
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "stock_addon/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "stock_addon.utils.jinja_methods",
# 	"filters": "stock_addon.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "stock_addon.install.before_install"
# after_install = "stock_addon.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "stock_addon.uninstall.before_uninstall"
# after_uninstall = "stock_addon.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "stock_addon.utils.before_app_install"
# after_app_install = "stock_addon.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "stock_addon.utils.before_app_uninstall"
# after_app_uninstall = "stock_addon.utils.after_app_uninstall"

# Migration
# ---------
# Inject the Inventory Counting link + all Stock Addon reports into the
# right standard workspaces (idempotent + self-healing on every migrate).
after_migrate = [
    "stock_addon.stock_addon.workspace_setup.add_inventory_counting_to_stock_workspace",
    "stock_addon.stock_addon.workspace_setup.add_stock_addon_reports_to_stock_workspace",
    "stock_addon.stock_addon.workspace_setup.add_route_and_sales_reports_to_accounts_workspace",
    "stock_addon.stock_addon.workspace_setup.add_summarized_stock_report_shortcut",
    "stock_addon.stock_addon.workspace_setup.add_journey_plan_to_accounts_workspace",
    # SAP Integration home tile — self-healing upsert from the shipped JSON
    "stock_addon.stock_addon.workspace_setup.ensure_sap_integration_workspace",
    # Client Scripts — push JS files into the DB so they load without bench build
    "stock_addon.stock_addon.client_scripts.install_journey_plan_form_script",
    "stock_addon.stock_addon.client_scripts.install_work_order_form_script",
    "stock_addon.stock_addon.client_scripts.remove_customer_list_script",
    # Switch off leftover Client Scripts that call an uninstalled app
    # (e.g. the old standalone "Batch Generation" script — that feature
    # lives in this app now).
    "stock_addon.stock_addon.client_scripts.disable_dead_client_scripts",
    # Default list ordering (Material Request: newest first, by date+time)
    "stock_addon.stock_addon.list_settings.set_default_list_sort",
    # Standard GL: turn off add_total_row (the client-side footer doubled
    # Debit/Credit and summed the Running Balance into a meaningless number).
    "stock_addon.stock_addon.report_patches.disable_gl_footer_total",
]

# Standard GL report is patched server-side via a module-level monkey patch
# installed in stock_addon/__init__.py — it wraps
# ``erpnext.accounts.report.general_ledger.general_ledger.execute`` to:
#   * remove Frappe's auto-footer Total row (sum of running balances was a
#     meaningless number),
#   * inject a Customer / Party Name column before Debit (resolved from
#     party_type/party OR the customer code in the "against" column for
#     cash/bank rows),
#   * relabel the Balance column to "Running Balance".
# No JS, no Client Script, no bench build required — every call to the
# standard GL returns the patched data.

# Fixtures — installed/updated on bench migrate (no bench build needed).
# Files live in stock_addon/fixtures/ (the app-package root, next to this
# hooks.py) — Frappe only syncs fixtures from there.
# Custom Fields added:
#     • Stock Entry Detail.custom_sales_price (Currency) — copied from MR
#     • Material Request.custom_user / Stock Entry.custom_user (User column)
#     • Customer.custom_visit_day / Customer.custom_journey_plan (Journey Plan)
#     • Sales Pro mobile app fields (exact fieldnames the app reads/writes):
#         Sales Person.custom_mapped_warehouse / custom_serving_warehouse /
#             custom_cash_account / custom_route_names
#         Material Request.custom_van_request / custom_van_return /
#             custom_dales_order / custom_total_qty / custom_total_stock_value /
#             custom_request_form / custom_narration
#         Material Request Item.custom_sales_price
#         Sales Order Item.custom_sales_price
#         Stock Entry.custom_sales_rep_confirmed
#         Item Group.custom_van_sell
#         Lead.custom_tin_number
#         Sales Invoice.custom_efris_fdn / custom_efris_verification_code /
#             custom_efris_qr_code
fixtures = [
    {
        "dt": "Custom Field",
        "filters": [
            [
                "name",
                "in",
                [
                    "Stock Entry Detail-custom_sales_price",
                    "Stock Entry Detail-custom_total_amount_sales_price",
                    "Stock Entry-custom_total_qty",
                    "Stock Entry-custom_total_sales_amount",
                    "Material Request-custom_user",
                    "Stock Entry-custom_user",
                    "Customer-custom_visit_day",
                    "Customer-custom_journey_plan",
                    "Sales Invoice-custom_return_reason",
                    "Sales Invoice-custom_return_narration",
                    "Work Order-custom_batch_number",
                    "Work Order-custom_batch_no",
                    "Work Order-custom_mfg",
                    "Work Order-custom_expiry_date",
                    "Batch-custom_batch_status",
                    "Quality Inspection-custom_batch_status",
                    "Sales Order-custom_sap_sync_status",
                    "Sales Order-custom_sap_docentry",
                    "Sales Order-custom_sap_docnum",
                    "Sales Invoice-custom_sap_sync_status",
                    "Sales Invoice-custom_sap_docentry",
                    "Sales Invoice-custom_sap_docnum",
                    "Material Request-custom_sap_sync_status",
                    "Material Request-custom_sap_docentry",
                    "Material Request-custom_sap_docnum",
                    "Payment Entry-custom_sap_sync_status",
                    "Payment Entry-custom_sap_docentry",
                    "Payment Entry-custom_sap_docnum",
                    "Field Expense-custom_sap_sync_status",
                    "Field Expense-custom_sap_docentry",
                    "Field Expense-custom_sap_docnum",
                    "Customer-custom_sap_cardcode",
                    "Customer-custom_sap_salesperson",
                    "Stock Entry-custom_sap_docentry",
                    "Account-custom_sap_gl_account",
                    # Sales Pro mobile app custom fields
                    "Sales Person-custom_mapped_warehouse",
                    "Sales Person-custom_serving_warehouse",
                    "Sales Person-custom_cash_account",
                    "Sales Person-custom_route_names",
                    "Material Request-custom_van_request",
                    "Material Request-custom_van_return",
                    "Material Request-custom_dales_order",
                    "Material Request-custom_total_qty",
                    "Material Request-custom_total_stock_value",
                    "Material Request-custom_request_form",
                    "Material Request-custom_narration",
                    "Material Request Item-custom_sales_price",
                    "Sales Order Item-custom_sales_price",
                    "Stock Entry-custom_sales_rep_confirmed",
                    "Item Group-custom_van_sell",
                    "Lead-custom_tin_number",
                    "Sales Invoice-custom_efris_fdn",
                    "Sales Invoice-custom_efris_verification_code",
                    "Sales Invoice-custom_efris_qr_code",
                ],
            ]
        ]
    },
]

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "stock_addon.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

override_doctype_class = {
	"Purchase Invoice": "stock_addon.stock_addon.overrides.purchase_invoice_override.PurchaseInvoice",
    "Purchase Receipt": "stock_addon.stock_addon.overrides.purchase_receipt_override.PurchaseReceipt",
    "Bin": "stock_addon.stock_addon.doctype.bin.bin.Bin"
}

# Document Events
# ---------------
# Hook on document methods and events
# NOTE: doc_events must be assigned exactly ONCE in this module — a second
# assignment silently replaces the first and Frappe only sees the last one.

doc_events = {
    "Purchase Receipt": {
        # "validate": "stock_addon.stock_addon.api.get_last_purchase_details_custom",
        "on_submit": [
            "stock_addon.stock_addon.doctype.purchase_receipt.purchase_receipt.create_lc",
            "stock_addon.stock_addon.doctype.purchase_receipt.purchase_receipt.create_outward_gate_pass_from_purchase_receipt",
            "stock_addon.stock_addon.doctype.bin.bin.recalc_impacted_bins"
        ],
        "on_cancel": [
            "stock_addon.stock_addon.doctype.bin.bin.recalc_impacted_bins"
        ]
    },
    "Stock Entry": {
        "on_submit": [
            "stock_addon.stock_addon.doctype.bin.bin.recalc_impacted_bins"
        ],
        "on_cancel": [
            "stock_addon.stock_addon.doctype.bin.bin.recalc_impacted_bins"
        ],
        "validate": [
            "stock_addon.stock_addon.doctype.stock_entry.stock_entry.set_cost_center_to_child_items",
            "stock_addon.stock_addon.doctype.stock_entry.stock_entry.get_expense_account",
            # Sales price autofill + row/document totals — validate runs on
            # both save and submit, so totals can never persist empty.
            "stock_addon.stock_addon.doc_events.stock_entry.set_sales_prices_and_totals",
            # Manufacture receipts: auto-fill the Work Order's generated
            # batch on the finished-item row.
            "stock_addon.stock_addon.doc_events.stock_entry.set_batch_from_work_order"
        ]
    },
    "Stock Reconciliation": {
        "on_submit": [
            "stock_addon.stock_addon.doctype.bin.bin.recalc_impacted_bins"
        ],
        "on_cancel": [
            "stock_addon.stock_addon.doctype.bin.bin.recalc_impacted_bins"
        ]
    },
    "Delivery Note": {
        "on_submit": "stock_addon.stock_addon.doctype.delivery_note.delivery_note.create_outward_gate_pass_from_delivery_note",
    },
    "Material Request": {
        "validate": "stock_addon.stock_addon.doctype.material_request.material_request.calculate_total_qty",
        # SAP: van stock requests go to SAP as Inventory Transfer Requests
        "on_submit": "stock_addon.stock_addon.sap_integration.transactions.on_material_request_submit",
    },
    "Landed Cost Voucher": {
        "on_submit": "stock_addon.stock_addon.doctype.landed_cost_voucher.landed_cost_voucher.create_purchase_invoice_from_landed_cost_voucher_taxes",
    },
    "Sales Invoice": {
        "validate": "stock_addon.stock_addon.doc_events.sales_invoice.validate",
        # SAP: invoices / credit notes push on submit
        "on_submit": "stock_addon.stock_addon.sap_integration.transactions.on_sales_invoice_submit",
    },
    "Payment Entry": {
        # SAP: incoming customer payments push on submit
        "on_submit": "stock_addon.stock_addon.sap_integration.transactions.on_payment_entry_submit",
    },
    "Sales Order": {
        # SAP: only when "Send Sales Orders" is On Submit — the handler checks
        "on_submit": "stock_addon.stock_addon.sap_integration.transactions.on_sales_order_submit",
    },
    "Sales Person": {
        # On creation, auto-provision a Cost Center + Warehouse named after
        # the sales person's own code.
        "after_insert": "stock_addon.stock_addon.doc_events.sales_person.after_insert",
    },
    "Quality Inspection": {
        # Copy the QC disposition (Batch Status) onto the linked Batch.
        "on_submit": "stock_addon.stock_addon.doc_events.quality_inspection.sync_batch_status",
        "on_update_after_submit": "stock_addon.stock_addon.doc_events.quality_inspection.sync_batch_status",
    },
}

# Scheduled Tasks
# ---------------
# SAP B1 integration background jobs (all internally guarded by the
# "Enable SAP Integration" master switch + their own feature toggles).
scheduler_events = {
    "cron": {
        # every minute: mirror SAP van-warehouse transfers into ERPNext.
        # A run that finds nothing is one cheap filtered GET, and the
        # single-flight lock stops runs overlapping, so this is the
        # tightest loop the scheduler allows.
        "* * * * *": [
            "stock_addon.stock_addon.sap_integration.stock_pull.scheduled_pull",
        ],
        # hourly: re-sync items + customers (only if auto-sync is on)
        "0 * * * *": [
            "stock_addon.stock_addon.sap_integration.masters.scheduled_masters_sync",
            "stock_addon.stock_addon.sap_integration.pricing.scheduled_pricing_sync",
        ],
    },
}

# scheduler_events = {
# 	"all": [
# 		"stock_addon.tasks.all"
# 	],
# 	"daily": [
# 		"stock_addon.tasks.daily"
# 	],
# 	"hourly": [
# 		"stock_addon.tasks.hourly"
# 	],
# 	"weekly": [
# 		"stock_addon.tasks.weekly"
# 	],
# 	"monthly": [
# 		"stock_addon.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "stock_addon.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "stock_addon.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps

override_doctype_dashboards = {
    "Purchase Invoice": "stock_addon.stock_addon.doctype.purchase_invoice.purchase_invoice_dashboard.get_data",
    "Landed Cost Voucher": "stock_addon.stock_addon.doctype.landed_cost_voucher.landed_cost_voucher_dashboard.get_data"
}

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["stock_addon.utils.before_request"]
# after_request = ["stock_addon.utils.after_request"]

# Job Events
# ----------
# before_job = ["stock_addon.utils.before_job"]
# after_job = ["stock_addon.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"stock_addon.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }


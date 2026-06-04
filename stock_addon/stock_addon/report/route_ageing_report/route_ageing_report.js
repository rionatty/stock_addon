// Copyright (c) 2024, Your Company and contributors
// For license information, please see license.txt

frappe.query_reports["Route Ageing Report"] = {
    "filters": [
        {
            "fieldname": "from_date",
            "label": __("From Date"),
            "fieldtype": "Date",
            "default": frappe.datetime.add_months(frappe.datetime.get_today(), -1),
            "reqd": 1
        },
        {
            "fieldname": "to_date",
            "label": __("To Date"),
            "fieldtype": "Date",
            "default": frappe.datetime.get_today(),
            "reqd": 1
        },
        {
            "fieldname": "company",
            "label": __("Company"),
            "fieldtype": "Link",
            "options": "Company",
            "default": frappe.defaults.get_user_default("Company"),
            "reqd": 1
        },
        {
            "fieldname": "sales_person",
            "label": __("Route / Sales Person"),
            "fieldtype": "Link",
            "options": "Sales Person"
        },
        {
            "fieldname": "customer",
            "label": __("Customer"),
            "fieldtype": "Link",
            "options": "Customer"
        },
        {
            "fieldname": "payment_status",
            "label": __("Payment Status"),
            "fieldtype": "MultiSelectList",
            "get_data": function(txt) {
                return [
                    {value: "Paid", description: "Fully paid invoices"},
                    {value: "Unpaid", description: "No payment received"},
                    {value: "Partly Paid", description: "Partial payment received"},
                    {value: "Overdue", description: "Past due date"},
                    {value: "Return", description: "Return/Credit Note"},
                    {value: "Credit Note Issued", description: "Credit note applied"}
                ];
            },
            "default": []
        },
        {
            "fieldname": "show_unreconciled_only",
            "label": __("Show Unreconciled Only"),
            "fieldtype": "Check",
            "default": 1,
            "description": __("Only show invoices with outstanding amount > 0")
        },
        {
            "fieldname": "ageing_based_on",
            "label": __("Ageing Based On"),
            "fieldtype": "Select",
            "options": "Due Date\nPosting Date",
            "default": "Due Date"
        },
        {
            "fieldname": "range1",
            "label": __("Range 1 (Days)"),
            "fieldtype": "Int",
            "default": 30
        },
        {
            "fieldname": "range2",
            "label": __("Range 2 (Days)"),
            "fieldtype": "Int",
            "default": 60
        },
        {
            "fieldname": "range3",
            "label": __("Range 3 (Days)"),
            "fieldtype": "Int",
            "default": 90
        }
    ],

    "formatter": function(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);

        if (data && data.entry_type === "BBF") {
            if (column.fieldname === "entry_type") {
                value = `<span style="background:#fef3c7;color:#92400e;padding:2px 8px;border-radius:3px;font-weight:bold">BBF</span>`;
            }
        }

        if (data && data.entry_type === "Return") {
            if (column.fieldname === "entry_type") {
                value = `<span style="background:#fed7d7;color:#9b2c2c;padding:2px 8px;border-radius:3px;font-weight:bold">RETURN</span>`;
            }
        }

        if (column.fieldname === "status" && data && data.status) {
            let colors = {
                "Paid": "#2f855a",
                "Unpaid": "#c53030",
                "Partly Paid": "#c05621",
                "Overdue": "#9b2c2c",
                "Return": "#718096",
                "Credit Note Issued": "#718096"
            };
            let bg = colors[data.status] || "#4a5568";
            value = `<span style="background:${bg};color:white;padding:2px 8px;border-radius:3px;font-size:10px;font-weight:600">${data.status}</span>`;
        }

        if (column.fieldname === "days_overdue" && data && data.days_overdue) {
            let days = data.days_overdue;
            if (days > 90) {
                value = `<span style="color:#9b2c2c;font-weight:bold;background:#fed7d7;padding:2px 6px;border-radius:3px">${value}</span>`;
            } else if (days > 60) {
                value = `<span style="color:#c53030;font-weight:bold">${value}</span>`;
            } else if (days > 30) {
                value = `<span style="color:#c05621;font-weight:bold">${value}</span>`;
            } else if (days > 0) {
                value = `<span style="color:#2b6cb0">${value}</span>`;
            }
        }

        if (column.fieldname === "debit" && data && data.debit > 0) {
            value = `<span style="color:#c05621">${value}</span>`;
        }

        if (column.fieldname === "credit" && data && data.credit > 0) {
            value = `<span style="color:#2f855a">${value}</span>`;
        }

        if (column.fieldname === "range4" && data && data.range4 > 0) {
            value = `<span style="color:#9b2c2c;font-weight:bold">${value}</span>`;
        }

        return value;
    },

    onload: function(report) {
        report.page.add_inner_button(__("Print PDF"), function() {
            let filters = report.get_filter_values();
            let data = report.data || [];

            if (!data.length) {
                frappe.msgprint(__("Please run the report first"));
                return;
            }

            frappe.call({
                method: "stock_addon.stock_addon.report.route_ageing_report.route_ageing_report.get_pdf_html",
                args: {
                    filters: filters,
                    data: data
                },
                callback: function(r) {
                    if (r.message) {
                        let print_window = window.open("", "_blank");
                        print_window.document.write(r.message);
                        print_window.document.close();
                        print_window.focus();
                        setTimeout(() => {
                            print_window.print();
                        }, 500);
                    }
                }
            });
        });
    }
};
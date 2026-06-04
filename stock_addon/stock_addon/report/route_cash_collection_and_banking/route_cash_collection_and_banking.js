// Copyright (c) 2026, Your Company and contributors
// For license information, please see license.txt

frappe.query_reports["Route Cash Collection and Banking"] = {
    filters: [
        {
            fieldname: "company",
            label: __("Company"),
            fieldtype: "Link",
            options: "Company",
            default: frappe.defaults.get_user_default("Company"),
            reqd: 1
        },
        {
            fieldname: "from_date",
            label: __("From Date"),
            fieldtype: "Date",
            default: frappe.datetime.month_start(),
            reqd: 1
        },
        {
            fieldname: "to_date",
            label: __("To Date"),
            fieldtype: "Date",
            default: frappe.datetime.now_date(),
            reqd: 1
        },
        {
            fieldname: "sales_person",
            label: __("Route / Sales Person"),
            fieldtype: "Link",
            options: "Sales Person"
        },
        {
            fieldname: "employee",
            label: __("Employee"),
            fieldtype: "Link",
            options: "Employee"
        },
        {
            fieldname: "user",
            label: __("User"),
            fieldtype: "Link",
            options: "User"
        },
        {
            fieldname: "show_zero_routes",
            label: __("Show Routes With No Activity"),
            fieldtype: "Check",
            default: 0
        }
    ],

    formatter: function (value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);

        if (data && data.entry_type === "ROUTE") {
            value = `<span style="font-weight:700;color:#5e72e4">${value || ""}</span>`;
        }
        if (data && data.entry_type === "SUBTOTAL") {
            value = `<span style="font-weight:700;color:#1a365d">${value || ""}</span>`;
        }
        if (column.fieldname === "collected" && flt(data?.collected) > 0) {
            value = `<span style="color:#2f855a;font-weight:600">${value}</span>`;
        }
        if (column.fieldname === "banked" && flt(data?.banked) > 0) {
            value = `<span style="color:#c05621;font-weight:600">${value}</span>`;
        }
        return value;
    },

    onload: function (report) {
        report.page.add_inner_button(__("Print PDF"), function () {
            const filters = report.get_values();
            const data = report.data || [];

            if (!data.length) {
                frappe.msgprint(__("Run the report first."));
                return;
            }

            frappe.call({
                method: "stock_addon.stock_addon.report.route_cash_collection_and_banking.route_cash_collection_and_banking.get_pdf_html",
                args: {
                    filters: JSON.stringify(filters),
                    data: JSON.stringify(data)
                },
                freeze: true,
                freeze_message: __("Generating PDF..."),
                callback: function (r) {
                    if (r.message) {
                        const w = window.open();
                        w.document.write(r.message);
                        w.document.close();
                        setTimeout(() => w.print(), 600);
                    }
                }
            });
        }).addClass("btn-primary");
    }
};
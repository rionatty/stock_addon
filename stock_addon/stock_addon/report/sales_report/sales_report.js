frappe.query_reports["Sales Report"] = {
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
            fieldname: "group_by",
            label: __("Group By"),
            fieldtype: "Select",
            options: "Detailed\nRoute\nCustomer\nItem\nItem Group\nDate",
            default: "Detailed"
        },
        {
            fieldname: "sales_person",
            label: __("Route / Sales Person"),
            fieldtype: "Link",
            options: "Sales Person"
        },
        {
            fieldname: "customer",
            label: __("Customer"),
            fieldtype: "Link",
            options: "Customer"
        },
        {
            fieldname: "customer_group",
            label: __("Customer Group"),
            fieldtype: "Link",
            options: "Customer Group"
        },
        {
            fieldname: "territory",
            label: __("Territory"),
            fieldtype: "Link",
            options: "Territory"
        },
        {
            fieldname: "item_code",
            label: __("Item"),
            fieldtype: "Link",
            options: "Item"
        },
        {
            fieldname: "item_group",
            label: __("Item Group"),
            fieldtype: "Link",
            options: "Item Group"
        },
        {
            fieldname: "brand",
            label: __("Brand"),
            fieldtype: "Link",
            options: "Brand"
        },
        {
            fieldname: "warehouse",
            label: __("Warehouse"),
            fieldtype: "Link",
            options: "Warehouse"
        },
        {
            fieldname: "payment_status",
            label: __("Payment Status"),
            fieldtype: "MultiSelectList",
            get_data: function () {
                return [
                    { value: "Paid", description: "Fully paid" },
                    { value: "Unpaid", description: "Not paid" },
                    { value: "Partly Paid", description: "Partially paid" },
                    { value: "Overdue", description: "Past due date" },
                    { value: "Return", description: "Sales return" },
                    { value: "Credit Note Issued", description: "Credit note" }
                ];
            }
        },
        {
            fieldname: "payment_terms_template",
            label: __("Payment Terms Template"),
            fieldtype: "Link",
            options: "Payment Terms Template"
        },
        {
            fieldname: "payment_term",
            label: __("Payment Term"),
            fieldtype: "Link",
            options: "Payment Term"
        },
        {
            fieldname: "include_returns",
            label: __("Include Returns"),
            fieldtype: "Check",
            default: 1
        }
    ],

    formatter: function (value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);

        if (data && data.entry_type === "GROUP") {
            value = `<span style="font-weight:700;color:#5e72e4">${value || ""}</span>`;
        }
        if (data && data.entry_type === "SUBTOTAL") {
            value = `<span style="font-weight:700;color:#1a365d">${value || ""}</span>`;
        }
        if (column.fieldname === "net_total" && flt(data?.net_total) < 0) {
            value = `<span style="color:#c53030;font-weight:600">${value}</span>`;
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
                method: "stock_addon.stock_addon.report.sales_report.sales_report.get_pdf_html",
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
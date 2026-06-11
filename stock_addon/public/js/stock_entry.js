frappe.ui.form.on("Stock Entry", {
    refresh: function(frm) {
        if (frm.doc.stock_entry_type == "Material Transfer for Manufacture" || frm.doc.stock_entry_type == "Manufacture") {
            frm.set_df_property("custom_cost_center", "reqd", 1);
        } else {
            frm.set_df_property("custom_cost_center", "reqd", 0); 
        }
    },
    
    // Handle stock_entry_type changes to update all rows
    stock_entry_type: function(frm) {
        if (frm.doc.stock_entry_type === 'Stitched Products' && frm.doc.items) {
            // Update all existing rows when type changes to "Stitched Products"
            frm.doc.items.forEach(function(item) {
                var cdt = item.doctype || 'Stock Entry Detail';
                var cdn = item.name;
                if (item.s_warehouse && item.s_warehouse !== "") {
                    // If s_warehouse is present (has warehouse), set allow_zero_valuation_rate = 0
                    frappe.model.set_value(cdt, cdn, 'allow_zero_valuation_rate', 0);
                } else {
                    // If s_warehouse is empty, set allow_zero_valuation_rate = 1
                    frappe.model.set_value(cdt, cdn, 'allow_zero_valuation_rate', 1);
                }
            });
        }
    },
    
    items_add: function(frm, cdt, cdn) {
        var row = frappe.get_doc(cdt, cdn);
        console.log('[STOCK ENTRY DEBUG] items_add triggered for row:', cdn);
        
        // Use setTimeout to ensure item_code is set (Excel paste may set fields async)
        setTimeout(function() {
            row = locals[cdt][cdn];
            
            // Check if row still exists
            if (!row) {
                console.log('[STOCK ENTRY DEBUG] Row deleted in items_add');
                return;
            }
            
            console.log('[STOCK ENTRY DEBUG] items_add - row data:', {
                item_code: row.item_code,
                uom: row.uom,
                stock_uom: row.stock_uom,
                qty: row.qty,
                stock_entry_type: frm.doc.stock_entry_type
            });
            
            // Set qty to 1 if not set
            if (!row.qty || row.qty == 0) {
                console.log('[STOCK ENTRY DEBUG] Setting qty to 1');
                frappe.model.set_value(cdt, cdn, 'qty', 1);
            }
            
            // Set allow_zero_valuation_rate based on s_warehouse for "Stitched Products"
            if (frm.doc.stock_entry_type === 'Stitched Products') {
                if (row.s_warehouse && row.s_warehouse !== "") {
                    // If s_warehouse is present (has warehouse), set allow_zero_valuation_rate = 0
                    console.log('[STOCK ENTRY DEBUG] Setting allow_zero_valuation_rate to 0 (s_warehouse is present)');
                    frappe.model.set_value(cdt, cdn, 'allow_zero_valuation_rate', 0);
                } else {
                    // If s_warehouse is empty, set allow_zero_valuation_rate = 1
                    console.log('[STOCK ENTRY DEBUG] Setting allow_zero_valuation_rate to 1 (s_warehouse is empty)');
                    frappe.model.set_value(cdt, cdn, 'allow_zero_valuation_rate', 1);
                }
            }
            
            // If UOM or stock_uom is not set, fetch from item
            if (row.item_code && (!row.uom || !row.stock_uom)) {
                console.log('[STOCK ENTRY DEBUG] Fetching UOM for item:', row.item_code);
                frappe.call({
                    method: 'frappe.client.get_value',
                    args: {
                        doctype: 'Item',
                        filters: { name: row.item_code },
                        fieldname: ['stock_uom']
                    },
                    callback: function(r) {
                        console.log('[STOCK ENTRY DEBUG] Server response for UOM:', r.message);
                        
                        // Re-check if row still exists
                        var current_row = locals[cdt][cdn];
                        if (!current_row) {
                            console.log('[STOCK ENTRY DEBUG] Row deleted during fetch in items_add');
                            return;
                        }
                        
                        if (r.message && r.message.stock_uom) {
                            // Set stock_uom if not set
                            if (!current_row.stock_uom) {
                                console.log('[STOCK ENTRY DEBUG] Setting stock_uom to:', r.message.stock_uom);
                                frappe.model.set_value(cdt, cdn, 'stock_uom', r.message.stock_uom);
                            }
                            // Set uom to stock_uom if not already set
                            if (!current_row.uom) {
                                console.log('[STOCK ENTRY DEBUG] Setting uom to:', r.message.stock_uom);
                                frappe.model.set_value(cdt, cdn, 'uom', r.message.stock_uom);
                            }
                        } else {
                            console.log('[STOCK ENTRY DEBUG] No UOM data returned from server');
                        }
                    },
                    error: function(r) {
                        console.error('[STOCK ENTRY DEBUG] Error fetching UOM:', r);
                    }
                });
            } else {
                console.log('[STOCK ENTRY DEBUG] UOM fields already populated or no item_code');
            }
        }, 100);
    }
});

frappe.ui.form.on('Stock Entry', {
    refresh: function(frm) {
        // Add custom buttons to the existing "Get Items From" dropdown
        if (frm.doc.docstatus === 0) {
            // Add "Warehouse" option to Get Items From dropdown
            // frm.add_custom_button(__('Warehouse'), function() {
            //     show_warehouse_dialog(frm);
            // }, __('Get Items From'));
            
            // Add "Sales Order" option to Get Items From dropdown
            // frm.add_custom_button(__('Sales Order'), function() {
            //     show_sales_order_dialog(frm);
            // }, __('Get Items From'));
            
            // Add "Warehouse with Sales Order" option to Get Items From dropdown
            // frm.add_custom_button(__('Warehouse with Sales Order'), function() {
            //     show_warehouse_with_sales_order_dialog(frm);
            // }, __('Get Items From'));
            
            // Add "Cost Center" option to Get Items From dropdown
            frm.add_custom_button(__('Warehouse with Cost Center'), function() {
                show_cost_center_dialog(frm);
            }, __('Get Items From'));
        }
        
        // Update allow_zero_valuation_rate for all rows when form loads (for "Stitched Products")
        if (frm.doc.stock_entry_type === 'Stitched Products' && frm.doc.items) {
            frm.doc.items.forEach(function(item) {
                var cdt = item.doctype || 'Stock Entry Detail';
                var cdn = item.name;
                if (item.s_warehouse && item.s_warehouse !== "") {
                    // If s_warehouse is present (has warehouse), set allow_zero_valuation_rate = 0
                    frappe.model.set_value(cdt, cdn, 'allow_zero_valuation_rate', 0);
                } else {
                    // If s_warehouse is empty, set allow_zero_valuation_rate = 1
                    frappe.model.set_value(cdt, cdn, 'allow_zero_valuation_rate', 1);
                }
            });
        }
    }
});

function show_warehouse_dialog(frm) {
    console.log('[DEBUG] show_warehouse_dialog called');
    
    const dialog = new frappe.ui.Dialog({
        title: __('Get Items from Warehouse'),
        fields: [
            {
                fieldtype: 'Table',
                fieldname: 'warehouse_table',
                label: __('Select Warehouses'),
                fields: [
                    {
                        fieldtype: 'Link',
                        fieldname: 'warehouse',
                        label: __('Warehouse'),
                        options: 'Warehouse',
                        reqd: 1,
                        in_list_view: 1,
                        get_query: function() {
                            return {
                                filters: {
                                    is_group: 0
                                }
                            };
                        }
                    }
                ],
                data: [{}] // Pre-populate with one empty row
            },
            {
                fieldtype: 'Check',
                fieldname: 'include_zero_qty',
                label: __('Include Zero Quantity Items'),
                default: 0
            },
            {
                fieldtype: 'Check',
                fieldname: 'clear_existing',
                label: __('Clear Existing Items'),
                default: 0
            }
        ],
        primary_action_label: __('Get Items'),
        primary_action: function(values) {
            console.log('[DEBUG] Warehouse dialog values:', values);
            
            if (!values.warehouse_table || values.warehouse_table.length === 0) {
                frappe.msgprint(__('Please add at least one warehouse'));
                return;
            }
            
            const warehouses = values.warehouse_table.map(row => row.warehouse).filter(Boolean);
            console.log('[DEBUG] Extracted warehouses:', warehouses);
            
            if (warehouses.length === 0) {
                frappe.msgprint(__('Please select at least one warehouse'));
                return;
            }
            
            frappe.show_alert({
                message: __('Fetching items from warehouses...'),
                indicator: 'blue'
            });
            
            const args = {
                source_type: 'Warehouse',
                source: warehouses[0], // For backward compatibility
                warehouses: warehouses,
                include_zero_qty: values.include_zero_qty,
                company: frm.doc.company
            };
            
            console.log('[DEBUG] Calling server with args:', args);
            
            frappe.call({
                method: 'stock_addon.stock_addon.doctype.stock_entry.stock_entry.get_items_from_source',
                args: args,
                callback: function(r) {
                    console.log('[DEBUG] Server response:', r);
                    
                    if (r.message && r.message.items) {
                        console.log('[DEBUG] Items received:', r.message.items.length);
                        console.log('[DEBUG] First few items:', r.message.items.slice(0, 3));
                        
                        if (values.clear_existing) {
                            frm.clear_table('items');
                        }
                        
                        r.message.items.forEach(item => {
                            if (!(flt(item.qty) > 0)) { return; }
                            const row = frm.add_child('items');
                            row.item_code = item.item_code;
                            row.item_name = item.item_name;
                            row.qty = item.qty;
                            row.uom = item.uom;
                            row.stock_uom = item.stock_uom;
                            row.conversion_factor = item.conversion_factor;
                            row.s_warehouse = item.warehouse;
                            
                            if (frm.doc.purpose === 'Material Transfer') {
                                row.t_warehouse = frm.doc.to_warehouse;
                            }
                            
                            row.basic_rate = item.rate || 0;
                            row.valuation_rate = item.valuation_rate || 0;
                            // Ensure Qty as per Stock UOM is populated
                            row.transfer_qty = flt(row.qty) * flt(row.conversion_factor || 1);
                        });
                        
                        frm.refresh_field('items');
                        dialog.hide();
                        
                        frappe.show_alert({
                            message: __('{0} items added successfully', [r.message.items.length]),
                            indicator: 'green'
                        });
                    } else {
                        console.log('[DEBUG] No items in response');
                        frappe.msgprint(__('No items found in selected warehouses'));
                    }
                },
                error: function(r) {
                    console.log('[DEBUG] Server error:', r);
                    frappe.msgprint(__('Error fetching items: {0}', [r.message || 'Unknown error']));
                }
            });
        }
    });
    
    dialog.show();
}

function show_sales_order_dialog(frm) {
    const dialog = new frappe.ui.Dialog({
        title: __('Get Items from Sales Order'),
        fields: [
            {
                fieldtype: 'Link',
                fieldname: 'sales_order',
                label: __('Sales Order'),
                options: 'Sales Order',
                reqd: 1,
                get_query: function() {
                    return {
                        filters: {
                            docstatus: 1,
                            status: ['not in', ['Stopped', 'Closed']]
                        }
                    };
                }
            },
            {
                fieldtype: 'Check',
                fieldname: 'clear_existing',
                label: __('Clear Existing Items'),
                default: 0
            }
        ],
        primary_action_label: __('Get Items'),
        primary_action: function(values) {
            if (!values.sales_order) {
                frappe.msgprint(__('Please select a sales order'));
                return;
            }
            
            frappe.show_alert({
                message: __('Fetching items from sales order...'),
                indicator: 'blue'
            });
            
            frappe.call({
                method: 'stock_addon.stock_addon.doctype.stock_entry.stock_entry.get_items_from_source',
                args: {
                    source_type: 'Sales Order',
                    source: values.sales_order,
                    include_zero_qty: false,
                    company: frm.doc.company
                },
                callback: function(r) {
                    if (r.message && r.message.items) {
                        if (values.clear_existing) {
                            frm.clear_table('items');
                        }
                        
                        r.message.items.forEach(item => {
                            if (!(flt(item.qty) > 0)) { return; }
                            const row = frm.add_child('items');
                            row.item_code = item.item_code;
                            row.item_name = item.item_name;
                            row.qty = item.qty;
                            row.uom = item.uom;
                            row.stock_uom = item.stock_uom;
                            row.conversion_factor = item.conversion_factor;
                            row.s_warehouse = item.warehouse;
                            
                            if (frm.doc.purpose === 'Material Transfer') {
                                row.t_warehouse = frm.doc.to_warehouse;
                            }
                            
                            row.basic_rate = item.rate || 0;
                            row.valuation_rate = item.valuation_rate || 0;
                            // Ensure Qty as per Stock UOM is populated
                            row.transfer_qty = flt(row.qty) * flt(row.conversion_factor || 1);
                        });
                        
                        frm.refresh_field('items');
                        dialog.hide();
                        
                        frappe.show_alert({
                            message: __('{0} items added successfully', [r.message.items.length]),
                            indicator: 'green'
                        });
                    } else {
                        frappe.msgprint(__('No items found in this sales order'));
                    }
                },
                error: function(r) {
                    frappe.msgprint(__('Error fetching items: {0}', [r.message || 'Unknown error']));
                }
            });
        }
    });
    
    dialog.show();
}

function show_warehouse_with_sales_order_dialog(frm) {
    const dialog = new frappe.ui.Dialog({
        title: __('Get Items from Warehouse with Sales Order'),
        fields: [
            {
                fieldtype: 'Link',
                fieldname: 'sales_order',
                label: __('Sales Order'),
                options: 'Sales Order',
                reqd: 1,
                get_query: function() {
                    return {
                        filters: {
                            docstatus: 1,
                            status: ['not in', ['Stopped', 'Closed']]
                        }
                    };
                }
            },
            {
                fieldtype: 'Table',
                fieldname: 'warehouse_table',
                label: __('Select Warehouses'),
                fields: [
                    {
                        fieldtype: 'Link',
                        fieldname: 'warehouse',
                        label: __('Warehouse'),
                        options: 'Warehouse',
                        reqd: 1,
                        in_list_view: 1,
                        get_query: function() {
                            return {
                                filters: {
                                    is_group: 0
                                }
                            };
                        }
                    }
                ],
                data: [{}] // Pre-populate with one empty row
            },
            {
                fieldtype: 'Check',
                fieldname: 'include_zero_qty',
                label: __('Include Zero Quantity Items'),
                default: 0
            },
            {
                fieldtype: 'Check',
                fieldname: 'clear_existing',
                label: __('Clear Existing Items'),
                default: 0
            }
        ],
        primary_action_label: __('Get Items'),
        primary_action: function(values) {
            if (!values.sales_order) {
                frappe.msgprint(__('Please select a sales order'));
                return;
            }
            
            if (!values.warehouse_table || values.warehouse_table.length === 0) {
                frappe.msgprint(__('Please add at least one warehouse'));
                return;
            }
            
            const warehouses = values.warehouse_table.map(row => row.warehouse).filter(Boolean);
            
            if (warehouses.length === 0) {
                frappe.msgprint(__('Please select at least one warehouse'));
                return;
            }
            
            frappe.show_alert({
                message: __('Fetching items from warehouses tagged to sales order...'),
                indicator: 'blue'
            });
            
            frappe.call({
                method: 'stock_addon.stock_addon.doctype.stock_entry.stock_entry.get_items_from_source',
                args: {
                    source_type: 'Warehouse with Sales Order',
                    source: warehouses[0], // For backward compatibility
                    warehouses: warehouses,
                    sales_order: values.sales_order,
                    include_zero_qty: values.include_zero_qty,
                    company: frm.doc.company
                },
                callback: function(r) {
                    if (r.message && r.message.items) {
                        if (values.clear_existing) {
                            frm.clear_table('items');
                        }
                        
                        r.message.items.forEach(item => {
                            if (!(flt(item.qty) > 0)) { return; }
                            const row = frm.add_child('items');
                            row.item_code = item.item_code;
                            row.item_name = item.item_name;
                            row.qty = item.qty;
                            row.uom = item.uom;
                            row.stock_uom = item.stock_uom;
                            row.conversion_factor = item.conversion_factor;
                            row.s_warehouse = item.warehouse;
                            
                            // Add sales order reference if available
                            if (item.sales_order) {
                                row.against_sales_order = item.sales_order;
                            }
                            
                            if (frm.doc.purpose === 'Material Transfer') {
                                row.t_warehouse = frm.doc.to_warehouse;
                            }
                            
                            row.basic_rate = item.rate || 0;
                            row.valuation_rate = item.valuation_rate || 0;
                            // Ensure Qty as per Stock UOM is populated
                            row.transfer_qty = flt(row.qty) * flt(row.conversion_factor || 1);
                        });
                        
                        frm.refresh_field('items');
                        dialog.hide();
                        
                        frappe.show_alert({
                            message: __('{0} items added successfully from sales order {1}', [r.message.items.length, values.sales_order]),
                            indicator: 'green'
                        });
                    } else {
                        frappe.msgprint(__('No items found in selected warehouses for this sales order'));
                    }
                },
                error: function(r) {
                    frappe.msgprint(__('Error fetching items: {0}', [r.message || 'Unknown error']));
                }
            });
        }
    });
    
    dialog.show();
}

function show_cost_center_dialog(frm) {
    console.log('[DEBUG] show_cost_center_dialog called');
    
    const dialog = new frappe.ui.Dialog({
        title: __('Get Items from Cost Center'),
        fields: [
            {
                fieldtype: 'Link',
                fieldname: 'cost_center',
                label: __('Cost Center (Optional)'),
                options: 'Cost Center',
                get_query: function() {
                    return {
                        filters: {
                            is_group: 0
                        }
                    };
                }
            },
            {
                fieldtype: 'Section Break',
                label: __('Warehouse Selection')
            },
            {
                fieldtype: 'Table',
                fieldname: 'warehouse_table',
                label: __('Select Warehouses'),
                reqd: 1,
                fields: [
                    {
                        fieldtype: 'Link',
                        fieldname: 'warehouse',
                        label: __('Warehouse'),
                        options: 'Warehouse',
                        reqd: 1,
                        in_list_view: 1,
                        get_query: function() {
                            return {
                                filters: {
                                    is_group: 0
                                }
                            };
                        }
                    }
                ]
            },
            {
                fieldtype: 'Section Break',
                label: __('Item Group Filter (Optional)')
            },
            {
                fieldtype: 'MultiSelectPills',
                fieldname: 'item_groups',
                label: __('Select Item Groups'),
                default: [], // Ensure it starts empty
                get_data: function(txt) {
                    console.log('[DEBUG] get_data called with txt:', txt);
                    return new Promise((resolve) => {
                        // Only fetch data if there's actual text input
                        if (!txt || txt.length < 1) {
                            resolve([]);
                            return;
                        }
                        
                        frappe.call({
                            method: 'frappe.client.get_list',
                            args: {
                                doctype: 'Item Group',
                                filters: {
                                    'name': ['like', `%${txt}%`] // Filter by search text
                                },
                                fields: ['name', 'is_group'],
                                limit_start: 0,
                                limit_page_length: 100, // Reduced limit
                                order_by: 'name asc'
                            }
                        }).then(r => {
                            console.log('[DEBUG] Item groups fetched for search:', txt, r.message);
                            
                            const results = (r.message || []).map(item => ({
                                value: item.name,
                                description: `${item.name} ${item.is_group ? '(Group)' : '(Item)'}`
                            }));
                            console.log('[DEBUG] Processed results:', results);
                            resolve(results);
                        }).catch((err) => {
                            console.error('[DEBUG] Error fetching item groups:', err);
                            resolve([]);
                        });
                    });
                }
            },
            {
                fieldtype: 'Section Break',
                label: __('Options')
            },
            {
                fieldtype: 'Check',
                fieldname: 'include_zero_qty',
                label: __('Include Zero Quantity Items'),
                default: 0
            },
            {
                fieldtype: 'Check',
                fieldname: 'clear_existing',
                label: __('Clear Existing Items'),
                default: 0
            }
        ],
        primary_action_label: __('Get Items'),
        primary_action: function(values) {
            console.log('[DEBUG] Cost Center dialog values:', values);
            console.log('[DEBUG] Raw item_groups value:', values.item_groups);
            console.log('[DEBUG] Type of item_groups:', typeof values.item_groups);
            
            if (!values.warehouse_table || values.warehouse_table.length === 0) {
                frappe.msgprint(__('Please select at least one warehouse'));
                return;
            }
            
            // Extract warehouses from table
            const warehouses = values.warehouse_table.map(row => row.warehouse).filter(Boolean);
            console.log('[DEBUG] Extracted warehouses:', warehouses);
            
            // Extract item groups - handle multiple formats
            let item_groups = [];
            if (values.item_groups) {
                console.log('[DEBUG] Processing item_groups...');
                
                if (typeof values.item_groups === 'string') {
                    console.log('[DEBUG] item_groups is string, trying to parse...');
                    try {
                        item_groups = JSON.parse(values.item_groups);
                        console.log('[DEBUG] Parsed from JSON:', item_groups);
                    } catch (e) {
                        console.log('[DEBUG] JSON parse failed, treating as single value');
                        item_groups = [values.item_groups];
                    }
                } else if (Array.isArray(values.item_groups)) {
                    console.log('[DEBUG] item_groups is already array');
                    item_groups = values.item_groups;
                } else if (typeof values.item_groups === 'object') {
                    console.log('[DEBUG] item_groups is object, extracting values...');
                    item_groups = Object.values(values.item_groups).filter(Boolean);
                }
            }
            console.log('[DEBUG] Final extracted item groups:', item_groups);
            
            // Get the primary action button and show loading state
            const primary_btn = dialog.$wrapper.find('.btn-primary');
            const original_text = primary_btn.text();
            const original_disabled = primary_btn.prop('disabled');
            
            // Show loading state
            primary_btn.prop('disabled', true).html('<i class="fa fa-spinner fa-spin"></i> ' + __('Loading...'));
            
            frappe.show_alert({
                message: __('Fetching items from warehouses...'),
                indicator: 'blue'
            });
            
            const args = {
                source_type: 'Cost Center',
                source: warehouses[0], // Pass first warehouse as source
                warehouses: warehouses,
                cost_center: values.cost_center || null,
                item_groups: item_groups,
                include_zero_qty: values.include_zero_qty,
                company: frm.doc.company
            };
            
            console.log('[DEBUG] Calling server with args:', args);
            
            frappe.call({
                method: 'stock_addon.stock_addon.doctype.stock_entry.stock_entry.get_items_from_source',
                args: args,
                callback: function(r) {
                    console.log('[DEBUG] Server response:', r);
                    
                    // Restore button state
                    primary_btn.prop('disabled', original_disabled).text(original_text);
                    
                    if (r.message && r.message.items) {
                        console.log('[DEBUG] Items received:', r.message.items.length);
                        console.log('[DEBUG] First few items:', r.message.items.slice(0, 3));
                        
                        if (values.clear_existing) {
                            frm.clear_table('items');
                        }
                        
                        r.message.items.forEach(item => {
                            if (!(flt(item.qty) > 0)) { return; }
                            const row = frm.add_child('items');
                            row.item_code = item.item_code;
                            row.item_name = item.item_name;
                            row.qty = item.qty;
                            row.uom = item.uom;
                            row.stock_uom = item.stock_uom;
                            row.conversion_factor = item.conversion_factor;
                            row.s_warehouse = item.warehouse;
                            
                            if (frm.doc.purpose === 'Material Transfer') {
                                row.t_warehouse = frm.doc.to_warehouse;
                            }
                            
                            row.basic_rate = item.rate || 0;
                            row.valuation_rate = item.valuation_rate || 0;
                            // Ensure Qty as per Stock UOM is populated
                            row.transfer_qty = flt(row.qty) * flt(row.conversion_factor || 1);
                        });
                        
                        frm.refresh_field('items');
                        dialog.hide();
                        
                        frappe.show_alert({
                            message: __('{0} items added successfully', [r.message.items.length]),
                            indicator: 'green'
                        });
                    } else {
                        frappe.msgprint(__('No items found'));
                    }
                },
                error: function(r) {
                    console.error('[DEBUG] Server error:', r);
                    
                    // Restore button state on error
                    primary_btn.prop('disabled', original_disabled).text(original_text);
                    
                    frappe.msgprint(__('Error fetching items: {0}', [r.message || 'Unknown error']));
                }
            });
        }
    });
    
    dialog.show();
}

// Function to fetch and set UOM for a row
function fetch_and_set_uom(frm, cdt, cdn, is_item_code_trigger) {
    try {
        var row = locals[cdt][cdn];
        
        if (!row) return;
        
        // Only process if item_code exists and UOM fields are missing
        if (!row.item_code || (row.uom && row.stock_uom)) {
            return;
        }
        
        console.log('[STOCK ENTRY DEBUG] Fetching UOM for item:', row.item_code, 'trigger:', is_item_code_trigger ? 'item_code' : 'other');
        
        frappe.call({
            method: 'frappe.client.get_value',
            args: {
                doctype: 'Item',
                filters: { name: row.item_code },
                fieldname: ['stock_uom']
            },
            callback: function(r) {
                try {
                    var current_row = locals[cdt][cdn];
                    if (!current_row) {
                        console.log('[STOCK ENTRY DEBUG] Row deleted during fetch');
                        return;
                    }
                    
                    if (r.message && r.message.stock_uom) {
                        if (!current_row.stock_uom) {
                            console.log('[STOCK ENTRY DEBUG] Setting stock_uom to:', r.message.stock_uom);
                            frappe.model.set_value(cdt, cdn, 'stock_uom', r.message.stock_uom);
                        }
                        if (!current_row.uom) {
                            console.log('[STOCK ENTRY DEBUG] Setting uom to:', r.message.stock_uom);
                            frappe.model.set_value(cdt, cdn, 'uom', r.message.stock_uom);
                        }
                    } else {
                        console.log('[STOCK ENTRY DEBUG] No UOM returned for item:', row.item_code);
                    }
                    
                    // Retry after a delay if UOM is still missing (for timing issues)
                    setTimeout(function() {
                        var check_row = locals[cdt][cdn];
                        if (check_row && check_row.item_code && (!check_row.uom || !check_row.stock_uom)) {
                            console.log('[STOCK ENTRY DEBUG] Retrying UOM fetch for item:', check_row.item_code);
                            fetch_and_set_uom(frm, cdt, cdn, false);
                        }
                    }, 300);
                } catch (e) {
                    console.error('[STOCK ENTRY DEBUG] Error in callback:', e);
                }
            },
            error: function(r) {
                console.error('[STOCK ENTRY DEBUG] Error fetching UOM:', r);
                
                // Check if error is due to inactive item - skip this row
                if (r.message && r.message.indexOf && r.message.indexOf('is not active or end of life') !== -1) {
                    console.log('[STOCK ENTRY DEBUG] Item is inactive, skipping:', row.item_code);
                    return; // Don't retry for inactive items
                }
                
                // Retry on other errors
                setTimeout(function() {
                    var check_row = locals[cdt][cdn];
                    if (check_row && check_row.item_code && (!check_row.uom || !check_row.stock_uom)) {
                        console.log('[STOCK ENTRY DEBUG] Retrying UOM fetch after error for item:', check_row.item_code);
                        fetch_and_set_uom(frm, cdt, cdn, false);
                    }
                }, 300);
            }
        });
    } catch (e) {
        console.error('[STOCK ENTRY DEBUG] Error in fetch_and_set_uom:', e);
    }
}

// Handle item_code changes to auto-populate UOM fields
frappe.ui.form.on('Stock Entry Detail', {
    item_code: function(frm, cdt, cdn) {
        try {
            var row = locals[cdt][cdn];
            
            // Check if row exists
            if (!row) {
                return;
            }
            
            console.log('[STOCK ENTRY DEBUG] item_code triggered:', {
                item_code: row.item_code,
                uom: row.uom,
                stock_uom: row.stock_uom,
                qty: row.qty,
                stock_entry_type: frm.doc.stock_entry_type
            });
            
            // Set qty to 1 if not set
            if (!row.qty || row.qty == 0) {
                frappe.model.set_value(cdt, cdn, 'qty', 1);
            }
            
            // Set allow_zero_valuation_rate based on s_warehouse for "Stitched Products"
            if (frm.doc.stock_entry_type === 'Stitched Products') {
                // Check the current row's s_warehouse
                var current_row = locals[cdt][cdn];
                if (current_row) {
                    if (current_row.s_warehouse && current_row.s_warehouse !== "") {
                        // If s_warehouse is present (has warehouse), set allow_zero_valuation_rate = 0
                        frappe.model.set_value(cdt, cdn, 'allow_zero_valuation_rate', 0);
                    } else {
                        // If s_warehouse is empty, set allow_zero_valuation_rate = 1
                        frappe.model.set_value(cdt, cdn, 'allow_zero_valuation_rate', 1);
                    }
                }
            }
            
            // Fetch UOM - will be called again with delay
            if (row.item_code && (!row.uom || !row.stock_uom)) {
                fetch_and_set_uom(frm, cdt, cdn, true);
            }
        } catch (e) {
            console.error('[STOCK ENTRY DEBUG] Error in item_code handler:', e);
            // Continue processing - don't block other items
        }
    },
    
    // Also trigger on uom change to catch paste scenarios
    uom: function(frm, cdt, cdn) {
        try {
            var row = locals[cdt][cdn];
            if (row && row.item_code && (!row.uom || !row.stock_uom)) {
                // Use a small delay to let the async paste complete
                setTimeout(function() {
                    fetch_and_set_uom(frm, cdt, cdn, false);
                }, 200);
            }
        } catch (e) {
            console.error('[STOCK ENTRY DEBUG] Error in uom handler:', e);
        }
    },
    
    // Handle s_warehouse changes for "Stitched Products"
    s_warehouse: function(frm, cdt, cdn) {
        try {
            if (frm.doc.stock_entry_type === 'Stitched Products') {
                var row = locals[cdt][cdn];
                if (row) {
                    if (row.s_warehouse && row.s_warehouse !== "") {
                        // If s_warehouse is present (has warehouse), set allow_zero_valuation_rate = 0
                        frappe.model.set_value(cdt, cdn, 'allow_zero_valuation_rate', 0);
                    } else {
                        // If s_warehouse is empty, set allow_zero_valuation_rate = 1
                        frappe.model.set_value(cdt, cdn, 'allow_zero_valuation_rate', 1);
                    }
                }
            }
        } catch (e) {
            console.error('[STOCK ENTRY DEBUG] Error in s_warehouse handler:', e);
        }
    },
    
    // Trigger when entire row is refreshed
    refresh: function(frm, cdt, cdn) {
        try {
            var row = locals[cdt][cdn];
            if (row && row.item_code && (!row.uom || !row.stock_uom)) {
                fetch_and_set_uom(frm, cdt, cdn, false);
            }
            
            // Update allow_zero_valuation_rate when row is refreshed (for "Stitched Products")
            if (frm.doc.stock_entry_type === 'Stitched Products' && row) {
                if (row.s_warehouse && row.s_warehouse !== "") {
                    // If s_warehouse is present (has warehouse), set allow_zero_valuation_rate = 0
                    frappe.model.set_value(cdt, cdn, 'allow_zero_valuation_rate', 0);
                } else {
                    // If s_warehouse is empty, set allow_zero_valuation_rate = 1
                    frappe.model.set_value(cdt, cdn, 'allow_zero_valuation_rate', 1);
                }
            }
        } catch (e) {
            console.error('[STOCK ENTRY DEBUG] Error in refresh handler:', e);
        }
    }
});
// ─── Sales Price auto-population + totals (stock_addon) ─────────────────────
// custom_sales_price        : unit selling price per STOCK UoM (auto-fetched
//                             from the default selling Item Price; editable).
// custom_total_amount_...   : sales price × stock qty for the row (read-only).
// Header custom_total_qty / custom_total_sales_amount: document roll-ups.
// The server hook (doc_events/stock_entry.py) recomputes the same numbers on
// save, so these are a live preview with an authoritative server pass.

function sa_row_stock_qty(row) {
    var transfer = flt(row.transfer_qty);
    if (transfer) return transfer;
    return flt(row.qty) * (flt(row.conversion_factor) || 1);
}

function sa_update_row_total(frm, cdt, cdn) {
    var row = locals[cdt][cdn];
    if (!row) return;
    var total = flt(row.custom_sales_price) * sa_row_stock_qty(row);
    if (flt(row.custom_total_amount_sales_price) !== total) {
        frappe.model.set_value(cdt, cdn, 'custom_total_amount_sales_price', total);
    }
    sa_update_doc_totals(frm);
}

function sa_update_doc_totals(frm) {
    var total_qty = 0;
    var total_sales = 0;
    (frm.doc.items || []).forEach(function (r) {
        total_qty += flt(r.qty);
        total_sales += flt(r.custom_total_amount_sales_price);
    });
    frm.set_value('custom_total_qty', total_qty);
    frm.set_value('custom_total_sales_amount', total_sales);
}

function sa_fetch_sales_price(frm, cdt, cdn) {
    var row = locals[cdt][cdn];
    if (!row || !row.item_code) return;
    // Don't clobber a price the user (or the MR copy) already set.
    if (flt(row.custom_sales_price)) {
        sa_update_row_total(frm, cdt, cdn);
        return;
    }
    frappe.call({
        method: 'stock_addon.stock_addon.doctype.inventory_counting.inventory_counting.get_selling_rate',
        args: { item_code: row.item_code },
        callback: function (r) {
            var rate = flt(r.message);
            if (rate && locals[cdt] && locals[cdt][cdn]) {
                frappe.model.set_value(cdt, cdn, 'custom_sales_price', rate);
            }
            sa_update_row_total(frm, cdt, cdn);
        }
    });
}

frappe.ui.form.on('Stock Entry Detail', {
    item_code: function (frm, cdt, cdn) {
        // small delay so ERPNext's own item fetch (uom/conversion) lands first
        setTimeout(function () { sa_fetch_sales_price(frm, cdt, cdn); }, 400);
    },
    qty: function (frm, cdt, cdn) {
        sa_update_row_total(frm, cdt, cdn);
    },
    uom: function (frm, cdt, cdn) {
        setTimeout(function () { sa_update_row_total(frm, cdt, cdn); }, 300);
    },
    conversion_factor: function (frm, cdt, cdn) {
        sa_update_row_total(frm, cdt, cdn);
    },
    transfer_qty: function (frm, cdt, cdn) {
        sa_update_row_total(frm, cdt, cdn);
    },
    custom_sales_price: function (frm, cdt, cdn) {
        sa_update_row_total(frm, cdt, cdn);
    },
    items_remove: function (frm) {
        sa_update_doc_totals(frm);
    }
});

// Recompute every row total + the document totals in one pass. Values are
// written straight onto the docs (no set_value) so opening an old draft does
// not mark the form dirty — the server hook persists them on the next save.
function sa_recalc_all(frm) {
    var total_qty = 0;
    var total_sales = 0;
    (frm.doc.items || []).forEach(function (row) {
        row.custom_total_amount_sales_price =
            flt(row.custom_sales_price) * sa_row_stock_qty(row);
        total_qty += flt(row.qty);
        total_sales += flt(row.custom_total_amount_sales_price);
    });
    frm.doc.custom_total_qty = total_qty;
    frm.doc.custom_total_sales_amount = total_sales;
    frm.refresh_field('items');
    frm.refresh_field('custom_total_qty');
    frm.refresh_field('custom_total_sales_amount');
}

frappe.ui.form.on('Stock Entry', {
    refresh: function (frm) {
        // Drafts saved before the server hook was wired show stale 0.00
        // totals — fix the display as soon as the form opens.
        if (frm.doc.docstatus === 0) sa_recalc_all(frm);
    },
    validate: function (frm) {
        sa_recalc_all(frm);
    }
});

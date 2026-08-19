// =====================================================================
// Neotec OrderFlow — Sales Order client bundle
// Manual warehouse allocator for DIRECTLY-ENTERED orders (the
// quotation path is handled by the Quotation generator). Splits the
// selected item's row warehouse-wise via a stepper dialog. Strict
// mirror mode: rows = exactly the dialog selections, no row without a
// warehouse. Gated by settings (enable_so_allocator).
// =====================================================================

const NOF_SO_PATH = 'neotec_orderflow.api.so_generator';

const NOF_SO_COPY_FIELDS = [
    'item_code', 'item_name', 'description', 'item_group', 'brand', 'image',
    'uom', 'stock_uom', 'conversion_factor',
    'price_list_rate', 'base_price_list_rate',
    'margin_type', 'margin_rate_or_amount', 'rate_with_margin',
    'discount_percentage', 'discount_amount',
    'rate', 'base_rate',
    'item_tax_template', 'delivery_date',
    'weight_per_unit', 'weight_uom',
    'prevdoc_docname', 'custom_item_reservation'
];

function nof_so_settings(frm) {
    if (frm.__nof_settings) return Promise.resolve(frm.__nof_settings);
    return frappe.call({ method: `${NOF_SO_PATH}.get_client_settings` })
        .then(r => { frm.__nof_settings = r.message || {}; return frm.__nof_settings; });
}

frappe.ui.form.on('Sales Order Item', {
    item_code: function (frm, cdt, cdn) {
        if (frm.__nof_suppress) return;
        if (!frm.__nof_ready) return;   // form still loading — never auto-open
        if (frm.doc.docstatus !== 0) return;
        const row = locals[cdt][cdn];
        if (!row.item_code) return;
        // Rows mapped from a quotation are handled by the Quotation
        // generator; only trigger for fresh manual rows.
        if (row.prevdoc_docname) return;
        nof_so_settings(frm).then(s => {
            if (s.enabled && s.enable_so_allocator) {
                nof_so_open_allocator(frm, cdt, cdn);
            }
        });
    }
});

frappe.ui.form.on('Sales Order', {
    onload_post_render: function (frm) {
        frm.__nof_ready = true;
    },
    refresh: function (frm) {
        if (frm.doc.docstatus !== 0) return;
        frm.__nof_settings = null;   // always honor the latest saved settings
        nof_so_settings(frm).then(s => {
            if (!(s.enabled && s.enable_so_allocator)) return;
            frm.fields_dict.items.grid.add_custom_button(__('Allocate Warehouses'), () => {
                const selected = frm.fields_dict.items.grid.get_selected_children();
                if (!selected.length) {
                    frappe.msgprint(__('Tick a row in the Items table first.'));
                    return;
                }
                const r = selected[0];
                if (!r.item_code) {
                    frappe.msgprint(__('Selected row has no Item Code.'));
                    return;
                }
                nof_so_open_allocator(frm, r.doctype, r.name);
            });
        });
    }
});

function nof_so_fetch_stock(item_code) {
    return frappe.call({
        method: 'frappe.client.get_list',
        args: {
            doctype: 'Bin',
            filters: { item_code: item_code },
            fields: ['warehouse', 'actual_qty', 'reserved_qty', 'reserved_stock'],
            limit_page_length: 500
        }
    }).then(r => (r.message || [])
        .map(b => ({
            warehouse: b.warehouse,
            available_qty: flt(b.actual_qty) - flt(b.reserved_qty) - flt(b.reserved_stock),
            select_qty: 0
        }))
        .filter(b => b.available_qty > 0)
        .sort((a, b) => b.available_qty - a.available_qty));
}

function nof_so_open_allocator(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    nof_so_fetch_stock(row.item_code).then(stock => {
        if (!stock.length) {
            frappe.msgprint(__('No available stock for {0} in any warehouse.', [row.item_code]));
            return;
        }
        let need = flt(row.qty) || 1;
        stock.forEach(s => { s.select_qty = Math.min(need, s.available_qty); need -= s.select_qty; });

        const d = new frappe.ui.Dialog({
            title: __('Allocate Stock — {0}', [row.item_code]),
            size: 'large',
            fields: [
                {
                    fieldtype: 'HTML', fieldname: 'head',
                    options: `<div style="margin:4px 0 6px;">
                        <b>${frappe.utils.escape_html(row.item_code)}</b>
                        &nbsp;\u2014&nbsp;${__('Ordering Qty')}: <b>${format_number(flt(row.qty) || 1)}</b>
                        &nbsp;|&nbsp;${__('Total Available')}:
                        <b>${format_number(stock.reduce((s, r) => s + r.available_qty, 0))}</b>
                        </div>`
                },
                {
                    fieldname: 'alloc', fieldtype: 'Table',
                    cannot_add_rows: true, in_place_edit: true, data: stock,
                    fields: [
                        { fieldname: 'warehouse', label: __('Warehouse'), fieldtype: 'Link',
                          options: 'Warehouse', in_list_view: 1, read_only: 1, columns: 4 },
                        { fieldname: 'available_qty', label: __('Available'), fieldtype: 'Float',
                          in_list_view: 1, read_only: 1, columns: 3 },
                        { fieldname: 'select_qty', label: __('Allocate'), fieldtype: 'Float',
                          in_list_view: 1, columns: 3 }
                    ]
                }
            ],
            primary_action_label: __('Apply Allocation'),
            primary_action: () => {
                const values = d.get_values();
                const allocations = (values.alloc || [])
                    .filter(r => flt(r.select_qty) > 0)
                    .map(r => ({ warehouse: r.warehouse, qty: flt(r.select_qty),
                                 available_qty: flt(r.available_qty) }));
                if (!allocations.length) {
                    frappe.msgprint(__('Enter a quantity against at least one warehouse.'));
                    return;
                }
                const over = allocations.find(a => a.qty > a.available_qty);
                if (over) {
                    frappe.msgprint(__('Allocation for {0} exceeds available ({1}).',
                        [over.warehouse, format_number(over.available_qty)]));
                    return;
                }
                const total = allocations.reduce((s, a) => s + a.qty, 0);
                const apply = () => { d.hide(); nof_so_apply(frm, cdt, cdn, allocations); };
                if (total < (flt(row.qty) || 0)) {
                    frappe.confirm(
                        __('Allocated {0} of {1} — order qty will become {0}. Continue?',
                            [format_number(total), format_number(row.qty)]),
                        apply
                    );
                } else apply();
            },
            secondary_action_label: __('Cancel')
        });
        d.show();
    });
}

async function nof_so_apply(frm, cdt, cdn, allocations) {
    frm.__nof_suppress = true;
    try {
        const src = locals[cdt][cdn];
        const quoted_rate = flt(src.rate);
        const quoted_plr = flt(src.price_list_rate);

        const snapshot = {};
        NOF_SO_COPY_FIELDS.forEach(f => {
            if (src[f] !== undefined) snapshot[f] = src[f];
        });

        const first = allocations[0];
        await frappe.model.set_value(cdt, cdn, 'warehouse', first.warehouse);
        await frappe.model.set_value(cdt, cdn, 'qty', first.qty);
        if (flt(src.rate) !== quoted_rate) {
            await frappe.model.set_value(cdt, cdn, 'rate', quoted_rate);
        }
        frappe.model.set_value(cdt, cdn, 'custom_available_qty', first.available_qty);

        for (let i = 1; i < allocations.length; i++) {
            const a = allocations[i];
            const nr = frm.add_child('items');
            Object.keys(snapshot).forEach(f => { nr[f] = snapshot[f]; });
            await frappe.model.set_value(nr.doctype, nr.name, 'warehouse', a.warehouse);
            await frappe.model.set_value(nr.doctype, nr.name, 'qty', a.qty);
            if (flt(nr.rate) !== quoted_rate) {
                await frappe.model.set_value(nr.doctype, nr.name, 'rate', quoted_rate);
            }
            if (quoted_plr && flt(nr.price_list_rate) !== quoted_plr) {
                nr.price_list_rate = quoted_plr;
            }
            frappe.model.set_value(nr.doctype, nr.name, 'custom_available_qty', a.available_qty);
        }

        frm.refresh_field('items');
        frappe.show_alert({
            message: __('{0} allocated across {1} warehouse(s)',
                [src.item_code, allocations.length]),
            indicator: 'green'
        }, 5);
    } finally {
        frm.__nof_suppress = false;
    }
}

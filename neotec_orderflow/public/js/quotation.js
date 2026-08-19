// =====================================================================
// Neotec OrderFlow — Quotation client bundle
// State-grouped stock availability matrix (with in-window partial SO
// creation on submitted quotations) + warehouse-wise SO generator with
// compliance pre-flight. All features gated by settings.
// =====================================================================

const NOF_PATH = 'neotec_orderflow.api.so_generator';

function nof_settings(frm) {
    if (frm.__nof_settings) return Promise.resolve(frm.__nof_settings);
    return frappe.call({ method: `${NOF_PATH}.get_client_settings` })
        .then(r => { frm.__nof_settings = r.message || {}; return frm.__nof_settings; });
}

frappe.ui.form.on('Quotation Item', {
    item_code: function (frm, cdt, cdn) {
        if (frm.doc.docstatus !== 0) return;
        // Never auto-open while the form is still loading / rows are being
        // mapped programmatically (e.g. from Opportunity) — only for a live
        // user selection on a rendered form.
        if (!frm.__nof_ready) return;
        const row = locals[cdt][cdn];
        if (!row.item_code) return;
        nof_settings(frm).then(s => {
            if (cint(s.enabled) && cint(s.enable_stock_viewer)
                && cint(s.viewer_open_on_item)) {
                nof_show_stock_matrix(frm, [row]);
            }
        });
    }
});

frappe.ui.form.on('Quotation', {
    onload_post_render: function (frm) {
        frm.__nof_ready = true;
    },
    refresh: function (frm) {
        // Re-read settings on every form refresh so a toggle the user just
        // saved in Neotec OrderFlow Settings takes effect immediately —
        // no stale per-form cache.
        frm.__nof_settings = null;
        nof_settings(frm).then(s => {
            if (!s.enabled) return;

            if (s.enable_stock_viewer) {
                frm.add_custom_button(__('Stock Availability'), () => {
                    const rows = (frm.doc.items || []).filter(r => r.item_code);
                    if (!rows.length) {
                        frappe.msgprint(__('No items on this quotation.'));
                        return;
                    }
                    nof_show_stock_matrix(frm, rows);
                });
            }

            if (s.enable_so_generator
                && frm.doc.docstatus === 1
                && !['Ordered', 'Cancelled', 'Expired'].includes(frm.doc.status)) {
                frm.add_custom_button(__('Create Sales Order (Warehouse-wise)'), () => {
                    nof_open_generator(frm, s);
                }).addClass('btn-primary');
            }
        });
    }
});

// =====================================================================
// STOCK MATRIX — items down; STATE sections across, each state's
// warehouses beneath it. EVERY warehouse holding stock is shown,
// including Transit warehouses (info-only when settings exclude them
// from allocation). The customer's state (from the quotation address)
// is highlighted as PREFERRED — indication only, user decides. On a
// SUBMITTED quotation the orderable cells become qty inputs and a
// "Create Sales Order" button issues partial SOs against the remaining
// balance (server re-validates everything).
// =====================================================================
function nof_show_stock_matrix(frm, rows) {
    const item_codes = [...new Set(rows.map(r => r.item_code))];
    frappe.call({
        method: `${NOF_PATH}.get_stock_matrix`,
        args: {
            item_codes: item_codes,
            quotation: frm.doc.__islocal ? null : frm.doc.name,
            context: {
                quotation_to: frm.doc.quotation_to,
                party_name: frm.doc.party_name,
                shipping_address_name: frm.doc.shipping_address_name,
                customer_address: frm.doc.customer_address
            }
        },
        freeze: true,
        freeze_message: __('Loading stock...'),
        callback: (r) => {
            const data = r.message;
            if (!data || !(data.state_groups || []).length) {
                frappe.msgprint(__('No stock in any warehouse for the selected item(s).'));
                return;
            }
            nof_settings(frm).then(s => {
                const can_order = frm.doc.docstatus === 1
                    && cint(s.enable_so_generator)
                    && !['Ordered', 'Cancelled', 'Expired'].includes(frm.doc.status);
                nof_render_matrix(frm, rows, data, s, can_order);
            });
        }
    });
}

function nof_render_matrix(frm, rows, data, settings, can_order) {
    const esc = frappe.utils.escape_html;
    const short = (w) => esc(w.split(' - ')[0]);
    const groups = data.state_groups;   // [{state, warehouses:[{name, info_only}]}]
    const avail = data.avail || {};
    const ordered_map = data.ordered || {};

    // customer-state preference (indication only)
    const cust_state = (data.customer_state || '').trim().toLowerCase();
    const is_pref = (g) => !!cust_state && g.state.trim().toLowerCase() === cust_state;
    const any_pref = groups.some(is_pref);
    const any_info_only = groups.some(g => g.warehouses.some(w => w.info_only));

    // quoted qty per item (sum across duplicate rows on the quotation)
    const quoted = {};
    (frm.doc.items || []).forEach(d => {
        if (d.item_code) quoted[d.item_code] = (quoted[d.item_code] || 0) + flt(d.qty);
    });

    const uniq_items = [];
    const seen = new Set();
    rows.forEach(row => {
        if (seen.has(row.item_code)) return;
        seen.add(row.item_code);
        uniq_items.push(row);
    });

    const STATE_BG = ['rgba(0,0,0,0.025)', 'transparent'];
    const PREF_BG = 'rgba(40,167,69,0.08)';
    const g_bg = (g, gi) => is_pref(g) ? PREF_BG : STATE_BG[gi % 2];

    // ---------------- header (two rows: states, then warehouses) -------
    let html = `<div style="overflow-x:auto; max-height:65vh; overflow-y:auto;">
    <table class="table table-bordered table-sm" style="margin:0; min-width:680px;">
        <thead>
        <tr>
            <th rowspan="2" style="position:sticky;left:0;z-index:2;
                background:var(--fg-color,#fff);min-width:160px;vertical-align:bottom;">
                ${__('Item')}</th>
            <th rowspan="2" class="text-right" style="min-width:56px;vertical-align:bottom;">
                ${__('Qty')}</th>
            <th rowspan="2" class="text-right" style="min-width:60px;vertical-align:bottom;">
                ${__('Ordered')}</th>
            <th rowspan="2" class="text-right" style="min-width:60px;vertical-align:bottom;">
                ${__('Balance')}</th>
            <th rowspan="2" class="text-center" style="min-width:86px;vertical-align:bottom;">
                ${__('Status')}</th>`;

    groups.forEach((g, gi) => {
        const orderable_count = g.warehouses.filter(w => !w.info_only).length;
        const span = g.warehouses.length + (orderable_count > 1 ? 1 : 0);
        const pref = is_pref(g);
        html += `<th colspan="${span}" class="text-center"
            style="background:${g_bg(g, gi)};
                   border-bottom:2px solid ${pref ? '#28a745' : 'var(--gray-400,#a8b1b9)'};">
            ${pref ? '\u2605 ' : ''}${esc(g.state)}
            ${pref ? `<span style="display:inline-block;margin-left:6px;padding:0 7px;
                border-radius:9px;font-size:10px;font-weight:normal;color:#fff;
                background:#28a745;vertical-align:middle;">${__('Customer State')}</span>` : ''}
            </th>`;
    });
    html += `<th rowspan="2" class="text-right" style="min-width:64px;vertical-align:bottom;">
        <b>${__('Total')}</b></th></tr><tr>`;

    groups.forEach((g, gi) => {
        const orderable_count = g.warehouses.filter(w => !w.info_only).length;
        g.warehouses.forEach(w => {
            html += `<th class="text-right" title="${esc(w.name)}"
                style="min-width:${can_order && !w.info_only ? 84 : 64}px;
                       background:${g_bg(g, gi)};${w.info_only ? 'font-style:italic;' : ''}">
                ${short(w.name)}${w.info_only
                    ? `<div class="text-muted" style="font-size:9px;font-weight:normal;">
                        ${__('Transit \u2014 info only')}</div>` : ''}</th>`;
        });
        if (orderable_count > 1) {
            html += `<th class="text-right"
                style="min-width:64px;background:${g_bg(g, gi)};font-style:italic;">
                ${__('State Qty')}</th>`;
        }
    });
    html += `</tr></thead><tbody>`;

    // ---------------- body ---------------------------------------------
    uniq_items.forEach(row => {
        const need = flt(quoted[row.item_code] != null ? quoted[row.item_code] : row.qty) || 0;
        const already = flt(ordered_map[row.item_code] || 0);
        const balance = Math.max(0, need - already);
        let total = 0, transit_total = 0, cells = '';

        groups.forEach((g, gi) => {
            const orderable_count = g.warehouses.filter(w => !w.info_only).length;
            let state_total = 0;
            g.warehouses.forEach(w => {
                const q = flt(avail[`${row.item_code}|${w.name}`] || 0);
                if (w.info_only) {
                    transit_total += q;
                    cells += `<td class="text-right"
                        style="background:${g_bg(g, gi)};font-style:italic;
                               ${q > 0 ? 'color:#8d99a6;' : 'color:#bbb;'}">
                        ${q > 0 ? format_number(q) : '\u2013'}</td>`;
                    return;
                }
                total += q; state_total += q;
                if (can_order && q > 0) {
                    cells += `<td class="text-right" style="background:${g_bg(g, gi)};">
                        <div class="text-muted" style="font-size:10px;">
                            ${__('Avl')}: ${format_number(q)}</div>
                        <input type="number" class="form-control input-sm nof-alloc"
                            data-item="${esc(row.item_code)}" data-wh="${esc(w.name)}"
                            data-max="${q}" min="0" max="${q}" step="any" value="0"
                            style="width:76px;text-align:right;padding:2px 6px;
                                   height:24px;display:inline-block;">
                    </td>`;
                } else {
                    cells += `<td class="text-right"
                        style="background:${g_bg(g, gi)};${q > 0 ? '' : 'color:#bbb;'}">
                        ${q > 0 ? format_number(q) : '\u2013'}</td>`;
                }
            });
            if (orderable_count > 1) {
                cells += `<td class="text-right"
                    style="background:${g_bg(g, gi)};font-style:italic;">
                    ${state_total > 0 ? format_number(state_total) : '\u2013'}</td>`;
            }
        });

        let badge, bg, hint = '';
        if (total >= balance && total > 0 && balance > 0) { badge = __('Available'); bg = '#28a745'; }
        else if (balance <= 0 && already > 0) { badge = __('Ordered'); bg = '#6c757d'; }
        else if (total > 0) {
            badge = __('Partial'); bg = '#e6a817';
            hint = `<div class="text-muted" style="font-size:10px;">
                ${__('short by')} ${format_number(balance - total)}</div>`;
        } else { badge = __('Out of Stock'); bg = '#dc3545'; }

        html += `<tr>
            <td style="position:sticky;left:0;z-index:1;background:var(--fg-color,#fff);">
                <b>${esc(row.item_code)}</b>
                ${row.item_name && row.item_name !== row.item_code
                    ? `<div class="text-muted" style="font-size:10px;">
                        ${esc(row.item_name)}</div>` : ''}
            </td>
            <td class="text-right">${format_number(need)}</td>
            <td class="text-right">${already ? format_number(already) : '\u2013'}</td>
            <td class="text-right"><b style="${balance > 0 ? 'color:#e6a817;' : ''}">
                ${format_number(balance)}</b></td>
            <td class="text-center">
                <span style="padding:1px 8px;border-radius:10px;font-size:11px;color:#fff;
                    background:${bg};">${badge}</span>${hint}
            </td>
            ${cells}
            <td class="text-right"><b>${format_number(total)}</b>
                ${transit_total > 0
                    ? `<div class="text-muted" style="font-size:10px;font-style:italic;">
                        +${format_number(transit_total)} ${__('in transit')}</div>` : ''}
            </td>
        </tr>`;
    });

    // ---------------- footer notes --------------------------------------
    let notes = '';
    if (any_pref) {
        notes += `<div style="margin-top:6px;font-size:12px;">
            <span style="color:#28a745;">\u2605</span>
            ${__('Customer is located in {0} — warehouses in this state are indicated as preferred for delivery. The final selection is yours.',
                [`<b>${esc(data.customer_state)}</b>`])}</div>`;
    } else if (cust_state) {
        notes += `<div class="text-muted" style="margin-top:6px;font-size:12px;">
            ${__('Customer state: {0} — no stocked warehouse in this state.',
                [`<b>${esc(data.customer_state)}</b>`])}</div>`;
    }
    if (any_info_only) {
        notes += `<div class="text-muted" style="margin-top:4px;font-size:12px;font-style:italic;">
            ${__('Transit warehouses are shown for information only and are excluded from the orderable Total (per settings).')}</div>`;
    }
    notes += `<div class="text-muted small" style="margin-top:4px;">
        ${can_order
            ? __('Enter the quantity to order against each warehouse — the balance is maintained per item. Server re-validates stock and balance on creation.')
            : __('Internal view — printed quotation discloses availability status only (per settings).')}
    </div>`;

    html += `</tbody></table></div>${notes}`;

    // ---------------- dialog --------------------------------------------
    const fields = [{ fieldtype: 'HTML', fieldname: 'stock_html', options: html }];
    if (can_order) {
        fields.push(
            { fieldtype: 'Section Break' },
            { fieldname: 'delivery_date', fieldtype: 'Date', label: __('Delivery Date'),
              reqd: 1, default: data.default_delivery_date },
            { fieldtype: 'Column Break' },
            { fieldname: 'group_mode', fieldtype: 'Select', label: __('Grouping'),
              options: 'single\nwarehouse\nstate',
              default: ['single', 'warehouse', 'state'].includes(data.default_grouping)
                  ? data.default_grouping : 'single',
              description: __('single = one SO; warehouse = one SO per warehouse; state = one SO per state') }
        );
    }

    const d = new frappe.ui.Dialog({
        title: uniq_items.length === 1
            ? __('Stock Availability — {0}', [uniq_items[0].item_code])
            : __('Stock Availability ({0} items)', [uniq_items.length]),
        size: 'extra-large',
        fields: fields,
        primary_action_label: can_order ? __('Create Sales Order') : __('Close'),
        primary_action: () => {
            if (!can_order) { d.hide(); return; }
            nof_matrix_create_so(frm, d, uniq_items, quoted, ordered_map);
        }
    });
    if (can_order) {
        d.set_secondary_action_label(__('Close'));
        d.set_secondary_action(() => d.hide());
    }
    d.show();
}

function nof_matrix_create_so(frm, d, uniq_items, quoted, ordered_map) {
    const allocations = [];
    const per_item = {};
    let bad = null;

    d.$wrapper.find('input.nof-alloc').each(function () {
        const $i = $(this);
        const qty = flt($i.val());
        if (qty <= 0) return;
        const max = flt($i.attr('data-max'));
        const item = $i.attr('data-item'), wh = $i.attr('data-wh');
        if (qty > max + 0.0001) {
            bad = __('{0} / {1}: entered {2} exceeds available {3}.',
                [item, wh, format_number(qty), format_number(max)]);
            return false;
        }
        per_item[item] = (per_item[item] || 0) + qty;
        allocations.push({ item_code: item, warehouse: wh, qty: qty });
    });

    if (bad) { frappe.msgprint(bad); return; }
    if (!allocations.length) {
        frappe.msgprint(__('Enter a quantity against at least one warehouse.'));
        return;
    }

    // balance check per item (quoted − already ordered)
    for (const item of Object.keys(per_item)) {
        const balance = Math.max(0,
            flt(quoted[item] || 0) - flt(ordered_map[item] || 0));
        if (per_item[item] > balance + 0.0001) {
            frappe.msgprint(__('{0}: total entered ({1}) exceeds remaining balance ({2}).',
                [item, format_number(per_item[item]), format_number(balance)]));
            return;
        }
    }

    const v = d.get_values();
    if (!v) return;
    const group_by = ['single', 'warehouse', 'state'].includes(v.group_mode)
        ? v.group_mode : 'single';

    frappe.call({
        method: `${NOF_PATH}.validate_compliance`,
        args: { quotation: frm.doc.name, allocations: allocations, group_by: group_by },
        freeze: true, freeze_message: __('Running compliance checks...'),
        callback: (r) => {
            const comp = r.message || { errors: [], warnings: [] };
            if (comp.errors.length) {
                frappe.msgprint({
                    title: __('Compliance Error — cannot proceed'),
                    indicator: 'red',
                    message: comp.errors.join('<hr>')
                });
                return;
            }
            const proceed = () => {
                d.hide();
                nof_create(frm, allocations, group_by, v.delivery_date);
            };
            if (comp.warnings.length) {
                frappe.confirm(
                    `<b>${__('Compliance Review Required')}</b><br><br>`
                    + comp.warnings.join('<hr>')
                    + `<br><br>${__('Proceed anyway?')}`,
                    proceed
                );
            } else proceed();
        }
    });
}

// =====================================================================
// SO GENERATOR — balance-aware stepper + grouping + compliance popup
// =====================================================================
function nof_open_generator(frm, s) {
    frappe.call({
        method: `${NOF_PATH}.get_allocation_data`,
        args: { quotation: frm.doc.name },
        freeze: true,
        freeze_message: __('Loading balances and stock...'),
        callback: (r) => {
            const data = r.message;
            if (!data) return;

            const sections = [];
            data.items.forEach(it => {
                if (flt(it.balance_qty) <= 0) return;
                const stock = (data.stock[it.item_code] || [])
                    .map(x => ({ ...x, select_qty: 0 }));
                if (!stock.length) return;
                let need = flt(it.balance_qty);
                stock.forEach(x => {
                    x.select_qty = Math.min(need, x.available_qty);
                    need -= x.select_qty;
                });
                sections.push({ ...it, stock });
            });

            if (!sections.length) {
                frappe.msgprint(__('No items with remaining balance and available stock.'));
                return;
            }
            nof_run_stepper(frm, sections, data, s);
        }
    });
}

function nof_run_stepper(frm, sections, data, settings) {
    let idx = 0;
    show_item_step();

    function chips_html() {
        return sections.map((sec, i) => {
            const total = sec.stock.reduce((a, r) => a + flt(r.select_qty), 0);
            let bg = '#d1d8dd';
            if (total >= sec.balance_qty && sec.balance_qty > 0) bg = '#98d85b';
            else if (total > 0) bg = '#ffa00a';
            const border = (i === idx) ? 'border:2px solid #36414c;' : '';
            return `<span title="${frappe.utils.escape_html(sec.item_code)}"
                style="display:inline-block;width:14px;height:14px;border-radius:50%;
                background:${bg};${border}margin-right:5px;"></span>`;
        }).join('');
    }

    function show_item_step() {
        const sec = sections[idx];
        const is_last = idx === sections.length - 1;

        const d = new frappe.ui.Dialog({
            title: __('Allocate — {0} ({1} of {2})', [sec.item_code, idx + 1, sections.length]),
            size: 'large',
            fields: [
                {
                    fieldtype: 'HTML', fieldname: 'head',
                    options: `<div style="margin:2px 0 8px;">${chips_html()}</div>
                        <div style="margin:4px 0 8px;">
                        <b>${frappe.utils.escape_html(sec.item_code)}</b>
                        ${sec.item_name && sec.item_name !== sec.item_code
                            ? `<span class="text-muted"> \u2014 ${frappe.utils.escape_html(sec.item_name)}</span>` : ''}
                        <br>
                        ${__('Quoted')}: <b>${format_number(sec.quoted_qty)}</b>
                        &nbsp;|&nbsp;${__('Already Ordered')}: <b>${format_number(sec.ordered_qty)}</b>
                        &nbsp;|&nbsp;${__('Balance')}: <b style="color:#e6a817;">${format_number(sec.balance_qty)}</b>
                        &nbsp;|&nbsp;${__('Stock')}: <b>${format_number(
                            sec.stock.reduce((a, r) => a + r.available_qty, 0))}</b>
                        </div>`
                },
                {
                    fieldname: 'alloc', fieldtype: 'Table',
                    cannot_add_rows: true, in_place_edit: true, data: sec.stock,
                    fields: [
                        { fieldname: 'warehouse', label: __('Warehouse'), fieldtype: 'Link',
                          options: 'Warehouse', in_list_view: 1, read_only: 1, columns: 3 },
                        { fieldname: 'state', label: __('Region/State'), fieldtype: 'Data',
                          in_list_view: 1, read_only: 1, columns: 2 },
                        { fieldname: 'available_qty', label: __('Available'), fieldtype: 'Float',
                          in_list_view: 1, read_only: 1, columns: 2 },
                        { fieldname: 'select_qty', label: __('Order Qty'), fieldtype: 'Float',
                          in_list_view: 1, columns: 3 }
                    ]
                }
            ],
            primary_action_label: is_last ? __('Next: Grouping') : __('Next'),
            primary_action: () => {
                if (!save_item_step(d, sec)) return;
                d.hide();
                if (is_last) show_grouping_step();
                else { idx++; show_item_step(); }
            },
            secondary_action_label: idx > 0 ? __('Previous') : __('Cancel'),
            secondary_action: () => {
                if (idx > 0) { save_item_step(d, sec, true); d.hide(); idx--; show_item_step(); }
                else d.hide();
            }
        });

        if (!is_last && typeof d.add_custom_action === 'function') {
            d.add_custom_action(__('Auto-Fill Rest & Continue'), () => {
                if (!save_item_step(d, sec)) return;
                d.hide();
                show_grouping_step();
            });
        }
        d.show();
    }

    function save_item_step(d, sec, lenient) {
        const values = d.get_values();
        (values.alloc || []).forEach(r => {
            const x = sec.stock.find(y => y.warehouse === r.warehouse);
            if (x) x.select_qty = flt(r.select_qty);
        });
        if (lenient) return true;

        const over = sec.stock.find(x => flt(x.select_qty) > flt(x.available_qty));
        if (over) {
            frappe.msgprint(__('{0}: allocation for {1} exceeds available ({2}).',
                [sec.item_code, over.warehouse, format_number(over.available_qty)]));
            return false;
        }
        const total = sec.stock.reduce((a, r) => a + flt(r.select_qty), 0);
        if (total > flt(sec.balance_qty) + 0.0001) {
            frappe.msgprint(__('{0}: total ({1}) exceeds remaining balance ({2}).',
                [sec.item_code, format_number(total), format_number(sec.balance_qty)]));
            return false;
        }
        return true;
    }

    function show_grouping_step() {
        const allocations = [];
        sections.forEach(sec => {
            sec.stock.forEach(x => {
                if (flt(x.select_qty) > 0) {
                    allocations.push({
                        item_code: sec.item_code,
                        warehouse: x.warehouse,
                        state: x.state || __('No State'),
                        qty: flt(x.select_qty)
                    });
                }
            });
        });
        if (!allocations.length) {
            frappe.msgprint(__('Nothing allocated.'));
            return;
        }

        const MODE_LABELS = {
            single: __('Single Order'),
            warehouse: __('One Order per Warehouse'),
            state: __('One Order per State/Region')
        };
        const mode_options = [MODE_LABELS.single, MODE_LABELS.warehouse];
        if (data.has_state_field) mode_options.push(MODE_LABELS.state);
        const default_label = MODE_LABELS[data.default_grouping] || MODE_LABELS.single;

        const d = new frappe.ui.Dialog({
            title: __('Grouping & Review'),
            size: 'large',
            fields: [
                {
                    fieldname: 'group_mode', fieldtype: 'Select',
                    label: __('How should Sales Orders be created?'),
                    options: mode_options.join('\n'),
                    default: mode_options.includes(default_label) ? default_label : MODE_LABELS.single,
                    reqd: 1,
                    change: () => render_preview(d, allocations, MODE_LABELS)
                },
                {
                    fieldname: 'delivery_date', fieldtype: 'Date',
                    label: __('Delivery Date'), reqd: 1,
                    default: data.default_delivery_date
                },
                { fieldtype: 'Section Break' },
                { fieldname: 'preview', fieldtype: 'HTML' }
            ],
            primary_action_label: __('Create Sales Order(s)'),
            primary_action: () => {
                const v = d.get_values();
                const group_by = v.group_mode === MODE_LABELS.warehouse ? 'warehouse'
                    : v.group_mode === MODE_LABELS.state ? 'state'
                    : 'single';
                const payload = allocations.map(a =>
                    ({ item_code: a.item_code, warehouse: a.warehouse, qty: a.qty }));

                frappe.call({
                    method: `${NOF_PATH}.validate_compliance`,
                    args: { quotation: frm.doc.name, allocations: payload, group_by },
                    freeze: true, freeze_message: __('Running compliance checks...'),
                    callback: (r) => {
                        const comp = r.message || { errors: [], warnings: [] };
                        if (comp.errors.length) {
                            frappe.msgprint({
                                title: __('Compliance Error — cannot proceed'),
                                indicator: 'red',
                                message: comp.errors.join('<hr>')
                            });
                            return;
                        }
                        const proceed = () => {
                            d.hide();
                            nof_create(frm, payload, group_by, v.delivery_date);
                        };
                        if (comp.warnings.length) {
                            frappe.confirm(
                                `<b>${__('Compliance Review Required')}</b><br><br>`
                                + comp.warnings.join('<hr>')
                                + `<br><br>${__('Proceed anyway?')}`,
                                proceed
                            );
                        } else proceed();
                    }
                });
            },
            secondary_action_label: __('Back'),
            secondary_action: () => { d.hide(); show_item_step(); }
        });

        d.show();
        render_preview(d, allocations, MODE_LABELS);
    }

    function render_preview(d, allocations, MODE_LABELS) {
        const mode = d.get_value('group_mode');
        const key_fn = mode === MODE_LABELS.warehouse ? (a => a.warehouse)
            : mode === MODE_LABELS.state ? (a => a.state)
            : (() => __('All Warehouses'));

        const groups = {};
        allocations.forEach(a => {
            (groups[key_fn(a)] = groups[key_fn(a)] || []).push(a);
        });

        let html = `<div class="text-muted small" style="margin-bottom:6px;">
            ${__('This will create {0} draft Sales Order(s):', [Object.keys(groups).length])}</div>`;
        Object.keys(groups).sort().forEach(key => {
            html += `<div style="margin-bottom:10px;">
                <b>SO \u2014 ${frappe.utils.escape_html(key)}</b>
                <table class="table table-bordered table-sm" style="margin:4px 0 0;">
                    <thead><tr>
                        <th>${__('Item')}</th><th>${__('Warehouse')}</th>
                        <th class="text-right">${__('Qty')}</th>
                    </tr></thead><tbody>`;
            groups[key].forEach(a => {
                html += `<tr><td>${frappe.utils.escape_html(a.item_code)}</td>
                    <td>${frappe.utils.escape_html(a.warehouse)}</td>
                    <td class="text-right">${format_number(a.qty)}</td></tr>`;
            });
            html += `</tbody></table></div>`;
        });
        d.get_field('preview').$wrapper.html(html);
    }
}

function nof_create(frm, allocations, group_by, delivery_date) {
    frappe.call({
        method: `${NOF_PATH}.create_sales_orders`,
        args: { quotation: frm.doc.name, allocations, group_by, delivery_date },
        freeze: true, freeze_message: __('Creating Sales Order(s)...'),
        callback: (r) => {
            const res = r.message;
            if (!res || !res.created) return;
            let msg = __('Created {0} draft Sales Order(s):', [res.created.length]) + '<br>';
            res.created.forEach(so => {
                msg += `<a href="/app/sales-order/${so.name}">${so.name}</a>
                    \u2014 ${frappe.utils.escape_html(so.group)}
                    (${so.items} ${__('items')},
                    ${format_currency(so.grand_total, frm.doc.currency)})<br>`;
            });
            frappe.msgprint({ title: __('Sales Orders Created'), message: msg, indicator: 'green' });
            frm.reload_doc();
        }
    });
}

// Neotec Print Settings — form helpers
frappe.ui.form.on('Neotec Print Settings', {
    refresh(frm) {
        frm.add_custom_button(__('Sync Print Formats Now'), () => {
            frappe.call({
                method: 'neotec_orderflow.setup.install.sync_print_formats',
                freeze: true,
                freeze_message: __('Syncing print formats...'),
                callback: (r) => {
                    const lines = r.message || [];
                    frappe.msgprint({
                        title: __('Print Format Sync'),
                        indicator: lines.some(l => l.startsWith('FAILED')) ? 'orange' : 'green',
                        message: lines.join('<br>')
                    });
                }
            });
        });
        frm.add_custom_button(__('Open Print Format List'), () => {
            frappe.set_route('List', 'Print Format',
                { module: 'Neotec OrderFlow' });
        });
    }
});

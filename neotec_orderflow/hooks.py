app_name = "neotec_orderflow"
app_title = "Neotec OrderFlow"
app_publisher = "Neotec"
app_description = "Warehouse-wise fulfilment engine for ERPNext"
app_email = "support@neotec.ai"
app_license = "Commercial"

after_install = "neotec_orderflow.setup.install.ensure_all"
after_migrate = "neotec_orderflow.setup.install.ensure_all"

doctype_js = {
    "Quotation": "public/js/quotation.js",
    "Sales Order": "public/js/sales_order.js",
}

doc_events = {
    "Quotation": {
        "on_submit": "neotec_orderflow.automation.auto_so.on_quotation_submit",
    }
}

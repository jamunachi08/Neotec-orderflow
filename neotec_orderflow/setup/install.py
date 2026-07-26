# =====================================================================
# Neotec OrderFlow — install / migrate (idempotent, code-driven)
# Runs on after_install AND after_migrate: safe to run repeatedly.
# =====================================================================

import os

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

CUSTOM_FIELDS = {
    "Warehouse": [
        {
            "fieldname": "custom_whprefix",
            "label": "WH Print Prefix (Neotec)",
            "fieldtype": "Data",
            "insert_after": "warehouse_type",
            "description": "Short label (e.g. VZ, K1). Warehouses with a prefix "
                           "appear as columns on the quotation stock print.",
        },
        {
            "fieldname": "custom_state",
            "label": "Region / State (Neotec)",
            "fieldtype": "Data",
            "insert_after": "custom_whprefix",
            "description": "India: exact GST state name (e.g. Andhra Pradesh). "
                           "KSA: branch/region name. Drives per-state grouping "
                           "and compliance checks.",
        },
    ],
    "Sales Order Item": [
        {
            "fieldname": "custom_available_qty",
            "label": "Available Qty (at allocation)",
            "fieldtype": "Float",
            "insert_after": "warehouse",
            "read_only": 1,
            "no_copy": 1,
            "print_hide": 1,
        },
    ],
}

PRINT_FORMAT_NAME = "Quotation Stock Availability (Neotec)"


def ensure_all():
    create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)
    ensure_print_format()
    frappe.db.commit()


def ensure_print_format():
    html_path = os.path.join(
        frappe.get_app_path("neotec_orderflow"),
        "print", "quotation_stock_availability.html",
    )
    with open(html_path, encoding="utf-8") as f:
        html = f.read()

    if frappe.db.exists("Print Format", PRINT_FORMAT_NAME):
        pf = frappe.get_doc("Print Format", PRINT_FORMAT_NAME)
    else:
        pf = frappe.new_doc("Print Format")
        pf.name = PRINT_FORMAT_NAME

    pf.update({
        "doc_type": "Quotation",
        "module": "Neotec OrderFlow",
        "print_format_type": "Jinja",
        "standard": "No",
        "disabled": 0,
        "html": html,
    })
    pf.save(ignore_permissions=True)

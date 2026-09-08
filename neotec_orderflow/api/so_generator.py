# =====================================================================
# Neotec OrderFlow — core API
# Settings-aware quotation balance/stock data + atomic grouped SO
# creation + region-pluggable compliance pre-flight.
# =====================================================================

import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

from erpnext.selling.doctype.quotation.quotation import make_sales_order

from neotec_orderflow.compliance import run_compliance

BASE_COPY_FIELDS = [
    "item_code", "item_name", "description", "item_group", "brand",
    "uom", "stock_uom", "conversion_factor",
    "price_list_rate", "margin_type", "margin_rate_or_amount",
    "discount_percentage", "discount_amount", "rate", "item_tax_template",
]


def get_settings():
    return frappe.get_cached_doc("Neotec OrderFlow Settings")


@frappe.whitelist()
def get_client_settings():
    """Feature flags for the client scripts (one call per form load).

    Reads the Single doc straight from the DB (not the document cache) so a
    setting the user just toggled takes effect on the very next form load,
    even across multiple workers on Frappe Cloud.
    """
    s = frappe.get_doc("Neotec OrderFlow Settings")
    return {
        "enabled": cint(s.enabled),
        "enable_stock_viewer": cint(s.enable_stock_viewer),
        "viewer_open_on_item": cint(s.viewer_open_on_item),
        "enable_so_generator": cint(s.enable_so_generator),
        "enable_so_allocator": cint(s.enable_so_allocator),
        "default_grouping": s.default_grouping or "single",
        "region": s.region or "None",
    }


def _warehouse_map(settings):
    """Enabled leaf warehouses -> {name: {state, warehouse_type}},
    honoring the exclude_transit setting."""
    fields = ["name", "warehouse_type"]
    has_state = frappe.get_meta("Warehouse").has_field("custom_state")
    if has_state:
        fields.append("custom_state")
    out = {}
    for w in frappe.get_all("Warehouse", filters={"disabled": 0, "is_group": 0},
                            fields=fields):
        if settings.exclude_transit and (w.warehouse_type or "") == "Transit":
            continue
        out[w.name] = {
            "state": (w.get("custom_state") or "").strip() if has_state else "",
        }
    return out, has_state


def _available(b, settings):
    avail = flt(b.actual_qty)
    if settings.deduct_reserved_qty:
        avail -= flt(b.reserved_qty)
    if settings.deduct_reserved_stock:
        avail -= flt(b.reserved_stock)
    return avail


def _customer_state(context):
    """Best-effort customer state from the quotation's addresses:
    shipping address -> billing address -> customer's default address."""
    if not context:
        return ""
    addr_name = context.get("shipping_address_name") or context.get("customer_address")
    if not addr_name and context.get("quotation_to") == "Customer" \
            and context.get("party_name"):
        try:
            from frappe.contacts.doctype.address.address import get_default_address
            addr_name = get_default_address("Customer", context.get("party_name"))
        except Exception:
            addr_name = None
    if not addr_name:
        return ""
    return (frappe.db.get_value("Address", addr_name, "state") or "").strip()


@frappe.whitelist()
def get_stock_matrix(item_codes, quotation: str = None, context=None):
    """State-grouped availability matrix for the Stock Availability window.

    Shows EVERY warehouse holding stock of the item(s) — including
    Transit-type warehouses. When exclude_transit is ON, transit
    warehouses are returned flagged info_only=1: visible in the window
    but not orderable and not counted in the orderable totals (matching
    the allocation formula). Also returns the customer's state (from the
    quotation addresses) so the client can highlight the preferred state
    section — indication only, the user makes the final choice.

    Returns:
      state_groups: [{state, warehouses: [{name, info_only}]}]
      avail:        {"item|warehouse": qty}
      ordered:      {item_code: qty already on SO from this quotation}
      customer_state: str
    """
    if isinstance(item_codes, str):
        item_codes = json.loads(item_codes)
    if isinstance(context, str):
        context = json.loads(context)
    item_codes = [i for i in (item_codes or []) if i]
    if not item_codes:
        frappe.throw(_("No items supplied."))

    s = get_settings()
    if not (s.enabled and s.enable_stock_viewer):
        frappe.throw(_("Stock viewer is disabled in Neotec OrderFlow Settings."))

    # full warehouse map — transit INCLUDED, flagged when settings exclude it
    fields = ["name", "warehouse_type"]
    has_state = frappe.get_meta("Warehouse").has_field("custom_state")
    if has_state:
        fields.append("custom_state")
    wh_map = {}
    for w in frappe.get_all("Warehouse", filters={"disabled": 0, "is_group": 0},
                            fields=fields):
        is_transit = (w.warehouse_type or "") == "Transit"
        wh_map[w.name] = {
            "state": (w.get("custom_state") or "").strip() if has_state else "",
            "info_only": 1 if (s.exclude_transit and is_transit) else 0,
        }

    avail, used_wh = {}, set()
    for b in frappe.get_all(
        "Bin",
        filters={"item_code": ["in", item_codes]},
        fields=["item_code", "warehouse", "actual_qty", "reserved_qty", "reserved_stock"],
        limit_page_length=0,
    ):
        if b.warehouse not in wh_map:
            continue
        q = _available(b, s)
        if q <= 0:
            continue
        key = f"{b.item_code}|{b.warehouse}"
        avail[key] = avail.get(key, 0) + q
        used_wh.add(b.warehouse)

    # group the warehouses that actually hold stock, state-wise
    by_state = {}
    for w in used_wh:
        state = wh_map[w]["state"] or ""
        by_state.setdefault(state, []).append(w)

    def state_sort_key(st):
        return (st == "", st)  # named states alphabetically, blank last

    state_groups = [
        {
            "state": st or _("No State"),
            "warehouses": [
                {"name": w, "info_only": wh_map[w]["info_only"]}
                for w in sorted(by_state[st])
            ],
        }
        for st in sorted(by_state.keys(), key=state_sort_key)
    ]

    # already-ordered qty against this quotation (for Balance column)
    ordered = {}
    if quotation and frappe.db.exists("Quotation", quotation):
        docstatus_filter = ["<", 2] if s.include_draft_so_in_balance else ["=", 1]
        for row in frappe.get_all(
            "Sales Order Item",
            filters={"prevdoc_docname": quotation, "docstatus": docstatus_filter},
            fields=["item_code", "sum(qty) as ordered_qty"],
            group_by="item_code",
        ):
            ordered[row.item_code] = flt(row.ordered_qty)

    return {
        "state_groups": state_groups,
        "avail": avail,
        "ordered": ordered,
        "has_state_field": has_state,
        "customer_state": _customer_state(context),
        "default_grouping": s.default_grouping or "single",
        "default_delivery_date": add_days(nowdate(), s.delivery_days_offset or 0),
    }


@frappe.whitelist()
def get_allocation_data(quotation: str):
    s = get_settings()
    if not s.enabled:
        frappe.throw(_("Neotec OrderFlow is disabled in settings."))

    qtn = frappe.get_doc("Quotation", quotation)
    if qtn.docstatus != 1:
        frappe.throw(_("Quotation must be submitted before creating Sales Orders."))

    item_codes = list({d.item_code for d in qtn.items if d.item_code})
    if not item_codes:
        frappe.throw(_("Quotation has no items."))

    docstatus_filter = ["<", 2] if s.include_draft_so_in_balance else ["=", 1]
    ordered_map = {}
    for row in frappe.get_all(
        "Sales Order Item",
        filters={"prevdoc_docname": quotation, "docstatus": docstatus_filter},
        fields=["item_code", "sum(qty) as ordered_qty"],
        group_by="item_code",
    ):
        ordered_map[row.item_code] = flt(row.ordered_qty)

    wh_map, has_state = _warehouse_map(s)

    stock = {}
    for b in frappe.get_all(
        "Bin",
        filters={"item_code": ["in", item_codes]},
        fields=["item_code", "warehouse", "actual_qty", "reserved_qty", "reserved_stock"],
        limit_page_length=0,
    ):
        if b.warehouse not in wh_map:
            continue
        avail = _available(b, s)
        if avail <= 0:
            continue
        stock.setdefault(b.item_code, []).append({
            "warehouse": b.warehouse,
            "state": wh_map[b.warehouse]["state"],
            "available_qty": avail,
        })
    for rows in stock.values():
        rows.sort(key=lambda r: -r["available_qty"])

    items, seen = [], set()
    for d in qtn.items:
        if d.item_code in seen:
            continue
        seen.add(d.item_code)
        quoted = sum(flt(x.qty) for x in qtn.items if x.item_code == d.item_code)
        ordered = ordered_map.get(d.item_code, 0)
        items.append({
            "item_code": d.item_code,
            "item_name": d.item_name,
            "uom": d.uom,
            "quoted_qty": quoted,
            "ordered_qty": ordered,
            "balance_qty": max(0, quoted - ordered),
        })

    return {
        "items": items,
        "stock": stock,
        "has_state_field": has_state,
        "default_grouping": s.default_grouping or "single",
        "default_delivery_date": add_days(nowdate(), s.delivery_days_offset or 0),
    }


@frappe.whitelist()
def validate_compliance(quotation: str, allocations, group_by: str = "single"):
    if isinstance(allocations, str):
        allocations = json.loads(allocations)
    s = get_settings()
    qtn = frappe.get_doc("Quotation", quotation)
    data = get_allocation_data(quotation)
    return run_compliance(qtn, allocations, group_by, data, s)


@frappe.whitelist()
def create_sales_orders(quotation: str, allocations, group_by: str = "single",
                        delivery_date: str = None):
    s = get_settings()
    if not s.enabled:
        frappe.throw(_("Neotec OrderFlow is disabled in settings."))
    if isinstance(allocations, str):
        allocations = json.loads(allocations)
    if group_by not in ("single", "warehouse", "state"):
        frappe.throw(_("Invalid grouping mode."))
    if not allocations:
        frappe.throw(_("No allocations provided."))

    qtn = frappe.get_doc("Quotation", quotation)
    if qtn.docstatus != 1:
        frappe.throw(_("Quotation must be submitted."))

    data = get_allocation_data(quotation)

    # ---- compliance: hard errors block unconditionally ----
    comp = run_compliance(qtn, allocations, group_by, data, s)
    if comp["errors"]:
        frappe.throw("<br><br>".join(comp["errors"]),
                     title=_("Compliance Error"))

    delivery_date = getdate(delivery_date) if delivery_date \
        else getdate(add_days(nowdate(), s.delivery_days_offset or 0))

    # ---- balance & stock revalidation (never trust the browser) ----
    balance = {i["item_code"]: i["balance_qty"] for i in data["items"]}
    stock_lookup = {
        (i, r["warehouse"]): r["available_qty"]
        for i, rows in data["stock"].items() for r in rows
    }
    wh_state = {r["warehouse"]: r["state"]
                for rows in data["stock"].values() for r in rows}

    alloc_per_item = {}
    for a in allocations:
        qty = flt(a.get("qty"))
        if qty <= 0:
            continue
        item, wh = a.get("item_code"), a.get("warehouse")
        if (item, wh) not in stock_lookup:
            frappe.throw(_("No available stock for {0} in {1}.").format(item, wh))
        if qty > stock_lookup[(item, wh)]:
            frappe.throw(_("{0}: allocation {1} exceeds available {2} in {3}.")
                         .format(item, qty, stock_lookup[(item, wh)], wh))
        alloc_per_item[item] = alloc_per_item.get(item, 0) + qty

    for item, total in alloc_per_item.items():
        if total > flt(balance.get(item, 0)) + 0.0001:
            frappe.throw(_("{0}: total allocation {1} exceeds remaining quotation balance {2}.")
                         .format(item, total, balance.get(item, 0)))

    clean = [a for a in allocations if flt(a.get("qty")) > 0]
    if not clean:
        frappe.throw(_("Nothing allocated."))

    extra_fields = [f.strip() for f in (s.extra_copy_fields or "").split(",") if f.strip()]

    def group_key(a):
        if group_by == "warehouse":
            return a["warehouse"]
        if group_by == "state":
            return wh_state.get(a["warehouse"]) or _("No State")
        return "__single__"

    groups = {}
    for a in clean:
        groups.setdefault(group_key(a), []).append(a)

    # ---- create (one transaction: any failure rolls back all) ----
    created = []
    for key, group_allocs in sorted(groups.items()):
        so = make_sales_order(qtn.name)
        template = {}
        for row in so.items:
            template.setdefault(row.item_code, row)

        so.items = []
        for a in group_allocs:
            t = template.get(a["item_code"])
            if not t:
                frappe.throw(_("Item {0} is not on quotation {1}.")
                             .format(a["item_code"], qtn.name))
            row = {f: t.get(f) for f in BASE_COPY_FIELDS}
            for f in extra_fields:
                if t.get(f) is not None:
                    row[f] = t.get(f)
            row.update({
                "prevdoc_docname": qtn.name,
                "warehouse": a["warehouse"],
                "qty": flt(a["qty"]),
                "delivery_date": delivery_date,
            })
            so.append("items", row)

        so.delivery_date = delivery_date
        so.insert()
        so.add_comment("Comment",
                       _("Created by Neotec OrderFlow from {0}").format(qtn.name))
        created.append({
            "name": so.name,
            "group": key if key != "__single__" else _("All Warehouses"),
            "items": len(so.items),
            "grand_total": so.grand_total,
        })

    return {"created": created, "group_by": group_by,
            "compliance_warnings": comp["warnings"]}

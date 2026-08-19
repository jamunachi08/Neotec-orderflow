# Copyright (c) 2026, Neotec. Commercial.
"""Context builder for the Neotec Delivery Note / Tax Invoice print formats.

Exposed to Jinja via hooks.jinja.methods as ``nof_print_context(doc, kind)``.
All decisions (which tax columns, logos, balances, HSN summary, charges)
are computed here so the HTML templates stay purely presentational and a
customer can re-skin them without touching logic.
"""
import frappe
from frappe import _
from frappe.utils import cint, flt, fmt_money, getdate, money_in_words

from neotec_orderflow.neotec_orderflow.doctype.neotec_print_settings.neotec_print_settings import (
    DEFAULT_DN_TERMS, DEFAULT_PKG, DEFAULT_PAY_NOTE, DEFAULT_SI_TERMS,
)

GST_TYPES = ("cgst", "sgst", "igst", "cess")
NUMERIC_PKG_FIELDS = (
    ("Bag", "custom_bag", "custom_bag_weight"),
    ("Box", "custom_box", "custom_box_weight"),
    ("Carton", "custom_carton", "custom_carton_weight"),
    ("W.Box", "custom_w_box", "custom_w_box_weight"),
    ("Length", "custom_length", None),
    ("Other", "custom_other", "custom_other_weight"),
)


def _settings():
    try:
        return frappe.get_cached_doc("Neotec Print Settings")
    except Exception:
        return frappe._dict()


def _hex_ok(c, default):
    c = (c or "").strip()
    return c if c.startswith("#") and len(c) in (4, 7) else default


def _company_logo(doc, s):
    if s.get("company_logo"):
        return s.company_logo
    logo = frappe.db.get_value("Company", doc.company, "company_logo")
    if logo:
        return logo
    # fallback: the letter head image, if any
    lh = doc.get("letter_head") or frappe.db.get_value("Company", doc.company, "default_letter_head")
    if lh:
        img = frappe.db.get_value("Letter Head", lh, "image")
        if img:
            return img
    return None


def _brand_logo(doc):
    brands = {i.get("brand") for i in doc.get("items") or [] if i.get("brand")}
    if len(brands) != 1:
        return None, None
    brand = brands.pop()
    img = frappe.db.get_value("Brand", brand, "image")
    return brand, img


def _company_address(doc):
    addr_name = doc.get("company_address")
    if not addr_name:
        links = frappe.get_all(
            "Dynamic Link",
            filters={"link_doctype": "Company", "link_name": doc.company, "parenttype": "Address"},
            pluck="parent",
        )
        if links:
            addr_name = links[0]
    if not addr_name:
        return None
    a = frappe.db.get_value(
        "Address", addr_name,
        ["address_line1", "address_line2", "city", "state", "pincode", "country", "phone", "email_id"],
        as_dict=True,
    )
    return a


def _company_info(doc):
    c = frappe.db.get_value(
        "Company", doc.company,
        ["company_name", "gstin", "phone_no", "email", "website"], as_dict=True,
    ) or frappe._dict()
    gstin = doc.get("company_gstin") or c.get("gstin") or frappe.db.get_value(
        "Company", doc.company, "tax_id")
    return frappe._dict(
        name=c.get("company_name") or doc.company,
        gstin=gstin,
        phone=c.get("phone_no"),
        email=c.get("email"),
        website=c.get("website"),
    )


def _package_details(doc, s):
    rows, any_val = [], False
    for label, cnt_f, wt_f in NUMERIC_PKG_FIELDS:
        cnt = flt(doc.get(cnt_f)) if doc.get(cnt_f) is not None else None
        wt = flt(doc.get(wt_f)) if wt_f and doc.get(wt_f) is not None else None
        if (cnt or wt):
            any_val = True
        rows.append({"label": label, "count": cnt, "weight": wt})
    has_weight = any(r["weight"] for r in rows)
    total_cnt = sum(flt(r["count"]) for r in rows)
    total_wt = sum(flt(r["weight"]) for r in rows)
    return frappe._dict(
        rows=rows, any=any_val, has_weight=has_weight,
        total_count=total_cnt, total_weight=total_wt,
        blank=(not any_val and cint(s.get("show_package_blank_grid"))),
    )


def _tax_totals(doc):
    """Per-type GST totals from the taxes table (India Compliance sets
    gst_tax_type; fall back to matching the account head)."""
    t = {"cgst": 0.0, "sgst": 0.0, "igst": 0.0, "cess": 0.0}
    others = []
    for row in doc.get("taxes") or []:
        gtype = (row.get("gst_tax_type") or "").lower()
        if not gtype:
            head = (row.get("account_head") or "").lower()
            for k in GST_TYPES:
                if head.startswith(k) or f" {k}" in head or f"{k} " in head or f"-{k}" in head:
                    gtype = k
                    break
        amt = flt(row.get("base_tax_amount_after_discount_amount") or row.get("tax_amount"))
        if gtype in t:
            t[gtype] += amt
        elif amt:
            others.append({"description": row.get("description") or row.get("account_head"),
                           "amount": amt})
    return t, others


def _tax_mode(s, tax_totals, items):
    mode = (s.get("tax_display") or "Auto").split(" ")[0].lower()   # auto/all/rates/hidden
    if mode == "hidden":
        return {"show": False, "cols": [], "rates_only": False}
    if mode == "all":
        return {"show": True, "cols": ["cgst", "sgst", "igst"], "rates_only": False}
    if mode == "rates":
        # per the markup: CGST %, SGST %, IGST % rate columns, no amounts
        return {"show": True, "cols": ["cgst", "sgst", "igst"], "rates_only": True}
    cols = _applicable_cols(tax_totals, items)
    return {"show": bool(cols), "cols": cols, "rates_only": False}


def _applicable_cols(tax_totals, items):
    has_igst = flt(tax_totals["igst"]) > 0 or any(flt(i.get("igst_rate")) for i in items)
    has_cgst = flt(tax_totals["cgst"]) > 0 or any(flt(i.get("cgst_rate")) for i in items)
    if has_igst and not has_cgst:
        return ["igst"]
    if has_cgst:
        return ["cgst", "sgst"]
    return []


def _item_rows(doc, s):
    rows = []
    for i in doc.get("items") or []:
        qty = flt(i.get("qty"))
        rate = flt(i.get("rate"))
        plr = flt(i.get("price_list_rate")) or rate
        gross = plr * qty
        disc_pct = flt(i.get("discount_percentage"))
        disc_amt = flt(i.get("discount_amount")) * qty if i.get("discount_amount") else (gross - rate * qty)
        taxable = flt(i.get("taxable_value")) or flt(i.get("net_amount")) or rate * qty
        cg_r, sg_r, ig_r = flt(i.get("cgst_rate")), flt(i.get("sgst_rate")), flt(i.get("igst_rate"))
        cg_a, sg_a, ig_a = flt(i.get("cgst_amount")), flt(i.get("sgst_amount")), flt(i.get("igst_amount"))
        # fall back to item_tax_rate JSON if India Compliance rates are absent
        if not (cg_r or sg_r or ig_r) and i.get("item_tax_rate"):
            try:
                for head, r in frappe.parse_json(i.item_tax_rate).items():
                    h = head.lower()
                    if "igst" in h: ig_r = flt(r)
                    elif "cgst" in h: cg_r = flt(r)
                    elif "sgst" in h: sg_r = flt(r)
            except Exception:
                pass
            if not (cg_a or sg_a or ig_a):
                cg_a, sg_a, ig_a = taxable * cg_r / 100, taxable * sg_r / 100, taxable * ig_r / 100
        line_total = taxable + cg_a + sg_a + ig_a
        rows.append(frappe._dict(
            item_code=i.get("item_code"), item_name=i.get("item_name"),
            description=(i.get("description") or ""), image=i.get("image"),
            hsn=i.get("gst_hsn_code"), qty=qty, uom=i.get("uom") or i.get("stock_uom"),
            unit_price=plr, gross=gross, disc_pct=disc_pct, disc_amt=max(disc_amt, 0),
            taxable=taxable,
            cgst_rate=cg_r, cgst_amount=cg_a, sgst_rate=sg_r, sgst_amount=sg_a,
            igst_rate=ig_r, igst_amount=ig_a, line_total=line_total,
            batch=i.get("batch_no"), serial=i.get("serial_no"),
            warehouse=i.get("warehouse"), against=i.get("against_sales_order") or i.get("sales_order"),
        ))
    return rows


def _hsn_summary(rows):
    agg = {}
    for r in rows:
        k = r.hsn or "-"
        a = agg.setdefault(k, frappe._dict(hsn=k, qty=0, taxable=0, cgst_rate=0, cgst=0,
                                           sgst_rate=0, sgst=0, igst_rate=0, igst=0, total=0))
        a.qty += r.qty; a.taxable += r.taxable
        a.cgst += r.cgst_amount; a.sgst += r.sgst_amount; a.igst += r.igst_amount
        a.cgst_rate = a.cgst_rate or r.cgst_rate
        a.sgst_rate = a.sgst_rate or r.sgst_rate
        a.igst_rate = a.igst_rate or r.igst_rate
        a.total += r.line_total
    out = sorted(agg.values(), key=lambda x: x.hsn)
    tot = frappe._dict(qty=sum(a.qty for a in out), taxable=sum(a.taxable for a in out),
                       cgst=sum(a.cgst for a in out), sgst=sum(a.sgst for a in out),
                       igst=sum(a.igst for a in out), total=sum(a.total for a in out))
    tot.tax = tot.cgst + tot.sgst + tot.igst
    return out, tot


def _customer_balance(doc):
    """Ledger balance as on posting date: current (incl. this invoice if
    submitted) and previous (before this invoice)."""
    try:
        from erpnext.accounts.utils import get_balance_on
        cur = flt(get_balance_on(
            party_type="Customer", party=doc.customer, company=doc.company,
            date=doc.get("posting_date") or doc.get("transaction_date"),
            ignore_account_permission=True,
        ))
    except Exception:
        return None
    this = flt(doc.get("base_rounded_total") or doc.get("base_grand_total") or doc.get("grand_total")) \
        if doc.docstatus == 1 else 0.0
    if doc.get("is_return"):
        this = -abs(this) if this > 0 else this
    prev = cur - this
    return frappe._dict(previous=prev, current=cur)


def _fmt_drcr(v, currency):
    v = flt(v)
    if abs(v) < 0.005:
        return fmt_money(0, currency=currency)
    return f"{fmt_money(abs(v), currency=currency)} {'Dr' if v > 0 else 'Cr'}"


def _terms(doc, s, kind):
    if not cint(s.get("show_terms", 1)):
        return ""
    src = s.get("terms_source") or "Document terms, else settings"
    settings_text = (s.get("dn_terms") if kind == "dn" else s.get("si_terms")) or \
        (DEFAULT_DN_TERMS if kind == "dn" else DEFAULT_SI_TERMS)
    doc_terms = frappe.utils.strip_html(doc.get("terms") or "").strip()
    if src.startswith("Settings"):
        return settings_text
    if src.startswith("Document terms,"):
        return doc_terms or settings_text
    return doc_terms


def _charges_band(doc, s, tax_totals, items, tax_cols):
    """Legacy-style band under the item table: Total Before Freight, the
    charge rows (Freight / Insurance / P&F ...) each with its own tax
    columns, and Round Off.

    Per-charge GST is derived from the document's GST rows: when the GST
    row is computed 'On Previous Row Total/Amount' its rate applies to the
    charge; otherwise we fall back to the dominant item rate. Amounts are
    charge x rate, which is exactly how ERPNext arrived at the GST total."""
    # always-show rows from settings: "Label|SAC"
    wanted = []
    for ln in (s.get("classic_charge_rows") or "").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        label, _, sac = ln.partition("|")
        wanted.append({"label": label.strip(), "sac": sac.strip()})

    # GST rate applicable to charges
    def _dominant(attr):
        vals = [flt(i.get(attr)) for i in items if flt(i.get(attr))]
        return max(set(vals), key=vals.count) if vals else 0.0
    gst_rates = {"cgst": 0.0, "sgst": 0.0, "igst": 0.0}
    on_prev = False
    for row in doc.get("taxes") or []:
        gtype = (row.get("gst_tax_type") or "").lower()
        if not gtype:
            head = (row.get("account_head") or "").lower()
            gtype = next((k for k in ("igst", "cgst", "sgst") if k in head), "")
        if gtype in gst_rates and flt(row.get("rate")):
            gst_rates[gtype] = flt(row.get("rate"))
            if (row.get("charge_type") or "").startswith("On Previous Row"):
                on_prev = True
    if not on_prev:
        # GST rows are item-wise (On Net Total with item tax templates) —
        # ERPNext then does NOT tax the charge rows unless they are taxed
        # separately. Still show the dominant item rate, matching the
        # legacy sheet which printed the rate next to P&F.
        gst_rates = {k: _dominant(f"{k}_rate") for k in gst_rates}

    # actual charge rows
    actual = []
    for row in doc.get("taxes") or []:
        gtype = (row.get("gst_tax_type") or "").lower()
        head = (row.get("account_head") or "").lower()
        if gtype or any(k in head for k in ("igst", "cgst", "sgst", "cess")):
            continue
        actual.append({"description": (row.get("description") or row.get("account_head") or "").strip(),
                       "amount": flt(row.get("base_tax_amount_after_discount_amount") or row.get("tax_amount"))})

    rows, used = [], set()
    def _mk(label, sac, amount):
        r = frappe._dict(label=label, sac=sac, amount=amount)
        for k in ("cgst", "sgst", "igst"):
            r[f"{k}_rate"] = gst_rates[k] if amount else (gst_rates[k] if k in tax_cols else 0.0)
            r[f"{k}_amount"] = amount * gst_rates[k] / 100 if amount else 0.0
        r.total = amount + r.cgst_amount + r.sgst_amount + r.igst_amount
        return r
    for w in wanted:
        amt, key = 0.0, w["label"].lower()
        for i, a in enumerate(actual):
            if i in used:
                continue
            d = a["description"].lower()
            if key == d or key in d or d in key:
                amt += a["amount"]; used.add(i)
        rows.append(_mk(w["label"], w["sac"], amt))
    for i, a in enumerate(actual):
        if i not in used and a["amount"]:
            rows.append(_mk(a["description"], "", a["amount"]))

    before = frappe._dict(
        taxable=sum(i.taxable for i in items),
        cgst=sum(i.cgst_amount for i in items), sgst=sum(i.sgst_amount for i in items),
        igst=sum(i.igst_amount for i in items), total=sum(i.line_total for i in items))
    return frappe._dict(rows=rows, before=before,
                        show_before=cint(s.get("classic_show_total_before_freight", 1)))


def _terms_lines(text):
    import re
    out = []
    for ln in (text or "").split("\n"):
        ln = ln.strip()
        if ln:
            out.append(re.sub(r"^\s*\d+[\).:-]\s*", "", ln))
    return out


def _qr_svg(data, size=92):
    """Inline SVG QR (frappe ships pyqrcode for 2FA)."""
    if not data:
        return ""
    try:
        import pyqrcode
        qr = pyqrcode.create(data, error="L")
        # render from the module matrix so we don't depend on pyqrcode.svg()
        mods = qr.text(quiet_zone=1).splitlines()
        n = len(mods)
        cell = size / n
        rects = []
        for y, row in enumerate(mods):
            x = 0
            while x < n:
                if row[x] == "1":
                    x0 = x
                    while x < n and row[x] == "1":
                        x += 1
                    rects.append(f'<rect x="{x0*cell:.2f}" y="{y*cell:.2f}" width="{(x-x0)*cell:.2f}" height="{cell:.2f}"/>')
                else:
                    x += 1
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
                f'viewBox="0 0 {size} {size}" shape-rendering="crispEdges">'
                f'<rect width="{size}" height="{size}" fill="#fff"/><g fill="#000">'
                + "".join(rects) + "</g></svg>")
    except Exception:
        return ""


def _footer_contact(s, comp, caddr):
    if s.get("footer_contact"):
        return s.footer_contact
    bits = []
    ph = comp.phone or (caddr and caddr.get("phone"))
    em = comp.email or (caddr and caddr.get("email_id"))
    if ph: bits.append(f"Ph : {ph}")
    if em: bits.append(f"Email : {em}")
    if comp.website: bits.append(comp.website)
    return "   |   ".join(bits)


def nof_print_context(doc, kind="si", variant="modern"):
    """kind: 'dn' (Delivery Note) or 'si' (Sales Invoice).
    variant: 'modern' (default Neotec layout) or 'classic' (legacy charges
    band under the item table, per-variant tax-column and logo defaults)."""
    s = _settings()
    kind = "dn" if kind == "dn" else "si"
    classic = (variant == "classic")
    currency = doc.get("currency") or "INR"
    if classic:
        # variant-specific overrides, everything else shared
        try:
            s = frappe._dict(s.as_dict())
        except Exception:
            s = frappe._dict(dict(s))
        s.tax_display = s.get("classic_si_tax_display" if kind == "si" else "classic_dn_tax_display") \
            or ("All — CGST, SGST & IGST" if kind == "si" else "Rates only")
        s.show_company_logo = cint(s.get("classic_show_company_logo"))
        s.show_brand_logo = cint(s.get("classic_show_brand_logo", 1))

    comp = _company_info(doc)
    caddr = _company_address(doc)
    brand, brand_logo = _brand_logo(doc)
    items = _item_rows(doc, s)
    tax_totals, other_charges = _tax_totals(doc)
    tax_mode = _tax_mode(s, tax_totals, items)
    hsn_rows, hsn_tot = _hsn_summary(items)
    band = _charges_band(doc, s, tax_totals, items, tax_mode["cols"]) \
        if classic and cint(s.get("classic_show_charges_band", 1)) else None

    # place of supply / state codes
    pos = doc.get("place_of_supply") or ""
    cust_gstin = doc.get("billing_address_gstin") or doc.get("customer_gstin") or ""
    gst_cat = doc.get("gst_category") or ""

    # is it inter-state? used for a subtle badge
    inter = flt(tax_totals["igst"]) > 0 and flt(tax_totals["cgst"]) <= 0 \
        or (not flt(tax_totals["igst"]) and not flt(tax_totals["cgst"])
            and any(flt(i.igst_rate) for i in items))

    # title/subtitle
    if kind == "dn":
        title = s.get("dn_title") or "DELIVERY CHALLAN"
        subtitle = s.get("dn_subtitle") or ""
        if doc.get("is_return"):
            title = "SALES RETURN"
    else:
        title = s.get("si_title") or "TAX INVOICE"
        subtitle = s.get("si_subtitle") or ""
        if doc.get("is_return"):
            title = "CREDIT NOTE"
        elif doc.get("is_debit_note"):
            title = "DEBIT NOTE"
        elif not cust_gstin and gst_cat in ("Unregistered", "Overseas"):
            title = s.get("si_title") or "TAX INVOICE"
    if doc.docstatus == 0:
        subtitle = (subtitle + "  •  DRAFT").strip("  •")
    elif doc.docstatus == 2:
        subtitle = (subtitle + "  •  CANCELLED").strip("  •")

    balance = None
    if kind == "si" and cint(s.get("show_customer_balance", 1)):
        balance = _customer_balance(doc)

    # totals
    net_total = flt(doc.get("net_total"))
    taxes_total = flt(doc.get("total_taxes_and_charges"))
    grand = flt(doc.get("grand_total"))
    rounded = flt(doc.get("rounded_total")) or grand
    round_off = flt(doc.get("rounding_adjustment"))
    disc_total = flt(doc.get("discount_amount"))
    other_total = sum(o["amount"] for o in other_charges)

    in_words = doc.get("in_words") or (money_in_words(rounded, currency) if rounded else "")

    ctx = frappe._dict(
        s=s, kind=kind, variant="classic" if classic else "modern", classic=classic,
        band=band, currency=currency,
        colors=frappe._dict(
            accent=_hex_ok(s.get("accent_color"), "#1F4E79"),
            secondary=_hex_ok(s.get("secondary_color"), "#E8F0F8"),
            highlight=_hex_ok(s.get("highlight_color"), "#FFF3B0"),
        ),
        font=s.get("font_family") or "Arial",
        font_size=cint(s.get("base_font_size")) or 10,
        title=title, subtitle=subtitle,
        company=comp, company_address=caddr,
        company_logo=_company_logo(doc, s) if cint(s.get("show_company_logo", 1)) else None,
        brand=brand,
        brand_logo=brand_logo if cint(s.get("show_brand_logo", 1)) and
            (s.get("brand_logo_position") or "") != "Hidden" else None,
        brand_logo_position=s.get("brand_logo_position") or "Right of Company Logo",
        package=_package_details(doc, s) if cint(s.get("show_package_details", 1)) else None,
        qr=s.get("qr_image") if cint(s.get("show_qr")) else None,
        qr_caption=s.get("qr_caption") or "Scan & Pay",
        einvoice=frappe._dict(
            irn=doc.get("irn"), ack_no=doc.get("ack_no") or doc.get("irn_ack_no"),
            ack_date=doc.get("ack_date") or doc.get("irn_ack_date"),
            ewaybill=doc.get("ewaybill"),
            qr_svg=_qr_svg(doc.get("signed_qr_code")) if cint(s.get("show_einvoice_qr", 1)) else "",
        ) if cint(s.get("show_einvoice_block", 1)) else None,
        customer_gstin=cust_gstin, gst_category=gst_cat, place_of_supply=pos,
        inter_state=inter,
        rows=items, tax=tax_mode, tax_totals=tax_totals, other_charges=other_charges,
        other_total=other_total,
        hsn_rows=hsn_rows, hsn_tot=hsn_tot,
        net_total=net_total, taxes_total=taxes_total, grand=grand, rounded=rounded,
        round_off=round_off, disc_total=disc_total, in_words=in_words,
        balance=balance,
        fmt=lambda v: fmt_money(flt(v), currency=currency),
        fmt_qty=lambda v: frappe.format(flt(v), {"fieldtype": "Float", "precision": 2}),
        drcr=lambda v: _fmt_drcr(v, currency),
        terms=_terms(doc, s, kind),
        terms_lines=_terms_lines(_terms(doc, s, kind)),
        package_inspection=(s.get("package_inspection_text") or DEFAULT_PKG)
            if cint(s.get("show_package_inspection", 1)) else "",
        payment_note=s.get("payment_terms_note") or DEFAULT_PAY_NOTE,
        discount_note=s.get("discount_note") or "",
        bank_details=(s.get("bank_details") or "") if cint(s.get("show_bank_details", 1)) else "",
        signatory=(s.get("signatory_text") or "For {company}").replace("{company}", comp.name),
        jurisdiction=s.get("jurisdiction_text") or "",
        footer_contact=_footer_contact(s, comp, caddr),
        min_rows=max(0, (cint(s.get("min_item_rows")) if s.get("min_item_rows") is not None else 6)
                     - ((len(band.rows) + 2 + (1 if band.show_before else 0)) if band else 0)),
        flags=frappe._dict(
            hsn=cint(s.get("show_hsn", 1)), image=cint(s.get("show_item_image")),
            desc=cint(s.get("show_item_description", 1)), uom=cint(s.get("show_uom", 1)),
            unit_price=cint(s.get("show_unit_price", 1)), disc=cint(s.get("show_discount_columns")),
            line_total=cint(s.get("show_line_total", 1)),
            dn_amounts=cint(s.get("dn_show_amounts", 1)),
            contact=cint(s.get("show_contact_details", 1)),
            sales_person=cint(s.get("show_sales_person", 1)),
            transport=cint(s.get("show_transport_block", 1)),
            round_off=cint(s.get("show_round_off", 1)),
            words=cint(s.get("show_amount_in_words", 1)),
            hsn_summary=cint(s.get("show_hsn_summary", 1)),
            other_charges=cint(s.get("show_other_charges", 1)),
            receiver_sig=cint(s.get("show_receiver_signature", 1)),
        ),
    )
    # cross references
    def _uniq(field):
        seen, out = set(), []
        for i in doc.get("items") or []:
            v = i.get(field)
            if v and v not in seen:
                seen.add(v); out.append(v)
        return out
    ctx.so_refs = _uniq("against_sales_order") if kind == "dn" else _uniq("sales_order")
    ctx.dn_refs = _uniq("delivery_note") if kind == "si" else []

    # sales team / contact
    st = doc.get("sales_team") or []
    ctx.sales_person = ", ".join(x.get("sales_person") for x in st if x.get("sales_person"))
    ctx.contact = frappe._dict(
        name=doc.get("contact_display"), mobile=doc.get("contact_mobile"),
        email=doc.get("contact_email"), person=doc.get("contact_person"),
    )
    return ctx

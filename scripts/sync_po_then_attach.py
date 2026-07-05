"""
Bucket A (Zoho PO != DB, Bill = DB): bring the Zoho PO into line with the DB value,
then attach the bill. Mechanism: mirror the BILL's pricing onto the PO line
(rate/tax/discount/inclusive-mode + adjustment) so PO total == bill total (== DB),
then link the bill to the corrected PO (round-off ~0). PO is corrected TOWARD DB,
never away from it. Revert PO to original if the sync overshoots.

Single-line / qty-match subset first.  DRY_RUN prints the plan without writing.
"""
import json, http.client, urllib.parse, time, os, csv, glob

ENV = r"C:\Users\Jogesh Behera\Code file\MaterialDepotDjangoBackend\.env.json"
CAND = r"C:\Users\Jogesh Behera\Code file\Inventory\takebill_vendorfixed_single.csv"
RESULTS = r"C:\Users\Jogesh Behera\Code file\Inventory\attach_results_vendorfixed_tb_single.csv"

DRY_RUN = False
REACTIVATE_INACTIVE = True
LIMIT = 1000
SLEEP = 1.0
DB_TOL = 1.0        # |bill - DB| and |synced PO - DB| must be within this
ONLY_BILL = None


class RateLimited(Exception):
    pass


d = json.load(open(ENV))
CID, CSEC, RT = d["zoho_client_id_haut_luxe"], d["zoho_client_secret_haut_luxe"], d["zoho_refresh_token_haut_luxe"]
ORG = str(d["zoho_org_id_haut_luxe"]); _tok = None; _ts = 0


def token(force=False):
    global _tok, _ts
    if _tok and not force and (time.time() - _ts) < 2400:
        return _tok
    c = http.client.HTTPSConnection("accounts.zoho.in")
    p = urllib.parse.urlencode({"client_id": CID, "client_secret": CSEC, "grant_type": "refresh_token", "refresh_token": RT})
    c.request("POST", f"/oauth/v2/token?{p}"); _tok = json.loads(c.getresponse().read())["access_token"]; _ts = time.time()
    return _tok


def call(method, path, body=None, _retry=True, _net=5):
    sep = "&" if "?" in path else "?"
    full = f"/books/v3{path}{sep}organization_id={ORG}"
    h = {"Authorization": f"Zoho-oauthtoken {token()}"}
    try:
        c = http.client.HTTPSConnection("www.zohoapis.in", timeout=60)
        if body is not None:
            h["content-type"] = "application/json"; c.request(method, full, json.dumps(body), h)
        else:
            c.request(method, full, headers=h)
        r = c.getresponse(); status = r.status; data = json.loads(r.read().decode())
    except (OSError, http.client.HTTPException):
        if _net > 0:
            time.sleep(3 * (6 - _net)); return call(method, path, body, _retry, _net - 1)
        raise
    if status == 401 and _retry:
        token(force=True); return call(method, path, body, _retry=False)
    msg = str(data.get("message", "")).lower()
    if status == 400 and "requested action could not be completed" in msg and _net > 0:
        time.sleep(3 * (6 - _net)); return call(method, path, body, _retry, _net - 1)
    if status == 429 or data.get("code") in (1001,) or "too many requests" in msg:
        raise RateLimited(msg)
    return status, data


def money_eq(a, b, tol=0.005):
    return abs(float(a) - float(b)) < tol


ITEM_STATUS = {}
for _f in glob.glob(r"C:\Users\Jogesh Behera\Code file\Inventory\Raw Files\Item_*\Item*.csv"):
    for _row in csv.DictReader(open(_f, encoding="utf-8")):
        _i = _row["Item ID"].strip()
        if _i:
            ITEM_STATUS[_i] = _row.get("Status", "").strip()


def po_line_body(p_li, po):
    """the PO's own line, echoed back verbatim (for revert)."""
    return {"line_item_id": p_li["line_item_id"], "item_id": p_li["item_id"],
            "account_id": p_li.get("account_id"), "name": p_li.get("name"),
            "description": p_li.get("description", ""), "rate": float(p_li["rate"]),
            "quantity": float(p_li["quantity"]), "discount": p_li.get("discount", 0),
            "unit": p_li.get("unit"), "hsn_or_sac": p_li.get("hsn_or_sac"),
            "tax_id": p_li.get("tax_id"), "location_id": p_li.get("location_id") or po.get("location_id")}


def put_po(po_id, line, adjustment, is_inclusive):
    body = {"line_items": [line], "adjustment": round(float(adjustment or 0), 2),
            "adjustment_description": "Round Off", "discount_type": "item_level",
            "is_inclusive_tax": bool(is_inclusive)}
    st, r = call("PUT", f"/purchaseorders/{po_id}", body)
    ok = st // 100 == 2 and r.get("code") in (0, None)
    return ok, st, r


def sync_and_attach(bill_id, po_id, db_target):
    _, bd = call("GET", f"/bills/{bill_id}"); bill = bd.get("bill")
    _, pd = call("GET", f"/purchaseorders/{po_id}"); po = pd.get("purchaseorder")
    if not bill or not po:
        return "FAIL", "fetch failed", "", ""
    bt, pt = float(bill["total"]), float(po["total"])
    if bill.get("purchaseorder_ids"):
        return "SKIP", "bill already linked", bt, pt
    if len(bill["line_items"]) != 1 or len(po["line_items"]) != 1:
        return "SKIP", "not single-line", bt, pt
    if po.get("billed_status") in ("billed", "partially_billed"):
        return "SKIP", f"PO {po.get('billed_status')}", bt, pt
    if po.get("status") != "open":
        return "SKIP", f"PO status {po.get('status')}", bt, pt
    if bill.get("vendor_id") != po.get("vendor_id"):
        return "SKIP", "vendor mismatch", bt, pt
    b_li, p_li = bill["line_items"][0], po["line_items"][0]
    if float(p_li.get("quantity_billed") or 0) > 0:
        return "SKIP", "PO line partially billed", bt, pt
    bt_pct, pt_pct = b_li.get("tax_percentage"), p_li.get("tax_percentage")
    if bt_pct is not None and pt_pct is not None and round(float(bt_pct), 2) != round(float(pt_pct), 2):
        return "SKIP", f"MANUAL: tax mismatch bill {bt_pct}% / po {pt_pct}%", bt, pt
    DB = round(float(db_target), 2)
    paid = round(bt, 2)                       # a paid bill's total == the amount paid
    # PO must equal DB exactly; the small paid-vs-DB excess rides on the BILL's round-off,
    # but only if it is under Rs.10 (else it is a real discrepancy -> manual).
    if abs(paid - DB) >= 10:
        return "SKIP", f"MANUAL: paid {paid} vs DB {DB} diff >= 10", bt, pt
    if abs(pt - DB) <= 0.05:
        return "SKIP", "PO already == DB", bt, pt

    item_id = p_li["item_id"]
    qty = float(p_li["quantity"])
    tax_pct = float(p_li.get("tax_percentage") or b_li.get("tax_percentage") or 0)
    need_react = str(ITEM_STATUS.get(item_id, "active")).lower() != "active"
    if need_react and not REACTIVATE_INACTIVE:
        return "SKIP", "item inactive", bt, pt

    # --- PO correction: drive PO total to DB via a clean NET rate (no discount line),
    #     so the PO carries no leftover discount and the bill's round-off is the only
    #     place the paid-vs-DB gap lives. rate = DB_pretax / qty. ---
    net_rate = round(DB / (qty * (1 + tax_pct / 100.0)), 6)
    synced_line = {"line_item_id": p_li["line_item_id"], "item_id": item_id,
                   "account_id": p_li.get("account_id") or b_li.get("account_id"),
                   "name": p_li.get("name"), "description": p_li.get("description", ""),
                   "rate": net_rate, "quantity": qty, "discount": 0,
                   "unit": p_li.get("unit") or b_li.get("unit"),
                   "hsn_or_sac": p_li.get("hsn_or_sac") or b_li.get("hsn_or_sac"),
                   "tax_id": b_li.get("tax_id") or p_li.get("tax_id"),
                   "location_id": p_li.get("location_id") or po.get("location_id")}

    if DRY_RUN:
        return ("DRY", f"PO {pt}->{DB} (net rate {p_li['rate']}->{net_rate}); bill stays {paid}, "
                f"excess {round(paid - DB, 2)} on bill round-off; react={need_react}", bt, pt)

    # --- writes: correct PO to DB, verify, then attach ---
    orig_line = po_line_body(p_li, po)
    orig_adj = round(float(po.get("adjustment") or 0), 2)
    orig_incl = bool(po.get("is_inclusive_tax"))
    if need_react:
        call("POST", f"/items/{item_id}/active")

    ok, st, r = put_po(po_id, synced_line, 0, False)
    if not ok:
        if need_react:
            call("POST", f"/items/{item_id}/inactive")
        return "FAIL", f"PO sync PUT {st}: {r.get('message')}", bt, pt
    t1 = float(r.get("purchaseorder", {}).get("total", -1))
    residual = round(DB - t1, 2)              # paisa drift from net-rate rounding / tax split
    if abs(residual) > 5:                     # landed far off (odd tax/cess) -> revert, manual
        put_po(po_id, orig_line, orig_adj, orig_incl)
        if need_react:
            call("POST", f"/items/{item_id}/inactive")
        return "SKIP", f"PO net-rate off: got {t1} vs DB {DB} (reverted)", bt, pt
    new_pt = t1
    if residual != 0:                         # nail PO exactly to DB with a paisa round-off
        ok, st, r = put_po(po_id, synced_line, residual, False)
        if not ok:
            put_po(po_id, orig_line, orig_adj, orig_incl)
            if need_react:
                call("POST", f"/items/{item_id}/inactive")
            return "FAIL", f"PO adj PUT {st}: {r.get('message')}", bt, pt
        new_pt = float(r.get("purchaseorder", {}).get("total", -1))
    if abs(new_pt - DB) > 0.05:               # still off -> revert, manual
        put_po(po_id, orig_line, orig_adj, orig_incl)
        if need_react:
            call("POST", f"/items/{item_id}/inactive")
        return "SKIP", f"PO sync overshoot {new_pt} vs DB {DB} (reverted)", bt, pt

    # attach: mirror the PO net line; hold the bill at paid so (paid - DB) lands on the bill round-off.
    _, pd2 = call("GET", f"/purchaseorders/{po_id}"); po2 = pd2.get("purchaseorder")
    p_li2 = po2["line_items"][0]
    qty2 = float(p_li2["quantity"])
    receive = []
    for rec in po2.get("purchasereceives", []):
        if rec.get("billed_status") == "billed":
            continue
        _, rdd = call("GET", f"/purchasereceives/{rec['receive_id']}")
        for rli in rdd.get("purchasereceive", {}).get("line_items", []):
            if rli.get("item_id") == item_id:
                receive.append({"receive_item_id": rli["line_item_id"], "quantity": qty2}); break
    po_line_total = round(float(po2["total"]) - float(po2.get("adjustment") or 0), 2)
    round_off = round(paid - po_line_total, 2)     # the paid-vs-DB excess, on the bill
    new_line = {"purchaseorder_item_id": p_li2["line_item_id"], "item_id": item_id,
                "account_id": p_li2.get("account_id") or b_li.get("account_id"),
                "name": p_li2.get("name"), "description": "",
                "rate": float(p_li2["rate"]), "quantity": qty2, "discount": 0,
                "unit": p_li2.get("unit") or b_li.get("unit"),
                "hsn_or_sac": p_li2.get("hsn_or_sac") or b_li.get("hsn_or_sac"),
                "tax_id": b_li.get("tax_id") or p_li2.get("tax_id"),
                "location_id": p_li2.get("location_id") or po2.get("location_id"),
                "item_order": 1, "is_billable": False}
    if receive:
        new_line["receive_line_items"] = receive
    ref = bill.get("reference_number") or ""
    if po2["purchaseorder_number"] not in ref:
        ref = (ref + "," + po2["purchaseorder_number"]).strip(",")
    PRESERVE = ["vendor_id", "date", "due_date", "notes", "terms", "exchange_rate", "is_inclusive_tax",
                "is_item_level_tax_calc", "payment_terms", "payment_terms_label", "gst_treatment", "gst_no",
                "source_of_supply", "destination_of_supply", "template_id", "billing_address_id"]
    bu = {"bill_number": bill["bill_number"], "reference_number": ref, "line_items": [new_line],
          "location_id": po2.get("location_id") or bill.get("location_id"),
          "adjustment": round_off, "adjustment_description": "Round Off", "discount_type": "item_level"}
    for k in PRESERVE:
        if bill.get(k) not in (None, ""):
            bu[k] = bill[k]
    if bill.get("custom_fields"):
        bu["custom_fields"] = [{"customfield_id": c["customfield_id"], "value": c.get("value")} for c in bill["custom_fields"]]

    st, r = call("PUT", f"/bills/{bill_id}", bu)
    bill_ok = st // 100 == 2 and r.get("code") in (0, None)
    if bill_ok:
        b2 = r.get("bill", {})
        resid = round(paid - float(b2.get("total", paid)), 2)
        if 0.005 < abs(resid) <= 1.0:
            bu["adjustment"] = round(round_off + resid, 2)
            st, r = call("PUT", f"/bills/{bill_id}", bu)
            bill_ok = st // 100 == 2 and r.get("code") in (0, None)
    if need_react:
        call("POST", f"/items/{item_id}/inactive")
    if not bill_ok:
        return "FAIL", f"attach PUT {st}: {r.get('message')} (PO already synced to DB)", bt, new_pt
    b2 = r.get("bill", {})
    ok2 = (money_eq(b2.get("total", -1), paid) and len(b2.get("line_items", [])) == 1 and b2.get("purchaseorder_ids"))
    return ("OK" if ok2 else "CHECK",
            f"PO {pt}->{new_pt}(=DB); excess {round(paid - DB, 2)} on bill" if ok2
            else f"verify billtot={b2.get('total')} linked={bool(b2.get('purchaseorder_ids'))}",
            b2.get("total", paid), new_pt)


def main():
    cands = list(csv.DictReader(open(CAND, encoding="utf-8-sig")))
    done = set()
    if os.path.exists(RESULTS):
        for r in csv.DictReader(open(RESULTS, encoding="utf-8-sig")):
            done.add(r["bill_id"])
    fh = None if DRY_RUN else open(RESULTS, "a", newline="", encoding="utf-8-sig")
    w = None if DRY_RUN else csv.DictWriter(fh, fieldnames=["bill_id", "po_id", "bill_number", "po_number",
                                            "db_amount", "result", "reason", "bill_total", "po_total"])
    if w and not done:
        w.writeheader()
    counts, n = {}, 0
    for c in cands:
        if ONLY_BILL and c["bill_id"] != ONLY_BILL:
            continue
        if c["bill_id"] in done:
            continue
        if n >= LIMIT:
            break
        n += 1
        try:
            res, reason, bt, pt = sync_and_attach(c["bill_id"], c["po_id"], c["db_amount"])
        except RateLimited as e:
            print(f"\nRATE LIMITED ({e}) -- stopping cleanly."); n -= 1; break
        except Exception as e:
            res, reason, bt, pt = "FAIL", f"exception: {e}", "", ""
        counts[res] = counts.get(res, 0) + 1
        if w:
            w.writerow({"bill_id": c["bill_id"], "po_id": c["po_id"], "bill_number": c["bill_number"],
                        "po_number": c["po_number"], "db_amount": c["db_amount"], "result": res,
                        "reason": reason, "bill_total": bt, "po_total": pt}); fh.flush()
        print(f"[{n}] {c['bill_number']}/{c['po_number']} (DB {c['db_amount']}) -> {res} {reason}")
        time.sleep(SLEEP if not DRY_RUN else 0)
    if fh:
        fh.close()
    print("\nSUMMARY:", counts, "| processed:", n, "| DRY_RUN:", DRY_RUN)


if __name__ == "__main__":
    main()

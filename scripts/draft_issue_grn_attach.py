"""
Draft-PO (delivered, inventory) flow: sync PO to DB -> issue (draft->open) -> create GRN
(purchase receive, full qty) -> attach bill (mirror PO line + receive link, excess on bill).
Single-line first. Reuses infra from sync_po_then_attach.
"""
import importlib.util, csv, os, time

SP = r"C:\Users\JOGESH~1\AppData\Local\Temp\claude\c--Users-Jogesh-Behera-Code-file-MaterialDepotDjangoBackend\2215d20a-39d5-4160-89ab-18486807898c\scratchpad\sync_po_then_attach.py"
spec = importlib.util.spec_from_file_location("m", SP)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
call, money_eq, ITEM_STATUS, RateLimited, put_po, po_line_body = (
    m.call, m.money_eq, m.ITEM_STATUS, m.RateLimited, m.put_po, m.po_line_body)

INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
CAND = os.path.join(INV, "takebill_extra_single.csv")
RESULTS = os.path.join(INV, "attach_results_takebill_extra_single.csv")
DRY_RUN = False
REACTIVATE_INACTIVE = True
LIMIT = 1000
SLEEP = 1.0
ONLY_BILL = None


import json, http.client


def call_inv(method, path, body=None, _net=5):
    """Zoho INVENTORY API (not /books/v3) — used for purchase receives (GRN)."""
    sep = "&" if "?" in path else "?"
    full = f"{path}{sep}organization_id={m.ORG}"
    h = {"Authorization": f"Zoho-oauthtoken {m.token()}"}
    try:
        c = http.client.HTTPSConnection("www.zohoapis.in", timeout=60)
        if body is not None:
            h["content-type"] = "application/json"; c.request(method, full, json.dumps(body), h)
        else:
            c.request(method, full, headers=h)
        r = c.getresponse(); status = r.status; data = json.loads(r.read().decode())
    except (OSError, http.client.HTTPException):
        if _net > 0:
            time.sleep(3 * (6 - _net)); return call_inv(method, path, body, _net - 1)
        raise
    if status == 401:
        m.token(force=True); return call_inv(method, path, body, _net - 1)
    msg = str(data.get("message", "")).lower()
    if status == 400 and "requested action could not be completed" in msg and _net > 0:
        time.sleep(3 * (6 - _net)); return call_inv(method, path, body, _net - 1)
    if status == 429 or data.get("code") in (1001,) or "too many requests" in msg:
        raise RateLimited(msg)
    return status, data


def grn_endpoint(po_id):
    return f"/inventory/v1/purchasereceives?purchaseorder_id={po_id}"


def draft_flow(bill_id, po_id, db_target):
    _, bd = call("GET", f"/bills/{bill_id}"); bill = bd.get("bill")
    _, pd = call("GET", f"/purchaseorders/{po_id}"); po = pd.get("purchaseorder")
    if not bill or not po:
        return "FAIL", "fetch failed", "", ""
    bt, pt = float(bill["total"]), float(po["total"])
    if bill.get("purchaseorder_ids"):
        return "SKIP", "bill already linked", bt, pt
    if len(bill["line_items"]) != 1 or len(po["line_items"]) != 1:
        return "SKIP", "not single-line", bt, pt
    if po.get("status") not in ("draft", "open"):
        return "SKIP", f"PO status {po.get('status')}", bt, pt
    if po.get("billed_status") in ("billed", "partially_billed"):
        return "SKIP", f"PO {po.get('billed_status')}", bt, pt
    if bill.get("vendor_id") != po.get("vendor_id"):
        return "SKIP", "vendor mismatch", bt, pt
    b_li, p_li = bill["line_items"][0], po["line_items"][0]
    bt_pct, pt_pct = b_li.get("tax_percentage"), p_li.get("tax_percentage")
    if bt_pct is not None and pt_pct is not None and round(float(bt_pct), 2) != round(float(pt_pct), 2):
        return "SKIP", f"MANUAL: tax mismatch bill {bt_pct}%/po {pt_pct}%", bt, pt

    DB = round(float(db_target), 2)
    paid = round(bt, 2)
    if abs(paid - DB) >= 10:
        return "SKIP", f"MANUAL: paid {paid} vs DB {DB} diff >= 10", bt, pt
    item_id = p_li["item_id"]
    qty = float(p_li["quantity"])
    tax_pct = float(p_li.get("tax_percentage") or b_li.get("tax_percentage") or 0)
    need_react = str(ITEM_STATUS.get(item_id, "active")).lower() != "active"
    if need_react and not REACTIVATE_INACTIVE:
        return "SKIP", "item inactive", bt, pt
    grn_date = bill.get("date")

    if DRY_RUN:
        net = round(DB / (qty * (1 + tax_pct / 100.0)), 4)
        return ("DRY", f"sync PO {pt}->{DB} (rate {p_li['rate']}->{net}); issue({po.get('status')}); "
                f"GRN qty {qty} @ {grn_date}; attach bill {paid} (excess {round(paid-DB,2)}); react={need_react}", bt, pt)

    # ---- writes (IDEMPOTENT: only the missing steps; safe to re-run partial failures) ----
    has_grn = bool(po.get("purchasereceives"))
    orig_line = po_line_body(p_li, po); orig_adj = round(float(po.get("adjustment") or 0), 2); orig_incl = bool(po.get("is_inclusive_tax"))
    if need_react:
        call("POST", f"/items/{item_id}/active")
    new_pt = pt

    # 1) sync PO to DB (net rate) — only if no GRN yet (line still editable) and not already at DB
    if not has_grn and abs(pt - DB) > 0.05:
        net_rate = round(DB / (qty * (1 + tax_pct / 100.0)), 6)
        synced = {"line_item_id": p_li["line_item_id"], "item_id": item_id,
                  "account_id": p_li.get("account_id") or b_li.get("account_id"), "name": p_li.get("name"),
                  "description": p_li.get("description", ""), "rate": net_rate, "quantity": qty, "discount": 0,
                  "unit": p_li.get("unit") or b_li.get("unit"), "hsn_or_sac": p_li.get("hsn_or_sac") or b_li.get("hsn_or_sac"),
                  "tax_id": b_li.get("tax_id") or p_li.get("tax_id"), "location_id": p_li.get("location_id") or po.get("location_id")}
        ok, st, r = put_po(po_id, synced, 0, False)
        if not ok:
            if need_react: call("POST", f"/items/{item_id}/inactive")
            return "FAIL", f"PO sync PUT {st}: {r.get('message')}", bt, pt
        t1 = float(r.get("purchaseorder", {}).get("total", -1)); residual = round(DB - t1, 2)
        if abs(residual) > 5:
            put_po(po_id, orig_line, orig_adj, orig_incl)
            if need_react: call("POST", f"/items/{item_id}/inactive")
            return "SKIP", f"PO net-rate off {t1} vs DB {DB} (reverted)", bt, pt
        if residual != 0:
            ok, st, r = put_po(po_id, synced, residual, False)
        new_pt = float(r.get("purchaseorder", {}).get("total", -1))
        if abs(new_pt - DB) > 0.05:
            put_po(po_id, orig_line, orig_adj, orig_incl)
            if need_react: call("POST", f"/items/{item_id}/inactive")
            return "SKIP", f"PO sync overshoot {new_pt} vs DB {DB} (reverted)", bt, pt

    # 2) issue the PO (draft -> open) if needed
    _, pd1 = call("GET", f"/purchaseorders/{po_id}"); po1 = pd1["purchaseorder"]
    if po1.get("status") == "draft":
        st, r = call("POST", f"/purchaseorders/{po_id}/status/open")
        if not (st // 100 == 2 and r.get("code") in (0, None)):
            if need_react: call("POST", f"/items/{item_id}/inactive")
            return "FAIL", f"issue PO {st}: {r.get('message')}", bt, new_pt

    # 3) create GRN (full qty) — only if none exists
    if not po1.get("purchasereceives"):
        pl1 = po1["line_items"][0]
        grn = {"purchaseorder_id": po_id, "receive_number": po1["purchaseorder_number"],
               "date": grn_date, "notes": "",
               "line_items": [{"line_item_id": pl1["line_item_id"], "item_id": item_id,
                               "quantity": str(qty), "item_order": 1}]}
        st, r = call_inv("POST", grn_endpoint(po_id), grn)
        if not (st // 100 == 2 and r.get("code") in (0, None)):
            if need_react: call("POST", f"/items/{item_id}/inactive")
            return "FAIL", f"GRN {st}: {r.get('message')}", bt, new_pt

    # 4) attach bill (mirror PO line + receive link; excess on bill)
    _, pd2 = call("GET", f"/purchaseorders/{po_id}"); po2 = pd2["purchaseorder"]
    p_li2 = po2["line_items"][0]
    receive = []
    for rec in po2.get("purchasereceives", []):
        if rec.get("billed_status") == "billed":
            continue
        _, rdd = call("GET", f"/purchasereceives/{rec['receive_id']}")
        for rli in rdd.get("purchasereceive", {}).get("line_items", []):
            if rli.get("item_id") == item_id:
                receive.append({"receive_item_id": rli["line_item_id"], "quantity": qty}); break
    po_line_total = round(float(po2["total"]) - float(po2.get("adjustment") or 0), 2)
    round_off = round(paid - po_line_total, 2)
    new_line = {"purchaseorder_item_id": p_li2["line_item_id"], "item_id": item_id,
                "account_id": p_li2.get("account_id") or b_li.get("account_id"), "name": p_li2.get("name"),
                "description": "", "rate": float(p_li2["rate"]), "quantity": qty, "discount": 0,
                "unit": p_li2.get("unit") or b_li.get("unit"), "hsn_or_sac": p_li2.get("hsn_or_sac") or b_li.get("hsn_or_sac"),
                "tax_id": b_li.get("tax_id") or p_li2.get("tax_id"),
                "location_id": p_li2.get("location_id") or po2.get("location_id"), "item_order": 1, "is_billable": False}
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
        return "FAIL", f"attach PUT {st}: {r.get('message')} (PO synced+issued+GRN done)", bt, new_pt
    b2 = r.get("bill", {})
    ok2 = money_eq(b2.get("total", -1), paid) and len(b2.get("line_items", [])) == 1 and b2.get("purchaseorder_ids")
    return ("OK" if ok2 else "CHECK",
            f"issued+GRN+attached; PO={new_pt}(=DB) excess {round(paid-DB,2)}" if ok2
            else f"verify billtot={b2.get('total')} linked={bool(b2.get('purchaseorder_ids'))}",
            b2.get("total", paid), new_pt)


def main():
    cands = list(csv.DictReader(open(CAND, encoding="utf-8-sig")))
    cands = [c for c in cands if c["n_lines"] == "1"]   # single-line first
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
            res, reason, b2, p2 = draft_flow(c["bill_id"], c["po_id"], c["db_amount"])
        except RateLimited as e:
            print(f"\nRATE LIMITED ({e}) -- stopping."); n -= 1; break
        except Exception as e:
            res, reason, b2, p2 = "FAIL", f"exception: {e}", "", ""
        counts[res] = counts.get(res, 0) + 1
        if w:
            w.writerow({"bill_id": c["bill_id"], "po_id": c["po_id"], "bill_number": c["bill_number"],
                        "po_number": c["po_number"], "db_amount": c["db_amount"], "result": res,
                        "reason": reason, "bill_total": b2, "po_total": p2}); fh.flush()
        print(f"[{n}] {c['bill_number']}/{c['po_number']} (DB {c['db_amount']}) -> {res} {reason}")
        time.sleep(SLEEP if not DRY_RUN else 0)
    if fh:
        fh.close()
    print("\nSUMMARY:", counts, "| processed:", n, "| DRY_RUN:", DRY_RUN)


if __name__ == "__main__":
    main()

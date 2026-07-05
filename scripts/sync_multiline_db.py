"""Multi-line Bucket A (Zoho PO != DB, Bill = DB): drive the multi-line PO total to DB,
then attach. Each PO line is rebuilt from its matched BILL line as a clean NET rate
(no discount), scaled by DB/paid so the PO line-sum lands on DB; a paisa PO adjustment
nails DB exactly. The bill is held at paid, so the (paid - DB) excess lands on the bill's
round-off. PO corrected TOWARD DB; reverts on overshoot. Reuses infra from sync_po_then_attach.
"""
import importlib.util, csv, os, time

SP = r"C:\Users\JOGESH~1\AppData\Local\Temp\claude\c--Users-Jogesh-Behera-Code-file-MaterialDepotDjangoBackend\2215d20a-39d5-4160-89ab-18486807898c\scratchpad\sync_po_then_attach.py"
spec = importlib.util.spec_from_file_location("m", SP)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
call, money_eq, ITEM_STATUS, RateLimited = m.call, m.money_eq, m.ITEM_STATUS, m.RateLimited

INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
CAND = os.path.join(INV, "takebill_qtymm_multi.csv")
RESULTS = os.path.join(INV, "attach_results_takebill_qtymm_multi.csv")
DRY_RUN = False
REACTIVATE_INACTIVE = True
LIMIT = 1000
SLEEP = 1.0
ONLY_BILL = None


def put_po_lines(po_id, lines, adjustment, is_inclusive):
    body = {"line_items": lines, "adjustment": round(float(adjustment or 0), 2),
            "adjustment_description": "Round Off", "discount_type": "item_level",
            "is_inclusive_tax": bool(is_inclusive)}
    st, r = call("PUT", f"/purchaseorders/{po_id}", body)
    return (st // 100 == 2 and r.get("code") in (0, None)), st, r


def echo_line(pl, po):
    return {"line_item_id": pl["line_item_id"], "item_id": pl["item_id"],
            "account_id": pl.get("account_id"), "name": pl.get("name"),
            "description": pl.get("description", ""), "rate": float(pl["rate"]),
            "quantity": float(pl["quantity"]), "discount": pl.get("discount", 0),
            "unit": pl.get("unit"), "hsn_or_sac": pl.get("hsn_or_sac"),
            "tax_id": pl.get("tax_id"), "location_id": pl.get("location_id") or po.get("location_id")}


def qkey(x):
    return round(float(x["quantity"]), 2)


def sync_and_attach_multi(bill_id, po_id, db_target):
    _, bd = call("GET", f"/bills/{bill_id}"); bill = bd.get("bill")
    _, pd = call("GET", f"/purchaseorders/{po_id}"); po = pd.get("purchaseorder")
    if not bill or not po:
        return "FAIL", "fetch failed", "", ""
    bt, pt = float(bill["total"]), float(po["total"])
    bls, pls = bill["line_items"], po["line_items"]
    if bill.get("purchaseorder_ids"):
        return "SKIP", "bill already linked", bt, pt
    if len(bls) < 2 or len(pls) != len(bls):
        return "SKIP", f"line count bill={len(bls)} po={len(pls)}", bt, pt
    if po.get("billed_status") in ("billed", "partially_billed"):
        return "SKIP", f"PO {po.get('billed_status')}", bt, pt
    if po.get("status") != "open":
        return "SKIP", f"PO status {po.get('status')}", bt, pt
    if bill.get("vendor_id") != po.get("vendor_id"):
        return "SKIP", "vendor mismatch", bt, pt
    if any(float(pl.get("quantity_billed") or 0) > 0 for pl in pls):
        return "SKIP", "PO line partially billed", bt, pt
    if sorted(round(float(x["quantity"]), 2) for x in bls) != sorted(round(float(x["quantity"]), 2) for x in pls):
        return "SKIP", "qty multiset mismatch", bt, pt

    DB = round(float(db_target), 2)
    paid = round(bt, 2)
    if abs(paid - DB) >= 10:
        return "SKIP", f"MANUAL: paid {paid} vs DB {DB} diff >= 10", bt, pt
    if abs(pt - DB) <= 0.05:
        return "SKIP", "PO already == DB", bt, pt
    if paid <= 0:
        return "SKIP", "bill total <= 0", bt, pt

    # bill and PO can use different item_ids for the same product; pair by sorted qty
    # (qty multiset already verified equal). For qty ties, attribution doesn't affect the total.
    scale = DB / paid
    pls_sorted = sorted(pls, key=qkey)
    bls_sorted = sorted(bls, key=qkey)
    synced, item_ids = [], []
    for i, (pl, bl) in enumerate(zip(pls_sorted, bls_sorted), 1):
        bt_pct, pt_pct = bl.get("tax_percentage"), pl.get("tax_percentage")
        if bt_pct is not None and pt_pct is not None and round(float(bt_pct), 2) != round(float(pt_pct), 2):
            return "SKIP", f"MANUAL: tax mismatch item {pl.get('item_id')} bill {bt_pct}%/po {pt_pct}%", bt, pt
        qy = float(bl["quantity"]) or 1.0
        net_pretax_unit = float(bl.get("item_total", 0)) / qy      # post-discount, pre-tax, per unit
        scaled_rate = round(net_pretax_unit * scale, 6)
        item_ids.append(pl["item_id"])
        synced.append({"line_item_id": pl["line_item_id"], "item_id": pl["item_id"],
                       "account_id": pl.get("account_id") or bl.get("account_id"),
                       "name": pl.get("name"), "description": pl.get("description", ""),
                       "rate": scaled_rate, "quantity": float(pl["quantity"]), "discount": 0,
                       "unit": pl.get("unit") or bl.get("unit"),
                       "hsn_or_sac": pl.get("hsn_or_sac") or bl.get("hsn_or_sac"),
                       "tax_id": bl.get("tax_id") or pl.get("tax_id"),
                       "location_id": pl.get("location_id") or po.get("location_id"), "item_order": i})
    inactive = [i for i in set(item_ids) if str(ITEM_STATUS.get(i, "active")).lower() != "active"]
    if inactive and not REACTIVATE_INACTIVE:
        return "SKIP", "inactive item", bt, pt

    if DRY_RUN:
        return ("DRY", f"PO {pt}->{DB} ({len(synced)} lines scaled x{round(scale,6)}); bill {paid}, "
                f"excess {round(paid-DB,2)} on bill; inactive={len(inactive)}", bt, pt)

    orig_lines = [echo_line(pl, po) for pl in pls]
    orig_adj = round(float(po.get("adjustment") or 0), 2)
    orig_incl = bool(po.get("is_inclusive_tax"))
    for iid in inactive:
        call("POST", f"/items/{iid}/active")

    ok, st, r = put_po_lines(po_id, synced, 0, False)
    if not ok:
        put_po_lines(po_id, orig_lines, orig_adj, orig_incl)
        for iid in inactive:
            call("POST", f"/items/{iid}/inactive")
        return "FAIL", f"PO sync PUT {st}: {r.get('message')}", bt, pt
    t1 = float(r.get("purchaseorder", {}).get("total", -1))
    residual = round(DB - t1, 2)
    if abs(residual) > 5:
        put_po_lines(po_id, orig_lines, orig_adj, orig_incl)
        for iid in inactive:
            call("POST", f"/items/{iid}/inactive")
        return "SKIP", f"PO scale off: got {t1} vs DB {DB} (reverted)", bt, pt
    new_pt = t1
    if residual != 0:
        ok, st, r = put_po_lines(po_id, synced, residual, False)
        if not ok:
            put_po_lines(po_id, orig_lines, orig_adj, orig_incl)
            for iid in inactive:
                call("POST", f"/items/{iid}/inactive")
            return "FAIL", f"PO adj PUT {st}: {r.get('message')}", bt, pt
        new_pt = float(r.get("purchaseorder", {}).get("total", -1))
    if abs(new_pt - DB) > 0.05:
        put_po_lines(po_id, orig_lines, orig_adj, orig_incl)
        for iid in inactive:
            call("POST", f"/items/{iid}/inactive")
        return "SKIP", f"PO sync overshoot {new_pt} vs DB {DB} (reverted)", bt, pt

    # attach: mirror the corrected PO lines onto the bill; hold bill at paid (excess on bill round-off)
    _, pd2 = call("GET", f"/purchaseorders/{po_id}"); po2 = pd2.get("purchaseorder")
    pls2 = po2["line_items"]
    recq = {}
    for rec in po2.get("purchasereceives", []):
        if rec.get("billed_status") == "billed":
            continue
        _, rdd = call("GET", f"/purchasereceives/{rec['receive_id']}")
        for rli in rdd.get("purchasereceive", {}).get("line_items", []):
            recq.setdefault(rli.get("item_id"), []).append(rli["line_item_id"])
    new_lines = []
    for i, pl in enumerate(pls2, 1):
        nl = {"purchaseorder_item_id": pl["line_item_id"], "item_id": pl["item_id"],
              "account_id": pl.get("account_id"), "name": pl.get("name"), "description": "",
              "rate": float(pl["rate"]), "quantity": float(pl["quantity"]), "discount": 0,
              "unit": pl.get("unit"), "hsn_or_sac": pl.get("hsn_or_sac"), "tax_id": pl.get("tax_id"),
              "location_id": pl.get("location_id") or po2.get("location_id"),
              "item_order": i, "is_billable": False}
        q = recq.get(pl["item_id"])
        if q:
            nl["receive_line_items"] = [{"receive_item_id": q.pop(0), "quantity": float(pl["quantity"])}]
        new_lines.append(nl)
    po_line_total = round(float(po2["total"]) - float(po2.get("adjustment") or 0), 2)
    round_off = round(paid - po_line_total, 2)
    ref = bill.get("reference_number") or ""
    if po2["purchaseorder_number"] not in ref:
        ref = (ref + "," + po2["purchaseorder_number"]).strip(",")
    PRESERVE = ["vendor_id", "date", "due_date", "notes", "terms", "exchange_rate", "is_inclusive_tax",
                "is_item_level_tax_calc", "payment_terms", "payment_terms_label", "gst_treatment", "gst_no",
                "source_of_supply", "destination_of_supply", "template_id", "billing_address_id"]
    bu = {"bill_number": bill["bill_number"], "reference_number": ref, "line_items": new_lines,
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
    for iid in inactive:
        call("POST", f"/items/{iid}/inactive")
    if not bill_ok:
        return "FAIL", f"attach PUT {st}: {r.get('message')} (PO already synced to DB)", bt, new_pt
    b2 = r.get("bill", {})
    ok2 = (money_eq(b2.get("total", -1), paid) and len(b2.get("line_items", [])) == len(pls2) and b2.get("purchaseorder_ids"))
    return ("OK" if ok2 else "CHECK",
            f"PO {pt}->{new_pt}(=DB); excess {round(paid-DB,2)} on bill" if ok2
            else f"verify billtot={b2.get('total')} lines={len(b2.get('line_items', []))} linked={bool(b2.get('purchaseorder_ids'))}",
            b2.get("total", paid), new_pt)


def main():
    cands = list(csv.DictReader(open(CAND, encoding="utf-8-sig")))
    done = set()
    if os.path.exists(RESULTS):
        for r in csv.DictReader(open(RESULTS, encoding="utf-8-sig")):
            done.add(r["bill_id"])
    fh = None if DRY_RUN else open(RESULTS, "a", newline="", encoding="utf-8-sig")
    w = None if DRY_RUN else csv.DictWriter(fh, fieldnames=["bill_id", "po_id", "bill_number", "po_number",
                                            "db_amount", "n_lines", "result", "reason", "bill_total", "po_total"])
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
            res, reason, bt2, pt2 = sync_and_attach_multi(c["bill_id"], c["po_id"], c["db_amount"])
        except RateLimited as e:
            print(f"\nRATE LIMITED ({e}) -- stopping."); n -= 1; break
        except Exception as e:
            res, reason, bt2, pt2 = "FAIL", f"exception: {e}", "", ""
        counts[res] = counts.get(res, 0) + 1
        if w:
            w.writerow({"bill_id": c["bill_id"], "po_id": c["po_id"], "bill_number": c["bill_number"],
                        "po_number": c["po_number"], "db_amount": c["db_amount"], "n_lines": c["n_lines"],
                        "result": res, "reason": reason, "bill_total": bt2, "po_total": pt2}); fh.flush()
        print(f"[{n}] {c['bill_number']}/{c['po_number']} ({c['n_lines']}ln, DB {c['db_amount']}) -> {res} {reason}")
        time.sleep(SLEEP if not DRY_RUN else 0)
    if fh:
        fh.close()
    print("\nSUMMARY:", counts, "| processed:", n, "| DRY_RUN:", DRY_RUN)


if __name__ == "__main__":
    main()

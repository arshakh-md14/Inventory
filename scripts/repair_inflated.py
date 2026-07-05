"""Repair the 6 CHECK (inflated) bills from the amount-diff run: their PO is already
synced (= DB) but the bill line dropped the discount, inflating the bill. Re-PUT each
bill to mirror the PO line EXACTLY (discount preserved) so bill total == PO total == DB.
Then flip their row in attach_results_amountdiff.csv from CHECK to OK."""
import importlib.util, csv, os

SP = r"C:\Users\JOGESH~1\AppData\Local\Temp\claude\c--Users-Jogesh-Behera-Code-file-MaterialDepotDjangoBackend\2215d20a-39d5-4160-89ab-18486807898c\scratchpad\sync_po_then_attach.py"
spec = importlib.util.spec_from_file_location("m", SP)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
call, money_eq = m.call, m.money_eq
RESULTS = r"C:\Users\Jogesh Behera\Code file\Inventory\attach_results_amountdiff.csv"

rows = list(csv.DictReader(open(RESULTS, encoding="utf-8-sig")))
targets = [r for r in rows if r["result"] == "CHECK"]
print("repairing", len(targets), "inflated bills")


def repair(bill_id, po_id, db_target):
    _, bd = call("GET", f"/bills/{bill_id}"); bill = bd["bill"]
    _, pd = call("GET", f"/purchaseorders/{po_id}"); po = pd["purchaseorder"]
    pl = po["line_items"][0]
    item_id = pl["item_id"]
    need_react = str(m.ITEM_STATUS.get(item_id, "active")).lower() != "active"
    qty = float(pl["quantity"])
    # receive links: editing an already-linked bill requires re-supplying ALL receive
    # lines for the item (they now read as 'billed' by this very bill).
    receive = []
    for rec in po.get("purchasereceives", []):
        _, rdd = call("GET", f"/purchasereceives/{rec['receive_id']}")
        for rli in rdd.get("purchasereceive", {}).get("line_items", []):
            if rli.get("item_id") == item_id:
                receive.append({"receive_item_id": rli["line_item_id"], "quantity": float(rli["quantity"])})
    po_line_total = round(float(po["total"]) - float(po.get("adjustment") or 0), 2)
    # bill total MUST equal the amount actually paid (else "payment exceeds due").
    paid = float(bill.get("payment_made") or 0)
    target = round(paid, 2) if paid > 0 else float(db_target)
    round_off = round(target - po_line_total, 2)
    b_li = bill["line_items"][0]
    new_line = {"purchaseorder_item_id": pl["line_item_id"], "item_id": item_id,
                "account_id": pl.get("account_id"), "name": pl.get("name"), "description": "",
                "rate": float(pl["rate"]), "quantity": qty,
                "discount": pl.get("discount", 0),           # <-- preserve the discount
                "unit": pl.get("unit"), "hsn_or_sac": pl.get("hsn_or_sac"),
                "tax_id": b_li.get("tax_id") or pl.get("tax_id"),
                "location_id": pl.get("location_id") or po.get("location_id"),
                "item_order": 1, "is_billable": False}
    if receive:
        new_line["receive_line_items"] = receive
    ref = bill.get("reference_number") or ""
    PRESERVE = ["vendor_id", "date", "due_date", "notes", "terms", "exchange_rate", "is_inclusive_tax",
                "is_item_level_tax_calc", "payment_terms", "payment_terms_label", "gst_treatment", "gst_no",
                "source_of_supply", "destination_of_supply", "template_id", "billing_address_id"]
    bu = {"bill_number": bill["bill_number"], "reference_number": ref, "line_items": [new_line],
          "location_id": po.get("location_id") or bill.get("location_id"),
          "adjustment": round_off, "adjustment_description": "Round Off", "discount_type": "item_level"}
    for k in PRESERVE:
        if bill.get(k) not in (None, ""):
            bu[k] = bill[k]
    if bill.get("custom_fields"):
        bu["custom_fields"] = [{"customfield_id": c["customfield_id"], "value": c.get("value")} for c in bill["custom_fields"]]
    if need_react:
        call("POST", f"/items/{item_id}/active")
    st, r = call("PUT", f"/bills/{bill_id}", bu)
    ok = st // 100 == 2 and r.get("code") in (0, None)
    if ok:
        b2 = r.get("bill", {})
        resid = round(target - float(b2.get("total", target)), 2)
        if 0.005 < abs(resid) <= 1.0:
            bu["adjustment"] = round(round_off + resid, 2)
            st, r = call("PUT", f"/bills/{bill_id}", bu)
            ok = st // 100 == 2 and r.get("code") in (0, None)
    if need_react:
        call("POST", f"/items/{item_id}/inactive")
    if not ok:
        return "FAIL", f"repair PUT {st}: {r.get('message')}", ""
    b2 = r.get("bill", {})
    good = money_eq(b2.get("total", -1), target, 0.02) and b2.get("purchaseorder_ids")
    return ("OK" if good else "CHECK"), f"repaired total={b2.get('total')} (target {target})", b2.get("total")


for r in targets:
    res, reason, tot = repair(r["bill_id"], r["po_id"], r["db_amount"])
    print(f"  {r['bill_number']}/{r['po_number']} -> {res} {reason}")
    if res == "OK":
        r["result"] = "OK"; r["reason"] = "repaired: discount preserved"; r["bill_total"] = tot

with open(RESULTS, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
print("done")

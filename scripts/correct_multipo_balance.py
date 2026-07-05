"""The multi-PO payment-drift fix bumped the round-off a paisa too high, leaving a ~Rs.0.01
balance (bill shows 'overdue'). Re-PUT each affected (CHECK) bill with the adjustment trimmed
so bill total == payment_made exactly (balance 0 -> paid). Re-supplies all PO lines + receives."""
import importlib.util, csv, os
from collections import defaultdict

SP = r"C:\Users\JOGESH~1\AppData\Local\Temp\claude\c--Users-Jogesh-Behera-Code-file-MaterialDepotDjangoBackend\2215d20a-39d5-4160-89ab-18486807898c\scratchpad\sync_po_then_attach.py"
spec = importlib.util.spec_from_file_location("m", SP); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
call, money_eq, ITEM_STATUS = m.call, m.money_eq, m.ITEM_STATUS
INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
RESULTS = os.path.join(INV, "attach_results_multipo.csv")

cand = {c["bill_number"]: c for c in csv.DictReader(open(os.path.join(INV, "multipo_candidates.csv"), encoding="utf-8-sig"))}
rows = list(csv.DictReader(open(RESULTS, encoding="utf-8-sig")))
targets = [r for r in rows if r["result"] == "CHECK"]
print("CHECK bills to correct:", len(targets))


def correct(bill_id, po_ids):
    _, bd = call("GET", f"/bills/{bill_id}"); bill = bd["bill"]
    paytarget = round(float(bill.get("payment_made") or 0), 2) or round(float(bill["total"]), 2)
    inactive = []
    new_lines = []; order = 1
    for pid in po_ids:
        _, pd = call("GET", f"/purchaseorders/{pid}"); p2 = pd["purchaseorder"]
        recq = defaultdict(list)
        for rec in p2.get("purchasereceives", []):
            _, rdd = call("GET", f"/purchasereceives/{rec['receive_id']}")
            for rli in rdd.get("purchasereceive", {}).get("line_items", []):
                recq[rli.get("item_id")].append(rli["line_item_id"])
        for pl in p2["line_items"]:
            iid = pl.get("item_id")
            if iid and str(ITEM_STATUS.get(iid, "active")).lower() != "active":
                inactive.append(iid)
            nl = {"purchaseorder_item_id": pl["line_item_id"], "item_id": iid, "account_id": pl.get("account_id"),
                  "name": pl.get("name"), "description": pl.get("description", ""), "rate": float(pl["rate"]),
                  "quantity": float(pl["quantity"]), "discount": 0, "unit": pl.get("unit"),
                  "hsn_or_sac": pl.get("hsn_or_sac"), "tax_id": pl.get("tax_id"),
                  "location_id": pl.get("location_id") or p2.get("location_id"), "item_order": order, "is_billable": False}
            q = recq.get(iid)
            if q:
                nl["receive_line_items"] = [{"receive_item_id": q.pop(0), "quantity": float(pl["quantity"])}]
            new_lines.append(nl); order += 1
    po_line_total = round(sum(float(call("GET", f"/purchaseorders/{pid}")[1]["purchaseorder"]["total"]) -
                             float(call("GET", f"/purchaseorders/{pid}")[1]["purchaseorder"].get("adjustment") or 0) for pid in po_ids), 2)
    round_off = round(paytarget - po_line_total, 2)
    ref = bill.get("reference_number") or ""
    PRESERVE = ["vendor_id", "date", "due_date", "notes", "terms", "exchange_rate", "is_inclusive_tax",
                "is_item_level_tax_calc", "payment_terms", "payment_terms_label", "gst_treatment", "gst_no",
                "source_of_supply", "destination_of_supply", "template_id", "billing_address_id"]
    bu = {"bill_number": bill["bill_number"], "reference_number": ref, "line_items": new_lines,
          "adjustment": round_off, "adjustment_description": "Round Off", "discount_type": "item_level"}
    for k in PRESERVE:
        if bill.get(k) not in (None, ""):
            bu[k] = bill[k]
    if bill.get("custom_fields"):
        bu["custom_fields"] = [{"customfield_id": c["customfield_id"], "value": c.get("value")} for c in bill["custom_fields"]]
    inactive = list(set(inactive))
    for iid in inactive:
        call("POST", f"/items/{iid}/active")
    st, r = call("PUT", f"/bills/{bill_id}", bu)
    ok = st // 100 == 2 and r.get("code") in (0, None)
    # nudge to exact payment if a residual remains
    if ok:
        b2 = r.get("bill", {}); resid = round(paytarget - float(b2.get("total", paytarget)), 2)
        if 0.005 < abs(resid) <= 1.0:
            bu["adjustment"] = round(round_off + resid, 2)
            st, r = call("PUT", f"/bills/{bill_id}", bu); ok = st // 100 == 2 and r.get("code") in (0, None)
    for iid in inactive:
        call("POST", f"/items/{iid}/inactive")
    if not ok:
        return "FAIL", f"{st}: {r.get('message')}"
    b2 = r.get("bill", {})
    good = money_eq(b2.get("total", -1), paytarget, 0.02) and b2.get("purchaseorder_ids")
    return ("OK" if good else "CHECK"), f"total={b2.get('total')} pay={paytarget} bal={b2.get('balance')} status={b2.get('status')}"


for r in targets:
    c = cand.get(r["bill_number"])
    if not c:
        print("  ", r["bill_number"], "no candidate"); continue
    try:
        res, det = correct(r["bill_id"] if r.get("bill_id") else c["bill_id"], c["po_ids"].split(";"))
    except Exception as e:
        res, det = "FAIL", f"exception {e}"
    print(f"  {r['bill_number']} -> {res} {det}")
    if res == "OK":
        r["result"] = "OK"; r["reason"] = "multi-PO linked (balance corrected)"

w = csv.DictWriter(open(RESULTS, "w", newline="", encoding="utf-8-sig"), fieldnames=rows[0].keys())
w.writeheader(); w.writerows(rows)
from collections import Counter
print("results now:", dict(Counter(r["result"] for r in rows)))

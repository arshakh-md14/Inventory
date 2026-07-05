"""Multi-PO attach: one bill -> multiple POs. Attach the UNION of all POs' line items to the
bill (each line -> its PO line + that PO's GRN receipt), POs untouched (= DB), and put the
paid - Sum(PO) gap on the BILL's round-off (only if |gap| <= Rs.10). Draft POs are issued +
GRN'd first ONLY if the DB shows them delivered. All-or-nothing: if any PO in the set can't be
prepared, the whole bill is skipped. Reuses sync_po_then_attach + draft_issue_grn_attach infra."""
import importlib.util, csv, os, time
from collections import defaultdict

SP = r"C:\Users\JOGESH~1\AppData\Local\Temp\claude\c--Users-Jogesh-Behera-Code-file-MaterialDepotDjangoBackend\2215d20a-39d5-4160-89ab-18486807898c\scratchpad\sync_po_then_attach.py"
spec = importlib.util.spec_from_file_location("m", SP); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
DF = r"C:\Users\JOGESH~1\AppData\Local\Temp\claude\c--Users-Jogesh-Behera-Code-file-MaterialDepotDjangoBackend\2215d20a-39d5-4160-89ab-18486807898c\scratchpad\draft_issue_grn_attach.py"
spec2 = importlib.util.spec_from_file_location("df", DF); df = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(df)
call, money_eq, ITEM_STATUS, RateLimited = m.call, m.money_eq, m.ITEM_STATUS, m.RateLimited
call_inv, grn_endpoint = df.call_inv, df.grn_endpoint

INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
CAND = os.path.join(INV, "multipo_candidates.csv")
RESULTS = os.path.join(INV, "attach_results_multipo.csv")
DRY_RUN = False
REACTIVATE_INACTIVE = True
ROUNDOFF_MAX = 10.0
LIMIT = 500
SLEEP = 1.0
ONLY_BILL = None

DELIVERED = {}
for r in csv.DictReader(open(os.path.join(INV, "multipo_db_delivery.csv"), encoding="utf-8-sig")):
    DELIVERED[r["ref"]] = (r["delivered"] == "yes")
# inventory-typed items (only these can be received on a GRN; S&P/service lines are billed directly)
import glob as _glob
INVENTORY_ITEMS = set()
for _f in _glob.glob(os.path.join(INV, "Raw Files", "Item_*", "Item*.csv")):
    for _r in csv.DictReader(open(_f, encoding="utf-8")):
        if (_r.get("Item Type") or "").strip() == "Inventory" and _r.get("Item ID", "").strip():
            INVENTORY_ITEMS.add(_r["Item ID"].strip())


def po_net(p):
    return round(float(p["total"]) - float(p.get("adjustment") or 0), 2)


def flow(bill_id, refs, po_ids, bill_date_fallback=None):
    _, bd = call("GET", f"/bills/{bill_id}"); bill = bd.get("bill")
    if not bill:
        return "FAIL", "bill fetch", "", ""
    if bill.get("purchaseorder_ids"):
        return "SKIP", "bill already linked", bill.get("total"), ""
    paid = round(float(bill["total"]), 2)
    bvid = bill.get("vendor_id")
    pos = []
    for pid in po_ids:
        _, pd = call("GET", f"/purchaseorders/{pid}"); p = pd.get("purchaseorder")
        if not p:
            return "SKIP", f"PO fetch fail {pid}", paid, ""
        pos.append(p)
    # guards
    if any(p.get("vendor_id") != bvid for p in pos):
        return "SKIP", "vendor mismatch", paid, ""
    if any(p.get("billed_status") in ("billed", "partially_billed") for p in pos):
        return "SKIP", "a PO already billed", paid, ""
    if any(float(pl.get("quantity_billed") or 0) > 0 for p in pos for pl in p["line_items"]):
        return "SKIP", "a PO line partially billed", paid, ""
    # gap check (from current PO totals; GRN doesn't change totals)
    sumpo = round(sum(po_net(p) for p in pos), 2)
    gap = round(paid - sumpo, 2)
    if abs(gap) > ROUNDOFF_MAX:
        return "SKIP", f"gap {gap} > {ROUNDOFF_MAX} (paid {paid} vs sumPO {sumpo})", paid, sumpo
    # readiness: each PO must end open+GRN; draft/no-GRN allowed only if DB-delivered
    need_prep = []   # (po, action)
    for p, ref in zip(pos, refs):
        has_grn = bool(p.get("purchasereceives"))
        if p.get("status") == "open" and has_grn:
            continue
        if not DELIVERED.get(ref, False):
            return "SKIP", f"PO {p['purchaseorder_number']} not open+GRN and not DB-delivered", paid, sumpo
        need_prep.append(p)
    grn_date = bill.get("date") or bill_date_fallback

    if DRY_RUN:
        return ("DRY", f"{len(pos)} POs, sumPO {sumpo}, gap {gap} on bill round-off; prep(issue+GRN) {len(need_prep)} POs; "
                f"attach {sum(len(p['line_items']) for p in pos)} lines", paid, sumpo)

    inactive = [i for p in pos for i in {pl["item_id"] for pl in p["line_items"] if pl.get("item_id")}
                if str(ITEM_STATUS.get(i, "active")).lower() != "active"]
    inactive = list(set(inactive))
    for iid in inactive:
        call("POST", f"/items/{iid}/active")

    def deact():
        for iid in inactive:
            call("POST", f"/items/{iid}/inactive")

    # prep: issue draft + create GRN
    for p in need_prep:
        pid = p["id"] if "id" in p else p["purchaseorder_id"]
        pid = p.get("purchaseorder_id") or p.get("id")
        if p.get("status") == "draft":
            st, r = call("POST", f"/purchaseorders/{pid}/status/open")
            if not (st // 100 == 2 and r.get("code") in (0, None)):
                deact(); return "FAIL", f"issue {p['purchaseorder_number']} {st}: {r.get('message')}", paid, sumpo
        _, pd = call("GET", f"/purchaseorders/{pid}"); p2 = pd["purchaseorder"]
        if not p2.get("purchasereceives"):
            grn_lines = [{"line_item_id": pl["line_item_id"], "item_id": pl["item_id"],
                          "quantity": str(float(pl["quantity"])), "item_order": i}
                         for i, pl in enumerate(p2["line_items"], 1)
                         if pl.get("item_id") and pl["item_id"] in INVENTORY_ITEMS]
            if not grn_lines:
                deact(); return "SKIP", "no inventory line to GRN", paid, sumpo
            grn = {"purchaseorder_id": pid, "receive_number": p2["purchaseorder_number"],
                   "date": grn_date, "notes": "", "line_items": grn_lines}
            st, r = call_inv("POST", grn_endpoint(pid), grn)
            if not (st // 100 == 2 and r.get("code") in (0, None)):
                deact(); return "FAIL", f"GRN {p2['purchaseorder_number']} {st}: {r.get('message')}", paid, sumpo

    # re-GET all POs, build bill line_items = union, each linked to its PO line + GRN receive
    new_lines = []
    order = 1
    for pid in po_ids:
        _, pd = call("GET", f"/purchaseorders/{pid}"); p2 = pd["purchaseorder"]
        recq = defaultdict(list)
        for rec in p2.get("purchasereceives", []):
            if rec.get("billed_status") == "billed":
                continue
            _, rdd = call("GET", f"/purchasereceives/{rec['receive_id']}")
            for rli in rdd.get("purchasereceive", {}).get("line_items", []):
                recq[rli.get("item_id")].append(rli["line_item_id"])
        for pl in p2["line_items"]:
            nl = {"purchaseorder_item_id": pl["line_item_id"], "item_id": pl.get("item_id"),
                  "account_id": pl.get("account_id"), "name": pl.get("name"), "description": pl.get("description", ""),
                  "rate": float(pl["rate"]), "quantity": float(pl["quantity"]), "discount": 0,
                  "unit": pl.get("unit"), "hsn_or_sac": pl.get("hsn_or_sac"), "tax_id": pl.get("tax_id"),
                  "location_id": pl.get("location_id") or p2.get("location_id"), "item_order": order, "is_billable": False}
            q = recq.get(pl.get("item_id"))
            if q:
                nl["receive_line_items"] = [{"receive_item_id": q.pop(0), "quantity": float(pl["quantity"])}]
            new_lines.append(nl); order += 1

    po_line_total = round(sum(po_net((call("GET", f"/purchaseorders/{pid}")[1]["purchaseorder"])) for pid in po_ids), 2)
    round_off = round(paid - po_line_total, 2)
    ref_str = bill.get("reference_number") or ""
    PRESERVE = ["vendor_id", "date", "due_date", "notes", "terms", "exchange_rate", "is_inclusive_tax",
                "is_item_level_tax_calc", "payment_terms", "payment_terms_label", "gst_treatment", "gst_no",
                "source_of_supply", "destination_of_supply", "template_id", "billing_address_id"]
    # NOTE: do NOT send purchaseorder_ids — Zoho derives the PO links from each line's
    # purchaseorder_item_id; sending the top-level list conflicts ("PO IDs alone are not enough").
    bu = {"bill_number": bill["bill_number"], "reference_number": ref_str, "line_items": new_lines,
          "adjustment": round_off, "adjustment_description": "Round Off", "discount_type": "item_level"}
    if bill.get("location_id"):
        bu["location_id"] = bill["location_id"]
    for k in PRESERVE:
        if bill.get(k) not in (None, ""):
            bu[k] = bill[k]
    if bill.get("custom_fields"):
        bu["custom_fields"] = [{"customfield_id": c["customfield_id"], "value": c.get("value")} for c in bill["custom_fields"]]
    def put_bill(adj):
        bu["adjustment"] = round(adj, 2)
        st, r = call("PUT", f"/bills/{bill_id}", bu)
        return (st // 100 == 2 and r.get("code") in (0, None)), st, r

    okb, st, r = put_bill(round_off)
    if not okb and "payment or credits applied is more" in str(r.get("message", "")).lower():
        # tax-rounding across multi-PO lines left the total a paisa under the applied payment;
        # bump the round-off up until the bill total >= payment.
        for bump in (0.01, 0.03, 0.06, 0.1, 0.25, 0.5, 1.0, 2.0):
            okb, st, r = put_bill(round_off + bump)
            if okb:
                break
    elif okb:
        b2 = r.get("bill", {})
        rr = round(paid - float(b2.get("total", paid)), 2)
        if 0.005 < abs(rr) <= 1.0:
            okb, st, r = put_bill(round_off + rr)
    deact()
    if not okb:
        return "FAIL", f"bill PUT {st}: {r.get('message')}", paid, sumpo
    b2 = r.get("bill", {})
    linked = len(b2.get("purchaseorder_ids", []))
    ok2 = money_eq(b2.get("total", -1), paid) and linked >= len(po_ids)
    return ("OK" if ok2 else "CHECK",
            f"{len(po_ids)} POs linked, gap {gap} on round-off" if ok2
            else f"verify total={b2.get('total')} linkedPOs={linked}/{len(po_ids)}", b2.get("total", paid), sumpo)


def main():
    cands = list(csv.DictReader(open(CAND, encoding="utf-8-sig")))
    done = set()
    if os.path.exists(RESULTS):
        for r in csv.DictReader(open(RESULTS, encoding="utf-8-sig")):
            done.add(r["bill_id"])
    fh = None if DRY_RUN else open(RESULTS, "a", newline="", encoding="utf-8-sig")
    w = None if DRY_RUN else csv.DictWriter(fh, fieldnames=["bill_id", "bill_number", "n_pos", "result", "reason", "bill_total", "sum_po"])
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
        refs = c["md_refs"].split(";"); po_ids = c["po_ids"].split(";")
        try:
            res, reason, bt, sp = flow(c["bill_id"], refs, po_ids)
        except RateLimited as e:
            print(f"RATE LIMITED {e}"); n -= 1; break
        except Exception as e:
            res, reason, bt, sp = "FAIL", f"exception: {e}", "", ""
        counts[res] = counts.get(res, 0) + 1
        if w:
            w.writerow({"bill_id": c["bill_id"], "bill_number": c["bill_number"], "n_pos": c["n_pos"],
                        "result": res, "reason": reason, "bill_total": bt, "sum_po": sp}); fh.flush()
        print(f"[{n}] {c['bill_number']} ({c['n_pos']} POs) -> {res} {reason}")
        time.sleep(SLEEP if not DRY_RUN else 0)
    if fh:
        fh.close()
    print("\nSUMMARY:", counts, "| processed:", n, "| DRY_RUN:", DRY_RUN)


if __name__ == "__main__":
    main()

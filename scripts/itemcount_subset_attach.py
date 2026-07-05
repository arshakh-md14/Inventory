"""item-count 'PO has extra lines (bill subset)' + 'lines match by amount (false mismatch)':
match bill line_items to PO line_items by amount; attach the bill mirroring ONLY the matched
PO lines (subset). Extra PO lines are left unbilled (PO becomes partially_billed, legit).
Inventory lines -> GRN receipt (partial). Round-off on bill, cap Rs10. Reuses charge-attach infra."""
import importlib.util, csv, os, time

SP = r"C:\Users\JOGESH~1\AppData\Local\Temp\claude\c--Users-Jogesh-Behera-Code-file-MaterialDepotDjangoBackend\2215d20a-39d5-4160-89ab-18486807898c\scratchpad\sync_po_then_attach.py"
spec = importlib.util.spec_from_file_location("m", SP); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
DF = r"C:\Users\JOGESH~1\AppData\Local\Temp\claude\c--Users-Jogesh-Behera-Code-file-MaterialDepotDjangoBackend\2215d20a-39d5-4160-89ab-18486807898c\scratchpad\draft_issue_grn_attach.py"
spec2 = importlib.util.spec_from_file_location("df", DF); df = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(df)
call, money_eq, ITEM_STATUS, RateLimited = m.call, m.money_eq, m.ITEM_STATUS, m.RateLimited
call_inv, grn_endpoint = df.call_inv, df.grn_endpoint

INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
CAND = os.path.join(INV, "itemcount_subset_candidates.csv")
RESULTS = os.path.join(INV, "attach_results_itemcount_subset.csv")
DRY_RUN = False
LIMIT = 100
SLEEP = 1.0


def amt(li):
    return round(float(li.get("item_total") or 0), 2)


def match_po_lines(bill_lines, po_lines):
    """Greedy match each bill line to a PO line by item_total. Returns matched PO lines (subset)."""
    pool = list(po_lines); matched = []
    for x in bill_lines:
        hit = next((y for y in pool if amt(y) == amt(x)), None)
        if hit:
            pool.remove(hit); matched.append(hit)
    return matched


def flow(bill_id, po_id, bill_amount):
    _, bd = call("GET", f"/bills/{bill_id}"); bill = bd.get("bill")
    _, pd = call("GET", f"/purchaseorders/{po_id}"); po = pd.get("purchaseorder")
    if not bill or not po:
        return "FAIL", "fetch failed", "", ""
    bt, pt = float(bill["total"]), float(po["total"])
    if bill.get("purchaseorder_ids"):
        return "SKIP", "bill already linked", bt, pt
    if po.get("status") not in ("draft", "open"):
        return "SKIP", f"PO status {po.get('status')}", bt, pt
    if po.get("billed_status") == "billed":
        return "SKIP", "PO already billed", bt, pt
    if bill.get("vendor_id") != po.get("vendor_id"):
        return "SKIP", "vendor mismatch", bt, pt
    matched = match_po_lines(bill["line_items"], po["line_items"])
    if len(matched) != len(bill["line_items"]):
        return "SKIP", f"only {len(matched)}/{len(bill['line_items'])} bill lines match a PO line by amount", bt, pt
    paid = round(bt, 2)
    matched_ids = {ml["line_item_id"] for ml in matched}
    item_ids = [ml["item_id"] for ml in matched if ml.get("item_id")]
    inactive = [i for i in set(item_ids) if str(ITEM_STATUS.get(i, "active")).lower() != "active"]

    if DRY_RUN:
        return ("DRY", f"bill {len(bill['line_items'])} lines -> mirror {len(matched)}/{len(po['line_items'])} PO lines "
                f"(PO {po['purchaseorder_number']} status {po['status']}); paid {paid}; react {len(inactive)}", bt, pt)

    for iid in inactive:
        call("POST", f"/items/{iid}/active")

    def deact():
        for iid in inactive:
            call("POST", f"/items/{iid}/inactive")

    # 1) issue if draft
    if po.get("status") == "draft":
        st, r = call("POST", f"/purchaseorders/{po_id}/status/open")
        if not (st // 100 == 2 and r.get("code") in (0, None)):
            deact(); return "FAIL", f"issue {st}: {r.get('message')}", bt, pt

    # 2) GRN the matched inventory lines if not already received
    _, pd1 = call("GET", f"/purchaseorders/{po_id}"); po1 = pd1["purchaseorder"]
    already = {}
    for rec in po1.get("purchasereceives", []):
        _, rdd = call("GET", f"/purchasereceives/{rec['receive_id']}")
        for rli in rdd.get("purchasereceive", {}).get("line_items", []):
            already.setdefault(rli.get("item_id"), 0)
            already[rli.get("item_id")] += float(rli.get("quantity") or 0)
    need_grn = []
    for i, pl in enumerate(po1["line_items"], 1):
        if pl["line_item_id"] not in matched_ids or not pl.get("item_id"):
            continue
        rcv = already.get(pl["item_id"], 0)
        if rcv < float(pl["quantity"]):
            need_grn.append({"line_item_id": pl["line_item_id"], "item_id": pl["item_id"],
                             "quantity": str(float(pl["quantity"]) - rcv), "item_order": i})
    if need_grn:
        grn_date = max(bill.get("date") or "", po1.get("date") or "")  # receive must be >= PO date
        grn = {"purchaseorder_id": po_id, "receive_number": po1["purchaseorder_number"],
               "date": grn_date, "notes": "", "line_items": need_grn}
        st, r = call_inv("POST", grn_endpoint(po_id), grn)
        if not (st // 100 == 2 and r.get("code") in (0, None)):
            deact(); return "FAIL", f"GRN {st}: {r.get('message')}", bt, pt

    # 3) attach bill: mirror ONLY matched PO lines; link receives for inventory
    _, pd2 = call("GET", f"/purchaseorders/{po_id}"); po2 = pd2["purchaseorder"]
    recq = {}
    for rec in po2.get("purchasereceives", []):
        if rec.get("billed_status") == "billed":
            continue
        _, rdd = call("GET", f"/purchasereceives/{rec['receive_id']}")
        for rli in rdd.get("purchasereceive", {}).get("line_items", []):
            recq.setdefault(rli.get("item_id"), []).append(rli["line_item_id"])
    new_lines = []
    order = 1
    est_total = 0.0   # tax-inclusive estimate of the mirrored subset
    for pl in po2["line_items"]:
        if pl["line_item_id"] not in matched_ids:
            continue
        nl = {"purchaseorder_item_id": pl["line_item_id"], "item_id": pl.get("item_id"), "account_id": pl.get("account_id"),
              "name": pl.get("name"), "description": pl.get("description", ""), "rate": float(pl["rate"]), "quantity": float(pl["quantity"]),
              "discount": 0, "unit": pl.get("unit"), "hsn_or_sac": pl.get("hsn_or_sac"), "tax_id": pl.get("tax_id"),
              "location_id": pl.get("location_id") or po2.get("location_id"), "item_order": order, "is_billable": False}
        q = recq.get(pl.get("item_id"))
        if q:
            nl["receive_line_items"] = [{"receive_item_id": q.pop(0), "quantity": float(pl["quantity"])}]
        new_lines.append(nl); order += 1
        taxpct = float(pl.get("tax_percentage") or 0)
        est_total += round(float(pl["rate"]) * float(pl["quantity"]), 2) * (1 + taxpct / 100.0)
    # bridge to paid on the FIRST PUT so we never dip below payment_made (-> "payment exceeds")
    init_adj = round(paid - est_total, 2)
    if abs(init_adj) > 10:
        deact(); return "SKIP", f"subset est total {round(est_total,2)} vs paid {paid} off by {init_adj} > 10", bt, pt
    ref = bill.get("reference_number") or ""
    if po2["purchaseorder_number"] not in ref:
        ref = (ref + "," + po2["purchaseorder_number"]).strip(",")
    PRESERVE = ["vendor_id", "date", "due_date", "notes", "terms", "exchange_rate", "is_inclusive_tax",
                "is_item_level_tax_calc", "payment_terms", "payment_terms_label", "gst_treatment", "gst_no",
                "source_of_supply", "destination_of_supply", "template_id", "billing_address_id"]
    bu = {"bill_number": bill["bill_number"], "reference_number": ref, "line_items": new_lines,
          "location_id": po2.get("location_id") or bill.get("location_id"),
          "adjustment": init_adj, "adjustment_description": "Round Off", "discount_type": "item_level"}
    for k in PRESERVE:
        if bill.get(k) not in (None, ""):
            bu[k] = bill[k]
    if bill.get("custom_fields"):
        bu["custom_fields"] = [{"customfield_id": c["customfield_id"], "value": c.get("value")} for c in bill["custom_fields"]]
    st, r = call("PUT", f"/bills/{bill_id}", bu)
    okb = st // 100 == 2 and r.get("code") in (0, None)
    # correct round-off to hit paid exactly (cap Rs10)
    if okb:
        b2 = r.get("bill", {})
        rr = round(paid - float(b2.get("total", paid)), 2)
        if abs(rr) > 10:
            deact(); return "SKIP", f"post-attach gap {rr} > 10", bt, pt
        if abs(rr) > 0.005:
            bu["adjustment"] = round(init_adj + rr, 2)
            st, r = call("PUT", f"/bills/{bill_id}", bu); okb = st // 100 == 2 and r.get("code") in (0, None)
    deact()
    if not okb:
        return "FAIL", f"attach PUT {st}: {r.get('message')}", bt, pt
    b2 = r.get("bill", {})
    ok2 = money_eq(b2.get("total", -1), paid) and b2.get("purchaseorder_ids")
    return ("OK" if ok2 else "CHECK", f"subset attached ({len(new_lines)} lines); PO partially billed" if ok2
            else f"verify total={b2.get('total')} linked={bool(b2.get('purchaseorder_ids'))}", b2.get("total", paid), pt)


def main():
    cands = list(csv.DictReader(open(CAND, encoding="utf-8-sig")))
    done = set()
    if os.path.exists(RESULTS):
        for r in csv.DictReader(open(RESULTS, encoding="utf-8-sig")):
            done.add(r["bill_id"])
    fh = None if DRY_RUN else open(RESULTS, "a", newline="", encoding="utf-8-sig")
    w = None if DRY_RUN else csv.DictWriter(fh, fieldnames=["bill_id", "po_id", "bill_number", "po_number", "class", "result", "reason", "bill_total", "po_total"])
    if w and not done:
        w.writeheader()
    counts, n = {}, 0
    for c in cands:
        if c["bill_id"] in done or n >= LIMIT:
            continue
        n += 1
        try:
            res, reason, b2, p2 = flow(c["bill_id"], c["po_id"], c["bill_amount"])
        except RateLimited as e:
            print(f"RATE LIMITED {e}"); n -= 1; break
        except Exception as e:
            res, reason, b2, p2 = "FAIL", f"exception: {e}", "", ""
        counts[res] = counts.get(res, 0) + 1
        if w:
            w.writerow({"bill_id": c["bill_id"], "po_id": c["po_id"], "bill_number": c["bill_number"], "po_number": c["po_number"],
                        "class": c["class"], "result": res, "reason": reason, "bill_total": b2, "po_total": p2}); fh.flush()
        print(f"[{n}] {c['bill_number']}/{c['po_number']} [{c['class'][:20]}] -> {res} {reason}")
        time.sleep(SLEEP if not DRY_RUN else 0)
    if fh:
        fh.close()
    print("\nSUMMARY:", counts, "| processed:", n, "| DRY_RUN:", DRY_RUN)


if __name__ == "__main__":
    main()

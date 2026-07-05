"""item-count 'charge-explained' bills: the bill = PO products + a freight/transport CHARGE
line the PO lacks. Add the charge line to the PO (take the bill as truth), then attach:
product lines -> PO line + GRN receipt; charge line -> billed directly (no receipt).
Handles open+GRN (most) and draft (issue+GRN products) POs. Reuses sync_po_then_attach infra."""
import importlib.util, csv, os, time

SP = r"C:\Users\JOGESH~1\AppData\Local\Temp\claude\c--Users-Jogesh-Behera-Code-file-MaterialDepotDjangoBackend\2215d20a-39d5-4160-89ab-18486807898c\scratchpad\sync_po_then_attach.py"
spec = importlib.util.spec_from_file_location("m", SP); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
DF = r"C:\Users\JOGESH~1\AppData\Local\Temp\claude\c--Users-Jogesh-Behera-Code-file-MaterialDepotDjangoBackend\2215d20a-39d5-4160-89ab-18486807898c\scratchpad\draft_issue_grn_attach.py"
spec2 = importlib.util.spec_from_file_location("df", DF); df = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(df)
call, money_eq, ITEM_STATUS, RateLimited = m.call, m.money_eq, m.ITEM_STATUS, m.RateLimited
call_inv, grn_endpoint = df.call_inv, df.grn_endpoint

INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
CAND = os.path.join(INV, "charge_attach_candidates.csv")
RESULTS = os.path.join(INV, "attach_results_charge_itemcount.csv")
DRY_RUN = False
REACTIVATE_INACTIVE = True
LIMIT = 200
SLEEP = 1.0
ONLY_BILL = ""
SHIP_ID = "2432338000028755653"
CHARGE_KW = ("pack", "transport", "freight", "shipping", "loading", "unloading", "handling",
             "cartage", "courier", "forwarding", "insuranc", "octroi", "carriage", "charge")


def is_charge(li):
    if li.get("item_id") == SHIP_ID:
        return True
    text = " ".join([li.get("name") or "", li.get("description") or ""]).lower()
    return any(k in text for k in CHARGE_KW)


def amt(li):
    return round(float(li.get("item_total") or 0), 2)


def flow(bill_id, po_id, target):
    _, bd = call("GET", f"/bills/{bill_id}"); bill = bd.get("bill")
    _, pd = call("GET", f"/purchaseorders/{po_id}"); po = pd.get("purchaseorder")
    if not bill or not po:
        return "FAIL", "fetch failed", "", ""
    bt, pt = float(bill["total"]), float(po["total"])
    if bill.get("purchaseorder_ids"):
        return "SKIP", "bill already linked", bt, pt
    if po.get("status") not in ("draft", "open"):
        return "SKIP", f"PO status {po.get('status')}", bt, pt
    if po.get("billed_status") in ("billed", "partially_billed"):
        return "SKIP", f"PO {po.get('billed_status')}", bt, pt
    if bill.get("vendor_id") != po.get("vendor_id"):
        return "SKIP", "vendor mismatch", bt, pt
    b_charge = [x for x in bill["line_items"] if is_charge(x)]
    b_prod = [x for x in bill["line_items"] if not is_charge(x)]
    if not b_charge:
        return "SKIP", "no charge line found on bill", bt, pt
    pls = po["line_items"]
    # sanity: PO product lines should correspond to the bill's product lines (count-wise)
    if len(pls) != len(b_prod):
        return "SKIP", f"po lines {len(pls)} != bill product lines {len(b_prod)}", bt, pt
    paid = round(bt, 2)
    item_ids = [pl["item_id"] for pl in pls if pl.get("item_id")]
    inactive = [i for i in set(item_ids) if str(ITEM_STATUS.get(i, "active")).lower() != "active"]
    tax_for_charge = b_charge[0].get("tax_id") or pls[0].get("tax_id")

    if DRY_RUN:
        return ("DRY", f"add {len(b_charge)} charge line(s) totalling {sum(amt(x) for x in b_charge)} to PO "
                f"{po['purchaseorder_number']} (status {po['status']}); PO {pt}->~{paid}; attach bill {paid}; "
                f"react={len(inactive)}", bt, pt)

    for iid in inactive:
        call("POST", f"/items/{iid}/active")

    def deact():
        for iid in inactive:
            call("POST", f"/items/{iid}/inactive")

    # 1) add charge line(s) to the PO (echo existing product lines, append charges)
    def echo(pl, i):
        return {"line_item_id": pl["line_item_id"], "item_id": pl.get("item_id"), "account_id": pl.get("account_id"),
                "name": pl.get("name"), "description": pl.get("description", ""), "rate": float(pl["rate"]),
                "quantity": float(pl["quantity"]), "discount": pl.get("discount", 0), "unit": pl.get("unit"),
                "hsn_or_sac": pl.get("hsn_or_sac"), "tax_id": pl.get("tax_id"),
                "location_id": pl.get("location_id") or po.get("location_id"), "item_order": i}
    new_po = [echo(pl, i) for i, pl in enumerate(pls, 1)]
    for j, ch in enumerate(b_charge, len(pls) + 1):
        new_po.append({"name": (ch.get("description") or "Freight charges")[:100], "description": ch.get("description", ""),
                       "rate": amt(ch), "quantity": 1.0, "discount": 0, "tax_id": ch.get("tax_id") or tax_for_charge,
                       "hsn_or_sac": ch.get("hsn_or_sac"), "location_id": pls[0].get("location_id") or po.get("location_id"),
                       "item_order": j})

    def put_po(lines, adj):
        body = {"line_items": lines, "adjustment": round(adj, 2), "adjustment_description": "Round Off",
                "discount_type": "item_level", "is_inclusive_tax": bool(po.get("is_inclusive_tax"))}
        st, r = call("PUT", f"/purchaseorders/{po_id}", body)
        return (st // 100 == 2 and r.get("code") in (0, None)), st, r

    ok, st, r = put_po(new_po, 0)
    if not ok and "item" in str(r.get("message", "")).lower():   # charge line may need an item_id
        for nl in new_po[len(pls):]:
            nl["item_id"] = SHIP_ID
        ok, st, r = put_po(new_po, 0)
    if not ok:
        deact(); return "FAIL", f"PO add-charge {st}: {r.get('message')}", bt, pt
    t1 = float(r["purchaseorder"]["total"]); resid = round(paid - t1, 2)
    if abs(resid) > 10:
        deact(); return "SKIP", f"PO total {t1} vs bill {paid} off by {resid} (not reverted)", bt, t1
    if resid != 0:
        ok, st, r = put_po(new_po, resid)
    new_pt = float(r["purchaseorder"]["total"])

    # 2) issue if draft, GRN products if none
    _, pd1 = call("GET", f"/purchaseorders/{po_id}"); po1 = pd1["purchaseorder"]
    if po1.get("status") == "draft":
        st, r = call("POST", f"/purchaseorders/{po_id}/status/open")
        if not (st // 100 == 2 and r.get("code") in (0, None)):
            deact(); return "FAIL", f"issue {st}: {r.get('message')}", bt, new_pt
    if not po1.get("purchasereceives"):
        grn_lines = [{"line_item_id": pl["line_item_id"], "item_id": pl["item_id"], "quantity": str(float(pl["quantity"])), "item_order": i}
                     for i, pl in enumerate(po1["line_items"], 1) if pl.get("item_id") and pl["item_id"] != SHIP_ID and not is_charge(pl)]
        grn = {"purchaseorder_id": po_id, "receive_number": po1["purchaseorder_number"], "date": bill.get("date"), "notes": "", "line_items": grn_lines}
        st, r = call_inv("POST", grn_endpoint(po_id), grn)
        if not (st // 100 == 2 and r.get("code") in (0, None)):
            deact(); return "FAIL", f"GRN {st}: {r.get('message')}", bt, new_pt

    # 3) attach bill: mirror PO lines; product->receive, charge->direct
    _, pd2 = call("GET", f"/purchaseorders/{po_id}"); po2 = pd2["purchaseorder"]
    recq = {}
    for rec in po2.get("purchasereceives", []):
        if rec.get("billed_status") == "billed":
            continue
        _, rdd = call("GET", f"/purchasereceives/{rec['receive_id']}")
        for rli in rdd.get("purchasereceive", {}).get("line_items", []):
            recq.setdefault(rli.get("item_id"), []).append(rli["line_item_id"])
    new_lines = []
    for i, pl in enumerate(po2["line_items"], 1):
        nl = {"purchaseorder_item_id": pl["line_item_id"], "item_id": pl.get("item_id"), "account_id": pl.get("account_id"),
              "name": pl.get("name"), "description": pl.get("description", ""), "rate": float(pl["rate"]), "quantity": float(pl["quantity"]),
              "discount": 0, "unit": pl.get("unit"), "hsn_or_sac": pl.get("hsn_or_sac"), "tax_id": pl.get("tax_id"),
              "location_id": pl.get("location_id") or po2.get("location_id"), "item_order": i, "is_billable": False}
        if not is_charge(pl):
            q = recq.get(pl.get("item_id"))
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
    okb = st // 100 == 2 and r.get("code") in (0, None)
    if okb:
        b2 = r.get("bill", {})
        rr = round(paid - float(b2.get("total", paid)), 2)
        if 0.005 < abs(rr) <= 1.0:
            bu["adjustment"] = round(round_off + rr, 2)
            st, r = call("PUT", f"/bills/{bill_id}", bu); okb = st // 100 == 2 and r.get("code") in (0, None)
    deact()
    if not okb:
        return "FAIL", f"attach PUT {st}: {r.get('message')} (PO charged)", bt, new_pt
    b2 = r.get("bill", {})
    ok2 = money_eq(b2.get("total", -1), paid) and b2.get("purchaseorder_ids")
    return ("OK" if ok2 else "CHECK", f"charge added, attached; PO {pt}->{new_pt}" if ok2
            else f"verify total={b2.get('total')} linked={bool(b2.get('purchaseorder_ids'))}", b2.get("total", paid), new_pt)


def main():
    cands = list(csv.DictReader(open(CAND, encoding="utf-8-sig")))
    done = set()
    if os.path.exists(RESULTS):
        for r in csv.DictReader(open(RESULTS, encoding="utf-8-sig")):
            done.add(r["bill_id"])
    fh = None if DRY_RUN else open(RESULTS, "a", newline="", encoding="utf-8-sig")
    w = None if DRY_RUN else csv.DictWriter(fh, fieldnames=["bill_id", "po_id", "bill_number", "po_number", "bill_amount", "result", "reason", "bill_total", "po_total"])
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
            res, reason, b2, p2 = flow(c["bill_id"], c["po_id"], c["bill_amount"])
        except RateLimited as e:
            print(f"RATE LIMITED {e}"); n -= 1; break
        except Exception as e:
            res, reason, b2, p2 = "FAIL", f"exception: {e}", "", ""
        counts[res] = counts.get(res, 0) + 1
        if w:
            w.writerow({"bill_id": c["bill_id"], "po_id": c["po_id"], "bill_number": c["bill_number"], "po_number": c["po_number"],
                        "bill_amount": c["bill_amount"], "result": res, "reason": reason, "bill_total": b2, "po_total": p2}); fh.flush()
        print(f"[{n}] {c['bill_number']}/{c['po_number']} -> {res} {reason}")
        time.sleep(SLEEP if not DRY_RUN else 0)
    if fh:
        fh.close()
    print("\nSUMMARY:", counts, "| processed:", n, "| DRY_RUN:", DRY_RUN)


if __name__ == "__main__":
    main()

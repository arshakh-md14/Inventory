"""Vendor-mismatch same-GST fix: repoint the PO to the BILL's vendor (same GSTIN, different
Zoho record), activating the bill's vendor if it is inactive (logged), then attach.
Logs: po_vendor_fix_log.csv (every repoint) + vendor_activation_log.csv (every activation).
Amount rule: PO=DB, round-off <= Rs.10 on the bill. Draft POs issued+GRN'd (all refs delivered).
Reuses sync_po_then_attach + draft_issue_grn_attach infra + Vendors CSV for GSTIN/status."""
import importlib.util, csv, os, time
from collections import defaultdict

SP = r"C:\Users\JOGESH~1\AppData\Local\Temp\claude\c--Users-Jogesh-Behera-Code-file-MaterialDepotDjangoBackend\2215d20a-39d5-4160-89ab-18486807898c\scratchpad\sync_po_then_attach.py"
spec = importlib.util.spec_from_file_location("m", SP); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
DF = r"C:\Users\JOGESH~1\AppData\Local\Temp\claude\c--Users-Jogesh-Behera-Code-file-MaterialDepotDjangoBackend\2215d20a-39d5-4160-89ab-18486807898c\scratchpad\draft_issue_grn_attach.py"
spec2 = importlib.util.spec_from_file_location("df", DF); df = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(df)
call, money_eq, ITEM_STATUS, RateLimited = m.call, m.money_eq, m.ITEM_STATUS, m.RateLimited
call_inv, grn_endpoint = df.call_inv, df.grn_endpoint
INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
CAND = os.path.join(INV, "vendorfix2_candidates.csv")
RESULTS = os.path.join(INV, "attach_results_vendorfix.csv")
ACT_LOG = os.path.join(INV, "vendor_activation_log.csv")
FIX_LOG = os.path.join(INV, "po_vendor_fix_log.csv")
DRY_RUN = False
LIMIT = 200
SLEEP = 1.0
ONLY_BILL = None

VEND = {}
for r in csv.DictReader(open(os.path.join(INV, "Raw Files", "Vendors (1).csv"), encoding="utf-8-sig")):
    VEND[r["Contact ID"].strip()] = (r["GST Identification Number (GSTIN)"].strip().upper(), r["Status"].strip(), r["Contact Name"])
DELIV = {r["ref"]: (r["delivered"] == "yes") for r in csv.DictReader(open(os.path.join(INV, "vendorfix2_delivery.csv"), encoding="utf-8-sig"))} if os.path.exists(os.path.join(INV, "vendorfix2_delivery.csv")) else {}


def logrow(path, row, fields):
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow(row)


def flow(c):
    bill_id, po_id = c["bill_id"], c["po_id"]
    _, bd = call("GET", f"/bills/{bill_id}"); bill = bd.get("bill")
    _, pd = call("GET", f"/purchaseorders/{po_id}"); po = pd.get("purchaseorder")
    if not bill or not po:
        return "FAIL", "fetch failed", "", ""
    bt, pt = float(bill["total"]), float(po["total"])
    if bill.get("purchaseorder_ids"):
        return "SKIP", "bill already linked", bt, pt
    if po.get("billed_status") in ("billed", "partially_billed"):
        return "SKIP", f"PO {po.get('billed_status')}", bt, pt
    bvid, pvid = bill.get("vendor_id"), po.get("vendor_id")
    bg = VEND.get(bvid, ("", "", ""))[0]; pg = VEND.get(pvid, ("", "", ""))[0]
    if bvid != pvid and not (bg and bg == pg):
        return "SKIP", f"GSTIN differ (bill {bg} po {pg})", bt, pt
    paid = round(bt, 2)
    ref = c.get("md_ref", "")

    if DRY_RUN:
        act = "activate+" if (bvid != pvid and VEND.get(bvid, ("", "?"))[1] != "Active") else ""
        rp = f"repoint PO->{VEND.get(bvid,('','','?'))[2]}" if bvid != pvid else "vendor already same"
        return ("DRY", f"{act}{rp} (gst {bg}); attach bill {paid}", bt, pt)

    # 1) repoint PO to bill's vendor (activating bill vendor if inactive)
    if bvid != pvid:
        bvstatus = VEND.get(bvid, ("", "?", ""))[1]
        if bvstatus != "Active":
            call("POST", f"/contacts/{bvid}/active")
            logrow(ACT_LOG, {"vendor_id": bvid, "vendor_name": VEND.get(bvid, ("", "", ""))[2], "gstin": bg,
                             "bill_number": c["bill_number"], "po_number": c["po_number"]},
                   ["vendor_id", "vendor_name", "gstin", "bill_number", "po_number"])
        st, r = call("PUT", f"/purchaseorders/{po_id}", {"vendor_id": bvid})
        if not (st // 100 == 2 and r.get("code") in (0, None)):
            return "FAIL", f"repoint PUT {st}: {r.get('message')}", bt, pt
        logrow(FIX_LOG, {"bill_number": c["bill_number"], "po_number": c["po_number"], "gstin": bg,
                         "old_vendor_id": pvid, "old_vendor_name": VEND.get(pvid, ("", "", ""))[2],
                         "new_vendor_id": bvid, "new_vendor_name": VEND.get(bvid, ("", "", ""))[2]},
               ["bill_number", "po_number", "gstin", "old_vendor_id", "old_vendor_name", "new_vendor_id", "new_vendor_name"])

    # 2) ensure PO open + GRN (issue/GRN if draft-delivered)
    _, pd1 = call("GET", f"/purchaseorders/{po_id}"); po1 = pd1["purchaseorder"]
    item_ids = [pl["item_id"] for pl in po1["line_items"] if pl.get("item_id")]
    inactive = list({i for i in item_ids if str(ITEM_STATUS.get(i, "active")).lower() != "active"})
    for iid in inactive:
        call("POST", f"/items/{iid}/active")

    def deact():
        for iid in inactive:
            call("POST", f"/items/{iid}/inactive")

    if po1.get("status") == "draft":
        if not DELIV.get(ref, False):
            deact(); return "SKIP", "PO draft & not DB-delivered", bt, pt
        st, r = call("POST", f"/purchaseorders/{po_id}/status/open")
        if not (st // 100 == 2 and r.get("code") in (0, None)):
            deact(); return "FAIL", f"issue {st}: {r.get('message')}", bt, pt
    if not po1.get("purchasereceives"):
        gl = [{"line_item_id": pl["line_item_id"], "item_id": pl["item_id"], "quantity": str(float(pl["quantity"])), "item_order": i}
              for i, pl in enumerate(po1["line_items"], 1) if pl.get("item_id")]
        st, r = call_inv("POST", grn_endpoint(po_id), {"purchaseorder_id": po_id, "receive_number": po1["purchaseorder_number"],
                         "date": bill.get("date"), "notes": "", "line_items": gl})
        if not (st // 100 == 2 and r.get("code") in (0, None)):
            deact(); return "FAIL", f"GRN {st}: {r.get('message')}", bt, pt

    # 3) attach (mirror PO lines + receives; round-off on bill, <=10)
    _, pd2 = call("GET", f"/purchaseorders/{po_id}"); po2 = pd2["purchaseorder"]
    po_net = round(float(po2["total"]) - float(po2.get("adjustment") or 0), 2)
    gap = round(paid - po_net, 2)
    if abs(gap) > 10:
        deact(); return "SKIP", f"gap {gap} > 10 (post-repoint)", bt, po2["total"]
    recq = defaultdict(list)
    for rec in po2.get("purchasereceives", []):
        if rec.get("billed_status") == "billed":
            continue
        _, rdd = call("GET", f"/purchasereceives/{rec['receive_id']}")
        for rli in rdd.get("purchasereceive", {}).get("line_items", []):
            recq[rli.get("item_id")].append(rli["line_item_id"])
    new_lines = []
    for i, pl in enumerate(po2["line_items"], 1):
        nl = {"purchaseorder_item_id": pl["line_item_id"], "item_id": pl.get("item_id"), "account_id": pl.get("account_id"),
              "name": pl.get("name"), "description": "", "rate": float(pl["rate"]), "quantity": float(pl["quantity"]),
              "discount": 0, "unit": pl.get("unit"), "hsn_or_sac": pl.get("hsn_or_sac"), "tax_id": pl.get("tax_id"),
              "location_id": pl.get("location_id") or po2.get("location_id"), "item_order": i, "is_billable": False}
        q = recq.get(pl.get("item_id"))
        if q:
            nl["receive_line_items"] = [{"receive_item_id": q.pop(0), "quantity": float(pl["quantity"])}]
        new_lines.append(nl)
    round_off = round(paid - po_net, 2)
    ref_s = bill.get("reference_number") or ""
    if po2["purchaseorder_number"] not in ref_s:
        ref_s = (ref_s + "," + po2["purchaseorder_number"]).strip(",")
    PRESERVE = ["vendor_id", "date", "due_date", "notes", "terms", "exchange_rate", "is_inclusive_tax",
                "is_item_level_tax_calc", "payment_terms", "payment_terms_label", "gst_treatment", "gst_no",
                "source_of_supply", "destination_of_supply", "template_id", "billing_address_id"]
    bu = {"bill_number": bill["bill_number"], "reference_number": ref_s, "line_items": new_lines,
          "location_id": po2.get("location_id") or bill.get("location_id"),
          "adjustment": round_off, "adjustment_description": "Round Off", "discount_type": "item_level"}
    for k in PRESERVE:
        if bill.get(k) not in (None, ""):
            bu[k] = bill[k]
    # NOTE: bill vendor already == PO vendor now (same id); do not re-send anything that unlinks
    if bill.get("custom_fields"):
        bu["custom_fields"] = [{"customfield_id": x["customfield_id"], "value": x.get("value")} for x in bill["custom_fields"]]
    st, r = call("PUT", f"/bills/{bill_id}", bu)
    okb = st // 100 == 2 and r.get("code") in (0, None)
    if okb:
        b2 = r.get("bill", {}); rr = round(paid - float(b2.get("total", paid)), 2)
        if 0.005 < abs(rr) <= 1.0:
            bu["adjustment"] = round(round_off + rr, 2)
            st, r = call("PUT", f"/bills/{bill_id}", bu); okb = st // 100 == 2 and r.get("code") in (0, None)
    deact()
    if not okb:
        return "FAIL", f"attach PUT {st}: {r.get('message')} (PO repointed)", bt, po2["total"]
    b2 = r.get("bill", {})
    ok2 = money_eq(b2.get("total", -1), paid) and b2.get("purchaseorder_ids")
    return ("OK" if ok2 else "CHECK", "repointed + attached" if ok2 else f"verify total={b2.get('total')} linked={bool(b2.get('purchaseorder_ids'))}", b2.get("total", paid), po2["total"])


def main():
    cands = list(csv.DictReader(open(CAND, encoding="utf-8-sig")))
    done = set()
    if os.path.exists(RESULTS):
        for r in csv.DictReader(open(RESULTS, encoding="utf-8-sig")):
            done.add(r["bill_id"])
    fh = None if DRY_RUN else open(RESULTS, "a", newline="", encoding="utf-8-sig")
    w = None if DRY_RUN else csv.DictWriter(fh, fieldnames=["bill_id", "po_id", "bill_number", "po_number", "result", "reason", "bill_total", "po_total"])
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
            res, reason, b2, p2 = flow(c)
        except RateLimited as e:
            print(f"RATE LIMITED {e}"); n -= 1; break
        except Exception as e:
            res, reason, b2, p2 = "FAIL", f"exception: {e}", "", ""
        counts[res] = counts.get(res, 0) + 1
        if w:
            w.writerow({"bill_id": c["bill_id"], "po_id": c["po_id"], "bill_number": c["bill_number"], "po_number": c["po_number"],
                        "result": res, "reason": reason, "bill_total": b2, "po_total": p2}); fh.flush()
        print(f"[{n}] {c['bill_number']}/{c['po_number']} -> {res} {reason}")
        time.sleep(SLEEP if not DRY_RUN else 0)
    if fh:
        fh.close()
    print("\nSUMMARY:", counts, "| processed:", n, "| DRY_RUN:", DRY_RUN)


if __name__ == "__main__":
    main()

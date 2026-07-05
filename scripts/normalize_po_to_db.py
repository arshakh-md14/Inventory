"""Normalize the PO total to DB for single-line/qty-match bills already linked under
the earlier (PO=paid) approach. Their bill already carries the paid-vs-DB excess on its
round-off; only the PO sits at paid. Fix = set the PO's adjustment so PO total == DB
exactly (line untouched). Bill is not modified. Self-targeting: GETs each candidate PO
and only edits ones still > Rs.0.05 off DB. Run AFTER the main batch finishes."""
import importlib.util, csv, os, time

SP = r"C:\Users\JOGESH~1\AppData\Local\Temp\claude\c--Users-Jogesh-Behera-Code-file-MaterialDepotDjangoBackend\2215d20a-39d5-4160-89ab-18486807898c\scratchpad\sync_po_then_attach.py"
spec = importlib.util.spec_from_file_location("m", SP)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
CAND = os.path.join(INV, "amountdiff_single_qtymatch.csv")
RESULTS = os.path.join(INV, "attach_results_amountdiff.csv")
OUT = os.path.join(INV, "normalize_po_results.csv")
DRY_RUN = False

done_ok = {r["bill_id"] for r in csv.DictReader(open(RESULTS, encoding="utf-8-sig")) if r["result"] == "OK"}
cands = list(csv.DictReader(open(CAND, encoding="utf-8-sig")))
# only linked-OK bills where paid != DB can possibly have PO off DB
targets = [c for c in cands if c["bill_id"] in done_ok
           and abs(float(c["bill_amount"]) - float(c["db_amount"])) > 0.005]
print("candidates to check:", len(targets))

already = set()
if os.path.exists(OUT):
    already = {r["po_id"] for r in csv.DictReader(open(OUT, encoding="utf-8-sig"))}
fh = None if DRY_RUN else open(OUT, "a", newline="", encoding="utf-8-sig")
w = None if DRY_RUN else csv.DictWriter(fh, fieldnames=["bill_id", "po_id", "bill_number", "po_number",
                                        "db_amount", "result", "reason", "po_before", "po_after"])
if w and not already:
    w.writeheader()

counts = {}
for c in targets:
    if c["po_id"] in already:
        continue
    try:
        _, pd = m.call("GET", f"/purchaseorders/{c['po_id']}")
        po = pd.get("purchaseorder")
        if not po:
            res, reason, before, after = "FAIL", "po fetch", "", ""
        else:
            DB = round(float(c["db_amount"]), 2)
            before = float(po["total"])
            if abs(before - DB) <= 0.05:
                res, reason, after = "SKIP", "PO already == DB", before
            else:
                pl = po["line_items"][0]
                iid = pl["item_id"]
                react = str(m.ITEM_STATUS.get(iid, "active")).lower() != "active"
                new_adj = round(float(po.get("adjustment") or 0) + (DB - before), 2)
                line = m.po_line_body(pl, po)
                if DRY_RUN:
                    res, reason, after = "DRY", f"adj->{new_adj} so PO {before}->{DB}", before
                else:
                    if react:
                        m.call("POST", f"/items/{iid}/active")
                    ok, st, r = m.put_po(c["po_id"], line, new_adj, bool(po.get("is_inclusive_tax")))
                    if react:
                        m.call("POST", f"/items/{iid}/inactive")
                    if not ok:
                        res, reason, after = "FAIL", f"PUT {st}: {r.get('message')}", before
                    else:
                        after = float(r["purchaseorder"]["total"])
                        good = abs(after - DB) <= 0.05
                        res, reason = ("OK", "PO->DB") if good else ("CHECK", f"after {after} vs DB {DB}")
    except m.RateLimited as e:
        print("RATE LIMITED -- stop:", e); break
    except Exception as e:
        res, reason, before, after = "FAIL", f"exception: {e}", "", ""
    counts[res] = counts.get(res, 0) + 1
    if w:
        w.writerow({"bill_id": c["bill_id"], "po_id": c["po_id"], "bill_number": c["bill_number"],
                    "po_number": c["po_number"], "db_amount": c["db_amount"], "result": res,
                    "reason": reason, "po_before": before, "po_after": after}); fh.flush()
    if res != "SKIP":
        print(f"{c['bill_number']}/{c['po_number']} -> {res} {reason}")
    if not DRY_RUN:
        time.sleep(0.5)
if fh:
    fh.close()
print("\nSUMMARY:", counts)

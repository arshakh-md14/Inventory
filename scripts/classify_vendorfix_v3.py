"""Correct classification: GET each vendor-mismatch pair's bill+PO for the ACTUAL vendor_ids,
then look up GSTIN + status by ID in the Vendors CSV (sheet names are stale). Same-GST +
in-scope (single-PO inv paid) -> vendorfix2_candidates.csv (with vendor_ids + activation flag)."""
import importlib.util, csv, glob, os, re
from collections import Counter

spec = importlib.util.spec_from_file_location("m", r"C:\Users\JOGESH~1\AppData\Local\Temp\claude\c--Users-Jogesh-Behera-Code-file-MaterialDepotDjangoBackend\2215d20a-39d5-4160-89ab-18486807898c\scratchpad\sync_po_then_attach.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
call = m.call
csv.field_size_limit(10000000)
INV = r"C:\Users\Jogesh Behera\Code file\Inventory"
INVSET = {"Inventory (active)", "Inventory (inactive)"}
VEND = {}
for r in csv.DictReader(open(os.path.join(INV, "Raw Files", "Vendors (1).csv"), encoding="utf-8-sig")):
    VEND[r["Contact ID"].strip()] = (r["GST Identification Number (GSTIN)"].strip().upper(), r["Status"].strip(), r["Contact Name"])


def invonly(itstr):
    toks = set(re.sub(r' x\d+', '', t).strip() for t in itstr.split(";") if t.strip())
    return bool(toks & INVSET)  # EXPANDED: inventory present (alone or mixed)


sheet = {r["Bill Number"]: r for r in csv.DictReader(open(os.path.join(INV, "bill_po_reconciliation.csv"), encoding="utf-8-sig"))
         if r["Multi-PO Bill"] != "Yes"}
pairs = {}
for f in glob.glob(os.path.join(INV, "attach_results*.csv")):
    for r in csv.DictReader(open(f, encoding="utf-8-sig")):
        if "vendor mismatch" in (r.get("reason") or "").lower() and r.get("bill_id") and r.get("po_id"):
            pairs[(r["bill_id"], r["po_id"])] = (r.get("bill_number", ""), r.get("po_number", ""))
print("pairs:", len(pairs))

cls = Counter(); cand = []
for i, ((bid, pid), (bn, pn)) in enumerate(pairs.items(), 1):
    srow = sheet.get(bn)
    if not srow:
        cls["not single-PO"] += 1; continue
    try:
        _, bd = call("GET", f"/bills/{bid}"); b = bd.get("bill")
        _, pd = call("GET", f"/purchaseorders/{pid}"); p = pd.get("purchaseorder")
    except Exception:
        cls["fetch-fail"] += 1; continue
    if not b or not p:
        cls["fetch-fail"] += 1; continue
    if b.get("purchaseorder_ids"):
        cls["already linked"] += 1; continue
    bvid, pvid = b.get("vendor_id"), p.get("vendor_id")
    bg = VEND.get(bvid, ("", "?", ""))[0]; pg = VEND.get(pvid, ("", "?", ""))[0]
    bstat = VEND.get(bvid, ("", "?", ""))[1]
    same = bool(bg) and bg == pg
    scope = srow["Bill Status"] == "Paid" and invonly(srow["Item Type(s) (from PO)"])
    if bvid == pvid:
        tag = "same vendor already"
    elif same:
        tag = "same-GST"
    elif not (bg and pg):
        tag = "blank/unknown GST"
    else:
        tag = "different GST (genuine)"
    cls[tag + (" | in-scope" if scope else " | out")] += 1
    if same and scope and bvid != pvid:
        cand.append({"bill_id": bid, "po_id": pid, "bill_number": bn, "po_number": pn,
                     "md_ref": srow["MD Reference(s)"].strip(), "bill_amount": b.get("total"),
                     "bill_vendor_id": bvid, "po_vendor_id": pvid, "gstin": bg,
                     "bill_vendor_name": VEND.get(bvid, ("", "", ""))[2], "bill_vendor_status": bstat})
    if i % 60 == 0:
        print(f"  ...{i}")

with open(os.path.join(INV, "vendorfix2_candidates.csv"), "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=["bill_id", "po_id", "bill_number", "po_number", "md_ref", "bill_amount",
                                       "bill_vendor_id", "po_vendor_id", "gstin", "bill_vendor_name", "bill_vendor_status"])
    w.writeheader(); w.writerows(cand)
print("\nCLASSIFICATION (by vendor_id -> CSV GSTIN):")
for k, v in cls.most_common():
    print(f"  {v:4}  {k}")
print("\nsame-GST + in-scope + id-differ candidates:", len(cand))
print("  bill-vendor inactive (need activate):", sum(1 for c in cand if c["bill_vendor_status"] != "Active"))

"""READ-ONLY. For vendor-mismatch candidates: compare bill vs PO vendor GSTIN, PO state, gap,
and whether the bill's vendor is inactive. Classify fixable vs genuine. -> vendorfix_classify.csv"""
import importlib.util, csv, os
from collections import Counter

spec = importlib.util.spec_from_file_location("m", r"C:\Users\JOGESH~1\AppData\Local\Temp\claude\c--Users-Jogesh-Behera-Code-file-MaterialDepotDjangoBackend\2215d20a-39d5-4160-89ab-18486807898c\scratchpad\sync_po_then_attach.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
call = m.call
INV = r"C:\Users\Jogesh Behera\Code file\Inventory"


def gstin(obj):
    return (obj.get("gst_no") or "").strip().upper()


cands = list(csv.DictReader(open(os.path.join(INV, "vendorfix_candidates.csv"), encoding="utf-8-sig")))
cls = Counter(); rows = []
vendor_status = {}
for i, c in enumerate(cands, 1):
    try:
        _, bd = call("GET", f"/bills/{c['bill_id']}"); b = bd.get("bill")
        _, pd = call("GET", f"/purchaseorders/{c['po_id']}"); p = pd.get("purchaseorder")
    except Exception as e:
        cls["fetch-fail"] += 1; continue
    if not b or not p:
        cls["fetch-fail"] += 1; continue
    bg, pg = gstin(b), gstin(p)
    same_gst = bool(bg) and bg == pg
    paid = round(float(b["total"]), 2)
    po_net = round(float(p["total"]) - float(p.get("adjustment") or 0), 2)
    gap = round(paid - po_net, 2)
    bvid = b.get("vendor_id")
    # bill vendor active?
    if bvid not in vendor_status:
        try:
            _, vd = call("GET", f"/contacts/{bvid}")
            vendor_status[bvid] = vd.get("contact", {}).get("status", "?")
        except Exception:
            vendor_status[bvid] = "?"
    vst = vendor_status.get(bvid, "?")
    if not same_gst:
        c_cls = "genuine (GST differ/blank)"
    elif p.get("billed_status") in ("billed", "partially_billed"):
        c_cls = "same-GST but PO billed"
    elif p.get("status") == "draft":
        c_cls = "same-GST, PO draft (needs issue+GRN)"
    elif not p.get("purchasereceives"):
        c_cls = "same-GST, open no-GRN"
    elif abs(gap) > 10:
        c_cls = "same-GST but gap>10"
    else:
        c_cls = "same-GST CLEAN (open+GRN, gap<=10)"
    cls[c_cls] += 1
    rows.append({"bill_number": c["bill_number"], "po_number": c["po_number"], "bill_gstin": bg, "po_gstin": pg,
                 "same_gst": same_gst, "po_status": p.get("status"), "gap": gap,
                 "bill_vendor_status": vst, "class": c_cls,
                 "bill_vendor": b.get("vendor_name"), "po_vendor": p.get("vendor_name")})
    if i % 60 == 0:
        print(f"  ...{i}")

with open(os.path.join(INV, "vendorfix_classify.csv"), "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print("\nCLASSIFICATION:")
for k, v in cls.most_common():
    print(f"  {v:4}  {k}")
print("bill-vendor status among same-GST:",
      dict(Counter(r["bill_vendor_status"] for r in rows if r["same_gst"])))

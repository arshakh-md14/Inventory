"""
Restore reworked/attached POs to their DB (export) values: original rate + original
adjustment. PO is the source of truth; we undo any rate/adjustment we set during attach.
Bill stays linked and untouched. Item temporarily reactivated to edit the billed PO line.

ONLY_BILL set -> process just that bill's PO (test).  DRY_RUN prints intended change.
"""
import json, http.client, urllib.parse, csv, glob, re, os, time

ENV = r"C:\Users\Jogesh Behera\Code file\MaterialDepotDjangoBackend\.env.json"
RESULTS = r"C:\Users\Jogesh Behera\Code file\Inventory\attach_results.csv"
BASE = r"C:\Users\Jogesh Behera\Code file\Inventory\Raw Files"
DRY_RUN = False
ONLY_BILL = None
LIMIT = 10
norm = lambda s: re.sub(r'[^0-9A-Za-z]', '', s).upper()


def f(v):
    try:
        return round(float(v or 0), 2)
    except (ValueError, TypeError):
        return 0.0


d = json.load(open(ENV))
CID, CSEC, RT = d["zoho_client_id_haut_luxe"], d["zoho_client_secret_haut_luxe"], d["zoho_refresh_token_haut_luxe"]
ORG = str(d["zoho_org_id_haut_luxe"]); _tok = None


def token():
    global _tok
    if _tok: return _tok
    c = http.client.HTTPSConnection("accounts.zoho.in")
    p = urllib.parse.urlencode({"client_id": CID, "client_secret": CSEC, "grant_type": "refresh_token", "refresh_token": RT})
    c.request("POST", f"/oauth/v2/token?{p}"); _tok = json.loads(c.getresponse().read())["access_token"]; return _tok


def call(m, path, body=None):
    c = http.client.HTTPSConnection("www.zohoapis.in"); h = {"Authorization": f"Zoho-oauthtoken {token()}"}
    if body is not None:
        h["content-type"] = "application/json"; c.request(m, f"/books/v3{path}?organization_id={ORG}", json.dumps(body), h)
    else: c.request(m, f"/books/v3{path}?organization_id={ORG}", headers=h)
    r = c.getresponse(); return r.status, json.loads(r.read().decode())


# PO export originals: po_number -> rate, adj, total
po_orig = {}
for fn in glob.glob(os.path.join(BASE, "Purchase Order_*", "Purchase_Order*.csv")):
    for row in csv.DictReader(open(fn, encoding="utf-8")):
        pn = row["Purchase Order Number"]
        if pn and pn not in po_orig:
            po_orig[pn] = {"rate": f(row["Item Price"]), "adj": f(row["Adjustment"]), "total": f(row["Total"])}

ok = [r for r in csv.DictReader(open(RESULTS, encoding="utf-8-sig")) if r["result"] == "OK"]
done = 0
for r in ok:
    if ONLY_BILL and r["bill_id"] != ONLY_BILL:
        continue
    if done >= LIMIT:
        break
    pid, pn = r["po_id"], r["po_number"]
    o = po_orig.get(pn)
    if not o:
        print(f"  {r['bill_number']}/{pn}: no export original -> skip"); continue
    _, pd = call("GET", f"/purchaseorders/{pid}"); po = pd["purchaseorder"]; pl = po["line_items"][0]
    item_id = pl["item_id"]
    cur = float(po["total"])
    if abs(cur - o["total"]) < 0.005 and f(po.get("adjustment")) == o["adj"]:
        print(f"  {r['bill_number']}/{pn}: already at DB value ({cur}); skip"); continue
    done += 1
    if DRY_RUN:
        print(f"{r['bill_number']}/{pn}: PO total {cur} (adj {po.get('adjustment')}) -> DB rate {o['rate']} adj {o['adj']} total {o['total']}")
        continue
    _, itd = call("GET", f"/items/{item_id}"); st_item = itd.get("item", {}).get("status")
    if st_item != "active":
        call("POST", f"/items/{item_id}/active")
    upd = {"line_items": [{"line_item_id": pl["line_item_id"], "item_id": item_id,
                           "rate": o["rate"], "quantity": float(pl["quantity"]), "tax_id": pl.get("tax_id")}],
           "adjustment": o["adj"], "adjustment_description": po.get("adjustment_description") or "Round Off"}
    st, rr = call("PUT", f"/purchaseorders/{pid}", upd)
    if st_item != "active":
        call("POST", f"/items/{item_id}/inactive")
    _, pd2 = call("GET", f"/purchaseorders/{pid}"); p2 = pd2["purchaseorder"]
    _, bd = call("GET", f"/bills/{r['bill_id']}"); b2 = bd["bill"]
    ok2 = abs(float(p2["total"]) - o["total"]) < 0.05 and b2.get("purchaseorder_ids")
    print(f"{r['bill_number']}/{pn}: PUT {st} -> PO total {p2['total']} (DB {o['total']}) adj {p2.get('adjustment')} "
          f"| bill {b2['total']} linked={bool(b2.get('purchaseorder_ids'))} billed={p2.get('billed_status')} -> {'OK' if ok2 else 'CHECK'}")
    time.sleep(0.4)

print("\ndone:", done, "| DRY_RUN:", DRY_RUN, "| ONLY_BILL:", ONLY_BILL)

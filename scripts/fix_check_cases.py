"""Align PO line tax to the bill's tax for the committed CHECK (tax-mismatch) cases,
so PO total == bill total. Updates attach_results.csv (CHECK -> OK)."""
import json, http.client, urllib.parse, csv, os

ENV = r"C:\Users\Jogesh Behera\Code file\MaterialDepotDjangoBackend\.env.json"
RESULTS = r"C:\Users\Jogesh Behera\Code file\Inventory\attach_results.csv"
d = json.load(open(ENV))
CID, CSEC, RT = d["zoho_client_id_haut_luxe"], d["zoho_client_secret_haut_luxe"], d["zoho_refresh_token_haut_luxe"]
ORG = str(d["zoho_org_id_haut_luxe"])
_tok = None


def token():
    global _tok
    if _tok:
        return _tok
    c = http.client.HTTPSConnection("accounts.zoho.in")
    p = urllib.parse.urlencode({"client_id": CID, "client_secret": CSEC, "grant_type": "refresh_token", "refresh_token": RT})
    c.request("POST", f"/oauth/v2/token?{p}")
    _tok = json.loads(c.getresponse().read())["access_token"]
    return _tok


def call(method, path, body=None):
    c = http.client.HTTPSConnection("www.zohoapis.in")
    sep = "&" if "?" in path else "?"
    h = {"Authorization": f"Zoho-oauthtoken {token()}"}
    if body is not None:
        h["content-type"] = "application/json"
        c.request(method, f"/books/v3{path}{sep}organization_id={ORG}", json.dumps(body), h)
    else:
        c.request(method, f"/books/v3{path}{sep}organization_id={ORG}", headers=h)
    r = c.getresponse()
    return r.status, json.loads(r.read().decode())


rows = list(csv.DictReader(open(RESULTS, encoding="utf-8-sig")))
fixed = {}
for r in rows:
    if r["result"] != "CHECK":
        continue
    bid, pid = r["bill_id"], r["po_id"]
    _, bd = call("GET", f"/bills/{bid}")
    b = bd["bill"]; bl = b["line_items"][0]
    _, pd = call("GET", f"/purchaseorders/{pid}")
    p = pd["purchaseorder"]; pl = p["line_items"][0]
    qty = float(bl["quantity"])
    rate = round(float(b["sub_total"]) / qty, 6)
    upd = {"line_items": [{"line_item_id": pl["line_item_id"], "item_id": pl["item_id"],
                           "rate": rate, "quantity": qty, "tax_id": bl["tax_id"]}],
           "adjustment": float(b.get("adjustment") or 0),
           "adjustment_description": b.get("adjustment_description") or "Round Off"}
    st, resp = call("PUT", f"/purchaseorders/{pid}", upd)
    _, pd2 = call("GET", f"/purchaseorders/{pid}")
    p2 = pd2["purchaseorder"]
    ok = abs(float(p2["total"]) - float(b["total"])) < 0.005
    print(f"{r['bill_number']}/{r['po_number']}: PUT {st} -> PO total {p2['total']} vs bill {b['total']} -> {'OK' if ok else 'STILL OFF'}")
    if ok:
        fixed[bid] = float(p2["total"])

# rewrite results: CHECK -> OK for fixed
for r in rows:
    if r["result"] == "CHECK" and r["bill_id"] in fixed:
        r["result"] = "OK"
        r["reason"] = "tax aligned to bill"
        r["po_total"] = fixed[r["bill_id"]]
with open(RESULTS, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=rows[0].keys())
    w.writeheader(); w.writerows(rows)
print("\nfixed:", len(fixed))

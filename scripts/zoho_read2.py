import json, http.client, urllib.parse, os

ENV = r"C:\Users\Jogesh Behera\Code file\MaterialDepotDjangoBackend\.env.json"
OUTDIR = os.path.dirname(__file__)
BILL_ID = "2432338000006302002"
PO_ID = "2432338000006216041"

d = json.load(open(ENV))
CID, CSEC, RT = d["zoho_client_id_haut_luxe"], d["zoho_client_secret_haut_luxe"], d["zoho_refresh_token_haut_luxe"]
ORG = str(d["zoho_org_id_haut_luxe"])


def token():
    c = http.client.HTTPSConnection("accounts.zoho.in")
    p = urllib.parse.urlencode({"client_id": CID, "client_secret": CSEC,
                                "grant_type": "refresh_token", "refresh_token": RT})
    c.request("POST", f"/oauth/v2/token?{p}")
    return json.loads(c.getresponse().read()).get("access_token")


def get(tok, path):
    c = http.client.HTTPSConnection("www.zohoapis.in")
    sep = "&" if "?" in path else "?"
    c.request("GET", f"/books/v3{path}{sep}organization_id={ORG}",
              headers={"Authorization": f"Zoho-oauthtoken {tok}"})
    r = c.getresponse(); return r.status, json.loads(r.read().decode())


tok = token()

st, bd = get(tok, f"/bills/{BILL_ID}")
b = bd.get("bill", {})
json.dump(bd, open(os.path.join(OUTDIR, "bill_3267.json"), "w"), indent=2)
print(f"BILL 3267: status={b.get('status')} total={b.get('total')} sub_total={b.get('sub_total')}")
print(f"  purchaseorder_ids={b.get('purchaseorder_ids')} reference_number={b.get('reference_number')!r}")
for li in b.get("line_items", []):
    print(f"  LI item_id={li.get('item_id')} po_item_id={li.get('purchaseorder_item_id')} "
          f"qty={li.get('quantity')} rate={li.get('rate')} total={li.get('item_total')} "
          f"name={li.get('name')!r}")

st, pd = get(tok, f"/purchaseorders/{PO_ID}")
p = pd.get("purchaseorder", {})
json.dump(pd, open(os.path.join(OUTDIR, "po_5744.json"), "w"), indent=2)
print(f"\nPO-05744: status={p.get('status')} billed_status={p.get('billed_status')} "
      f"received_status={p.get('received_status')} total={p.get('total')}")
for li in p.get("line_items", []):
    print(f"  LI line_item_id={li.get('line_item_id')} item_id={li.get('item_id')} "
          f"qty={li.get('quantity')} q_received={li.get('quantity_received')} "
          f"q_billed={li.get('quantity_billed')} rate={li.get('rate')} total={li.get('item_total')}")

# purchase receives for this PO
st, prd = get(tok, f"/purchasereceives?purchaseorder_id={PO_ID}")
print(f"\npurchasereceives -> HTTP {st}")
print("  ", json.dumps(prd.get("purchasereceives", prd))[:500])

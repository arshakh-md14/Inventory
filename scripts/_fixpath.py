p = r"C:\Users\JOGESH~1\AppData\Local\Temp\claude\c--Users-Jogesh-Behera-Code-file-MaterialDepotDjangoBackend\2215d20a-39d5-4160-89ab-18486807898c\scratchpad\sync_po_then_attach.py"
lines = open(p, encoding="utf-8").read().split("\n")
for i, ln in enumerate(lines):
    if ln.startswith("CAND ="):
        lines[i] = 'CAND = r"C:\\Users\\Jogesh Behera\\Code file\\Inventory\\takebill_vendorfixed_single.csv"'
    if ln.startswith("RESULTS ="):
        lines[i] = 'RESULTS = r"C:\\Users\\Jogesh Behera\\Code file\\Inventory\\attach_results_vendorfixed_tb_single.csv"'
open(p, "w", encoding="utf-8").write("\n".join(lines))
print("fixed")
print([l for l in lines if l.startswith("CAND =") or l.startswith("RESULTS =")])

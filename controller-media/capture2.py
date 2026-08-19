import hid, time, sys

VID, PID = 1406, 8201
DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 15

d = hid.device()
d.open(VID, PID)
d.set_nonblocking(True)

print(f"capturing for {DURATION}s, mash ALL buttons/sticks now...")
baseline = None
seen_diffs = {}
start = time.time()
while time.time() - start < DURATION:
    data = d.read(64)
    if data:
        b = bytes(data)
        if baseline is None:
            baseline = b
        for i, (x, y) in enumerate(zip(b, baseline)):
            if x != y:
                seen_diffs.setdefault(i, set()).add(x)
    time.sleep(0.002)
d.close()
print("baseline:", baseline.hex())
print("changed byte indices and observed values:")
for i in sorted(seen_diffs):
    vals = sorted(seen_diffs[i])
    print(f"  byte[{i}] baseline={baseline[i]:02x} seen={[hex(v) for v in vals]}")

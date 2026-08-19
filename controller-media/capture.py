import hid, time, sys

VID, PID = 1406, 8201
DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 20

d = hid.device()
d.open(VID, PID)
d.set_nonblocking(True)

print(f"capturing for {DURATION}s...")
last = None
start = time.time()
while time.time() - start < DURATION:
    data = d.read(64)
    if data:
        b = bytes(data)
        if b != last:
            print(f"{time.time()-start:5.2f}  {b.hex()}")
            last = b
    time.sleep(0.005)
d.close()
print("done")

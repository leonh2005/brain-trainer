import time
import GameController
from Foundation import NSNotificationCenter, NSRunLoop, NSDate

nc = NSNotificationCenter.defaultCenter()
nc.addObserverForName_object_queue_usingBlock_(
    GameController.GCControllerDidConnectNotification, None, None, lambda n: None
)

controller = None
while controller is None:
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.2))
    cs = GameController.GCController.controllers()
    if cs:
        controller = cs[0]

gp = controller.extendedGamepad()
print("bound, polling all elements...", flush=True)

all_buttons = ["buttonA", "buttonB", "buttonX", "buttonY", "leftShoulder", "rightShoulder",
               "leftTrigger", "rightTrigger", "buttonMenu", "buttonOptions", "buttonHome",
               "leftThumbstickButton", "rightThumbstickButton"]
dpad_dirs = ["up", "down", "left", "right"]
dpad = gp.dpad()

prev = {}
end = time.time() + 25
while time.time() < end:
    for name in all_buttons:
        try:
            b = getattr(gp, name)()
        except AttributeError:
            continue
        if b is None:
            continue
        p = b.isPressed()
        if p != prev.get(name, False):
            print(f"{name}: {p}", flush=True)
        prev[name] = p
    for d in dpad_dirs:
        b = getattr(dpad, d)()
        p = b.isPressed()
        k = f"dpad_{d}"
        if p != prev.get(k, False):
            print(f"{k}: {p}", flush=True)
        prev[k] = p
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.02))
print("done", flush=True)

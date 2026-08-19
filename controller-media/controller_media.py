import time
import GameController
import Quartz
from Foundation import NSNotificationCenter, NSRunLoop, NSDate

KEY = {
    "space": 49, "left": 123, "right": 124, "up": 126, "down": 125,
    "m": 46, "f": 3, "j": 38, "l": 37,
}

# A=play/pause  B=mute  X=fullscreen
# Dpad left/right=seek -5s/+5s  Dpad up/down=volume up/down
# L/R shoulder=skip -10s/+10s
WATCH = {
    "buttonA": "space",
    "buttonB": "m",
    "buttonX": "f",
}
DPAD = {"up": "up", "down": "down", "left": "left", "right": "right"}
SHOULDER = {"leftShoulder": "j", "rightShoulder": "l"}


def tap_key(name):
    code = KEY[name]
    down = Quartz.CGEventCreateKeyboardEvent(None, code, True)
    up = Quartz.CGEventCreateKeyboardEvent(None, code, False)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
    print(f"-> {name}", flush=True)


GameController.GCController.setShouldMonitorBackgroundEvents_(True)

nc = NSNotificationCenter.defaultCenter()
nc.addObserverForName_object_queue_usingBlock_(
    GameController.GCControllerDidConnectNotification, None, None, lambda n: None
)

print("waiting for controller...", flush=True)
controller = None
while controller is None:
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.2))
    cs = GameController.GCController.controllers()
    if cs:
        controller = cs[0]

gp = controller.extendedGamepad()
print(f"bound: {controller.vendorName()}, polling...", flush=True)

prev = {}
dpad = gp.dpad()
while True:
    for btn_name, key_name in WATCH.items():
        pressed = getattr(gp, btn_name)().isPressed()
        if pressed and not prev.get(btn_name):
            tap_key(key_name)
        prev[btn_name] = pressed
    for dir_name, key_name in DPAD.items():
        pressed = getattr(dpad, dir_name)().isPressed()
        k = f"dpad_{dir_name}"
        if pressed and not prev.get(k):
            tap_key(key_name)
        prev[k] = pressed
    for btn_name, key_name in SHOULDER.items():
        pressed = getattr(gp, btn_name)().isPressed()
        if pressed and not prev.get(btn_name):
            tap_key(key_name)
        prev[btn_name] = pressed
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.02))

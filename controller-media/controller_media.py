import json
import subprocess
import time
from pathlib import Path

import GameController
import Quartz
from Foundation import NSNotificationCenter, NSRunLoop, NSDate

from mapping import ACTIONS, BUTTONS, CONFIG_PATH, KEY, PRESETS, load_config, save_config

STATE_PATH = Path(__file__).parent / "state.json"
HEARTBEAT_SEC = 2
REPEAT_SEC = 0.4
CONFIG_CHECK_SEC = 1
AUTO_SWITCH_SEC = 2

DETECT_SCRIPT = '''
tell application "System Events"
    set frontApp to name of first application process whose frontmost is true
    if frontApp is "firefox" then
        tell process "firefox" to return name of front window
    end if
end tell
return ""
'''


def detect_profile():
    try:
        result = subprocess.run(
            ["osascript", "-e", DETECT_SCRIPT], capture_output=True, text=True, timeout=2
        )
        title = result.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return None
    if "YouTube" in title:
        return "youtube"
    if "抖音" in title or "Douyin" in title or "TikTok" in title:
        return "douyin"
    return None


current_profile = None


def write_state(last_action=None, connected=True, controller_name=""):
    data = {
        "connected": connected,
        "controller_name": controller_name,
        "profile": current_profile,
        "updated_at": time.time(),
    }
    if last_action is not None:
        data["last_action"] = last_action
        data["last_action_at"] = time.time()
    elif STATE_PATH.exists():
        try:
            prev_data = json.loads(STATE_PATH.read_text())
            data["last_action"] = prev_data.get("last_action")
            data["last_action_at"] = prev_data.get("last_action_at")
        except (json.JSONDecodeError, OSError):
            pass
    STATE_PATH.write_text(json.dumps(data))


def tap_key(key, label, shift=False, ctrl=False):
    code = KEY[key]
    down = Quartz.CGEventCreateKeyboardEvent(None, code, True)
    up = Quartz.CGEventCreateKeyboardEvent(None, code, False)
    flags = 0
    if shift:
        flags |= Quartz.kCGEventFlagMaskShift
    if ctrl:
        flags |= Quartz.kCGEventFlagMaskControl
    if flags:
        Quartz.CGEventSetFlags(down, flags)
        Quartz.CGEventSetFlags(up, flags)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
    print(f"-> {label}", flush=True)
    write_state(last_action=label)


def do_volume(delta, label):
    subprocess.run([
        "osascript", "-e",
        f"set volume output volume ((output volume of (get volume settings)) + ({delta}))"
    ])
    print(f"-> {label}", flush=True)
    write_state(last_action=label)


def run_action(action_id):
    action = ACTIONS.get(action_id)
    if not action or action_id == "none":
        return
    if "key" in action:
        tap_key(action["key"], action["label"], shift=action.get("shift", False), ctrl=action.get("ctrl", False))
    elif "volume_delta" in action:
        do_volume(action["volume_delta"], action["label"])


GameController.GCController.setShouldMonitorBackgroundEvents_(True)

nc = NSNotificationCenter.defaultCenter()
nc.addObserverForName_object_queue_usingBlock_(
    GameController.GCControllerDidConnectNotification, None, None, lambda n: None
)

def bind_controller():
    print("waiting for controller...", flush=True)
    c = None
    while c is None:
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.2))
        cs = GameController.GCController.controllers()
        if cs:
            c = cs[0]
    name = c.vendorName()
    print(f"bound: {name}, polling...", flush=True)
    write_state(connected=True, controller_name=name)
    return c, c.extendedGamepad(), name


controller, gp, controller_name = bind_controller()
dpad = gp.dpad()
REBIND_CHECK_SEC = 2
last_rebind_check = 0.0


def get_pressed(button_id):
    if button_id.startswith("dpad_"):
        return getattr(dpad, button_id.split("_", 1)[1])().isPressed()
    if button_id.startswith("leftThumbstick_"):
        direction = button_id.split("_", 1)[1]
        return getattr(gp.leftThumbstick(), direction)().isPressed()
    if button_id.startswith("rightThumbstick_"):
        direction = button_id.split("_", 1)[1]
        return getattr(gp.rightThumbstick(), direction)().isPressed()
    return getattr(gp, button_id)().isPressed()


config = load_config()
config_mtime = CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else 0
active_profile = None
prev = {}
last_fire = {}
last_heartbeat = 0.0
last_config_check = 0.0
last_auto_switch = 0.0

while True:
    now = time.time()

    if now - last_rebind_check >= REBIND_CHECK_SEC:
        last_rebind_check = now
        if controller not in GameController.GCController.controllers():
            controller, gp, controller_name = bind_controller()
            dpad = gp.dpad()
            prev = {}

    if now - last_auto_switch >= AUTO_SWITCH_SEC:
        last_auto_switch = now
        detected = detect_profile()
        if detected and detected != active_profile:
            save_config(dict(PRESETS[detected]))
            active_profile = detected
            current_profile = detected
            print(f"auto-switched profile -> {detected}", flush=True)

    if now - last_config_check >= CONFIG_CHECK_SEC:
        last_config_check = now
        mtime = CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else 0
        if mtime != config_mtime:
            config = load_config()
            config_mtime = mtime
            print("config reloaded", flush=True)

    for button_id, _ in BUTTONS:
        pressed = get_pressed(button_id)
        action_id = config.get(button_id, "none")
        repeatable = ACTIONS.get(action_id, {}).get("repeat", False)
        fire = pressed and (
            not prev.get(button_id)
            or (repeatable and now - last_fire.get(button_id, 0) >= REPEAT_SEC)
        )
        if fire:
            run_action(action_id)
            last_fire[button_id] = now
        prev[button_id] = pressed

    if now - last_heartbeat >= HEARTBEAT_SEC:
        write_state(connected=True, controller_name=controller_name)
        last_heartbeat = now

    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.02))

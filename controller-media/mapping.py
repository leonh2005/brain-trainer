import json
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"

KEY = {
    "space": 49, "left": 123, "right": 124, "up": 126, "down": 125,
    "m": 46, "c": 8, "f": 3, "j": 38, "l": 37, "n": 45, "p": 35,
    "x": 7, "z": 6, "h": 4,
}

ACTIONS = {
    "play_pause": {"label": "播放 / 暫停", "key": "space"},
    "captions": {"label": "字幕開關 (YouTube)", "key": "c"},
    "mute": {"label": "靜音開關 (YouTube)", "key": "m"},
    "fullscreen": {"label": "全螢幕切換 (YouTube)", "key": "f"},
    "prev_video": {"label": "上一部影片 (YouTube)", "key": "p", "shift": True},
    "next_video": {"label": "下一部影片 (YouTube)", "key": "n", "shift": True},
    "rewind": {"label": "倒轉 10 秒 (YouTube)", "key": "j", "repeat": True},
    "fastforward": {"label": "快轉 10 秒 (YouTube)", "key": "l", "repeat": True},
    "prev_chapter": {"label": "上一章節 (YouTube)", "key": "left", "ctrl": True},
    "next_chapter": {"label": "下一章節 (YouTube)", "key": "right", "ctrl": True},
    "seek_back": {"label": "倒退 5 秒", "key": "left", "repeat": True},
    "seek_forward": {"label": "快轉 5 秒", "key": "right", "repeat": True},
    "douyin_prev": {"label": "上一部影片 (抖音)", "key": "up"},
    "douyin_next": {"label": "下一部影片 (抖音)", "key": "down"},
    "douyin_like": {"label": "按讚 (抖音)", "key": "z"},
    "douyin_comment": {"label": "留言區 (抖音)", "key": "x"},
    "douyin_favorite": {"label": "收藏 (抖音)", "key": "c"},
    "douyin_fullscreen": {"label": "全螢幕 (抖音)", "key": "h"},
    "douyin_clear_screen": {"label": "清屏 (抖音)", "key": "j"},
    "volume_down": {"label": "系統音量 -", "volume_delta": -6, "repeat": True},
    "volume_up": {"label": "系統音量 +", "volume_delta": 6, "repeat": True},
    "none": {"label": "無動作"},
}

BUTTONS = [
    ("buttonMenu", "Start (+)"),
    ("buttonOptions", "Select (-)"),
    ("buttonA", "A"),
    ("buttonB", "B"),
    ("buttonX", "X"),
    ("buttonY", "Y"),
    ("leftShoulder", "L1"),
    ("rightShoulder", "R1"),
    ("leftTrigger", "L2"),
    ("rightTrigger", "R2"),
    ("leftThumbstickButton", "左搖桿按下"),
    ("rightThumbstickButton", "右搖桿按下"),
    ("leftThumbstick_up", "左搖桿 上"),
    ("leftThumbstick_down", "左搖桿 下"),
    ("leftThumbstick_left", "左搖桿 左"),
    ("leftThumbstick_right", "左搖桿 右"),
    ("rightThumbstick_up", "右搖桿 上"),
    ("rightThumbstick_down", "右搖桿 下"),
    ("rightThumbstick_left", "右搖桿 左"),
    ("rightThumbstick_right", "右搖桿 右"),
    ("dpad_up", "方向鍵 上"),
    ("dpad_down", "方向鍵 下"),
    ("dpad_left", "方向鍵 左"),
    ("dpad_right", "方向鍵 右"),
]

PRESETS = {
    "youtube": {
        "buttonMenu": "play_pause",
        "buttonOptions": "captions",
        "buttonA": "none",
        "buttonB": "none",
        "buttonX": "mute",
        "buttonY": "none",
        "leftShoulder": "volume_down",
        "rightShoulder": "volume_up",
        "leftTrigger": "rewind",
        "rightTrigger": "fastforward",
        "leftThumbstickButton": "none",
        "rightThumbstickButton": "fullscreen",
        "leftThumbstick_up": "none",
        "leftThumbstick_down": "none",
        "leftThumbstick_left": "none",
        "leftThumbstick_right": "none",
        "rightThumbstick_up": "none",
        "rightThumbstick_down": "none",
        "rightThumbstick_left": "none",
        "rightThumbstick_right": "none",
        "dpad_up": "none",
        "dpad_down": "none",
        "dpad_left": "prev_video",
        "dpad_right": "next_video",
    },
    "douyin": {
        "buttonMenu": "play_pause",
        "buttonOptions": "douyin_like",
        "buttonA": "none",
        "buttonB": "douyin_favorite",
        "buttonX": "douyin_comment",
        "buttonY": "douyin_clear_screen",
        "leftShoulder": "volume_down",
        "rightShoulder": "volume_up",
        "leftTrigger": "seek_back",
        "rightTrigger": "seek_forward",
        "leftThumbstickButton": "none",
        "rightThumbstickButton": "douyin_fullscreen",
        "leftThumbstick_up": "none",
        "leftThumbstick_down": "none",
        "leftThumbstick_left": "none",
        "leftThumbstick_right": "none",
        "rightThumbstick_up": "none",
        "rightThumbstick_down": "none",
        "rightThumbstick_left": "none",
        "rightThumbstick_right": "none",
        "dpad_up": "douyin_prev",
        "dpad_down": "douyin_next",
        "dpad_left": "none",
        "dpad_right": "none",
    },
}

DEFAULT_PROFILE = "youtube"


def _read_all():
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text())
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def load_config(profile=DEFAULT_PROFILE):
    merged = dict(PRESETS.get(profile, PRESETS[DEFAULT_PROFILE]))
    saved = _read_all().get(profile)
    if isinstance(saved, dict):
        merged.update(saved)
    return merged


def save_config(profile, config):
    data = _read_all()
    data[profile] = config
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(CONFIG_PATH)

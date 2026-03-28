## 角色與圖片定義

# ── 角色 ──
define mc      = Character("[player_name]",  color="#4FC3F7")
define haein   = Character("車海仁",          color="#F48FB1")
define system  = Character("【系統】",        color="#76FF03", what_color="#76FF03")
define npc     = Character("獵人",            color="#AAAAAA")

# ── 主角（背影佔位） ──
image mc_back = Solid("#2A2A35", xsize=250, ysize=520)

# ── 車海仁表情佔位（替換美術後刪除此區段） ──
# 顏色從淡到深代表不同情緒，便於辨識
image haein neutral     = Solid("#F8C8D0", xsize=280, ysize=560)
image haein curious     = Solid("#F5B0BE", xsize=280, ysize=560)
image haein smile       = Solid("#F09DB2", xsize=280, ysize=560)
image haein slight_smile= Solid("#F3B4C0", xsize=280, ysize=560)
image haein flustered   = Solid("#E8809A", xsize=280, ysize=560)
image haein serious     = Solid("#C8809A", xsize=280, ysize=560)
image haein surprised   = Solid("#F5BFCC", xsize=280, ysize=560)
image haein worried     = Solid("#C89AA8", xsize=280, ysize=560)
image haein soft        = Solid("#FFD8E0", xsize=280, ysize=560)

# ── 背景佔位 ──
image bg_black              = Solid("#000000")
image bg_white              = Solid("#FFFFFF")
image bg_hunter_hall        = Solid("#1A2040")   # 獵人協會大廳（深藍）
image bg_dungeon_entrance   = Solid("#151515")   # 地下城入口（深黑）
image bg_dungeon_corridor   = Solid("#1C1008")   # 地下城走廊（暗棕）
image bg_dungeon_deep       = Solid("#0C0808")   # 地下城深處（黑紅）
image bg_hidden_room        = Solid("#0A1E30")   # 隱藏空間（深藍綠）
image bg_apartment          = Solid("#1C1C2E")   # 安全屋（深紫）
image bg_apartment_night    = Solid("#0A0A18")   # 安全屋夜晚（極深紫）
image bg_apartment_night_after = Solid("#0D0D1F") # 安全屋黎明前
image bg_training_ground    = Solid("#181810")   # 訓練場（深褐）
image bg_cafe               = Solid("#1E1810")   # 咖啡廳（暗棕）
image bg_title              = Solid("#05050F")   # 標題畫面

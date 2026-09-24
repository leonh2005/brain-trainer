#!/usr/bin/env python3
"""單張概念驗證：把墨墨做成「像 3D 模型」貼在平面背景上。

流程：照片 → 去背(BiRefNet) → 深度(Depth Anything V2) → 表面法線
      → 重新打光 + 邊緣光 → 投影片陰影 → 平面背景合成

用法：
    ./venv/bin/python relight_single.py [來源檔] [輸出檔]
"""
import os
import sys
import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFilter

SRC_DEFAULT = os.path.join(os.path.dirname(__file__), "source/momo.jpg")
OUT_DEFAULT = os.path.join(os.path.dirname(__file__), "output/relight_single.png")

WORK = 1024                 # 工作解析度
DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"

# 打光參數
LIGHT_DIR = np.array([-0.50, -0.65, 0.57])   # 主光（左上前方）
FILL_DIR = np.array([0.70, 0.15, 0.70])      # 補光（右下）
RELIEF_STRENGTH = 30.0                        # 表面起伏強度（法線斜率放大）
SPEC_POWER = 42.0                             # 高光銳利度
SPEC_GAIN = 62.0                              # 高光強度
RIM_STRENGTH = 0.5                            # 邊緣光強度
SHADE_FLOOR = 0.74                            # 陰影下限，避免輪廓出現死黑


def segment(img: Image.Image) -> np.ndarray:
    """回傳前景 alpha（0..1），解析度同 img。"""
    from transformers import AutoModelForImageSegmentation
    from torchvision import transforms

    model = AutoModelForImageSegmentation.from_pretrained(
        "ZhengPeng7/BiRefNet_lite", trust_remote_code=True).to(DEVICE).eval()
    tf = transforms.Compose([
        transforms.Resize((1024, 1024)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ])
    x = tf(img).unsqueeze(0).to(DEVICE)
    with torch.no_grad():
        pred = model(x)[-1].sigmoid().cpu()[0, 0].numpy()
    alpha = Image.fromarray((pred * 255).astype(np.uint8)).resize(img.size, Image.LANCZOS)
    a = np.asarray(alpha).astype(np.float32) / 255.0
    # 收斂邊緣：拉高對比後羽化，避免去背殘影與鋸齒
    a = np.clip((a - 0.35) / 0.3, 0, 1)
    return a


def estimate_depth(img: Image.Image) -> np.ndarray:
    """回傳 0..1 深度圖，1 = 近。"""
    from transformers import pipeline
    pipe = pipeline("depth-estimation",
                    model="depth-anything/Depth-Anything-V2-Base-hf", device=DEVICE)
    d = np.asarray(pipe(img)["depth"]).astype(np.float32)
    return (d - d.min()) / (d.max() - d.min() + 1e-6)


def enhance_relief(depth: np.ndarray, amount: float = 2.0, sigma: float = 6.0) -> np.ndarray:
    """強化中高頻起伏，讓毛髮表面在打光後有立體顆粒感。"""
    from scipy.ndimage import gaussian_filter
    base = gaussian_filter(depth, sigma=sigma)
    detail = depth - base
    return np.clip(base + detail * amount, 0, 1)


def make_normals(depth: np.ndarray) -> np.ndarray:
    """由深度梯度算表面法線 (h, w, 3)。

    深度圖帶有量化階梯，直接微分再放大會在表面產生同心條紋，
    所以先平滑掉階梯再取梯度。
    """
    from scipy.ndimage import gaussian_filter
    d = gaussian_filter(depth, sigma=1.8)
    gy, gx = np.gradient(d)
    nx = -gx * RELIEF_STRENGTH
    ny = -gy * RELIEF_STRENGTH
    nz = np.ones_like(d)
    n = np.sqrt(nx * nx + ny * ny + nz * nz)
    return np.stack([nx / n, ny / n, nz / n], axis=-1)


def ambient_occlusion(depth: np.ndarray, strength: float = 2.2) -> np.ndarray:
    """用深度低頻估粗略環境遮蔽：比周圍遠的凹陷處變暗。"""
    from scipy.ndimage import gaussian_filter
    local = gaussian_filter(depth, sigma=WORK * 0.025)
    return np.clip(1.0 - (local - depth) * strength, 0.6, 1.0)


def shade(normals: np.ndarray, alpha: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """主光 + 補光 + Blinn-Phong 高光。回傳 (shading, specular)。"""
    def unit(v: np.ndarray) -> np.ndarray:
        return v / np.linalg.norm(v)

    light, fill = unit(LIGHT_DIR), unit(FILL_DIR)
    half = unit(light + np.array([0.0, 0.0, 1.0]))   # 視線固定朝 +z
    ndl = (normals * light).sum(axis=-1)
    ndf = (normals * fill).sum(axis=-1)
    diffuse = 0.32 + 0.78 * np.clip(ndl, 0, 1) + 0.22 * np.clip(ndf, 0, 1)
    spec = np.clip((normals * half).sum(axis=-1), 0, 1) ** SPEC_POWER

    fg = alpha > 0.5
    mean = diffuse[fg].mean() if fg.any() else 1.0
    shading = np.clip(diffuse / (mean + 1e-6), SHADE_FLOOR, 1.5)
    return shading, spec * fg


def rim_light(alpha: np.ndarray, shading: np.ndarray) -> np.ndarray:
    """邊緣光：讓輪廓從背景浮出來，是「3D 感」的關鍵之一。"""
    from scipy.ndimage import binary_erosion, gaussian_filter
    m = alpha > 0.5
    inner = binary_erosion(m, iterations=int(WORK * 0.012))
    edge = (m & ~inner).astype(np.float32)
    glow = gaussian_filter(edge, sigma=WORK * 0.008)
    warm = np.array([1.0, 0.96, 0.86])
    return shading[..., None] + (glow * RIM_STRENGTH)[..., None] * warm


def build_background(size: int) -> np.ndarray:
    """柔和放射漸層背景，中性的工作室感。"""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    cx, cy = size * 0.5, size * 0.42
    r = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / (size * 0.78)
    r = np.clip(r, 0, 1)
    inner = np.array([0.93, 0.91, 0.88])
    outer = np.array([0.46, 0.45, 0.49])
    bg = inner * (1 - r)[..., None] + outer * r[..., None]
    return np.clip(bg * 255, 0, 255)


def contact_sheet(panels: list[tuple[str, Image.Image]], out_path: str) -> None:
    cell, pad, label_h = 460, 14, 34
    cols = len(panels)
    sheet = Image.new("RGB", (cols * (cell + pad) + pad, cell + label_h + pad), (20, 20, 22))
    dr = ImageDraw.Draw(sheet)
    for i, (label, im) in enumerate(panels):
        x = pad + i * (cell + pad)
        sheet.paste(im.resize((cell, cell), Image.LANCZOS), (x, label_h))
        dr.text((x, 12), label, fill=(255, 214, 0))
    sheet.save(out_path)


def main() -> None:
    src = sys.argv[1] if len(sys.argv) > 1 else SRC_DEFAULT
    out = sys.argv[2] if len(sys.argv) > 2 else OUT_DEFAULT
    workdir = os.path.dirname(out)
    os.makedirs(workdir, exist_ok=True)

    im = Image.open(src).convert("RGB").resize((WORK, WORK), Image.LANCZOS)
    # 輕微柔化去掉照片顆粒感，往 CG 表面靠攏（去背/深度仍用原圖）
    rgb = np.asarray(im.filter(ImageFilter.GaussianBlur(0.9))).astype(np.float32)
    print(f"來源 {os.path.basename(src)} → {WORK}px  (device={DEVICE})")

    print("去背中…")
    alpha = segment(im)
    coverage = float((alpha > 0.5).mean())
    print(f"  前景占比 {coverage*100:.1f}%")

    print("深度估計中…")
    depth = enhance_relief(estimate_depth(im))

    print("法線與打光…")
    normals = make_normals(depth)
    shading, spec = shade(normals, alpha)
    shading = rim_light(alpha, shading) * ambient_occlusion(depth)[..., None]

    # 立體化：原圖只被「調變」不取代原有明暗，高光另外疊加
    shaded = np.clip(rgb * shading + spec[..., None] * SPEC_GAIN * np.array([1.0, 0.98, 0.93]),
                     0, 255)

    # 邊緣羽化，避免貼上時出現硬邊
    a = np.asarray(
        Image.fromarray((alpha * 255).astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(WORK * 0.003))
    ).astype(np.float32) / 255.0

    print("背景與陰影合成…")
    from scipy.ndimage import gaussian_filter, shift as ndshift
    bg = build_background(WORK)
    # 地板投影：alpha 往下位移並模糊，模擬立體物件站在平面上
    shadow = ndshift(gaussian_filter(a, sigma=WORK * 0.035),
                     (WORK * 0.05, 0), mode="constant") * 0.6
    bg = bg * (1 - shadow[..., None]) + np.array([18, 16, 20]) * shadow[..., None]

    comp = bg * (1 - a[..., None]) + shaded * a[..., None]
    result = Image.fromarray(np.clip(comp, 0, 255).astype(np.uint8))

    # 各階段存檔
    paths = {}
    for name, arr in [
        ("alpha", np.stack([alpha * 255] * 3, axis=-1)),
        ("depth", np.stack([depth * 255] * 3, axis=-1)),
        ("normals", (normals * 0.5 + 0.5) * 255),
        ("shading", np.clip(shading / 1.5, 0, 1) * 255),
        ("shaded", shaded * a[..., None] + 22 * (1 - a[..., None])),
    ]:
        p = os.path.join(workdir, f"step_{name}.png")
        Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).save(p)
        paths[name] = p
        print(f"  {name} → {p}")

    result.save(out)
    print(f"成品 → {out}")

    contact_sheet([
        ("source", im),
        ("1. matte", Image.open(paths["alpha"])),
        ("2. depth", Image.open(paths["depth"])),
        ("3. normals", Image.open(paths["normals"])),
        ("4. relit", Image.open(paths["shaded"])),
        ("5. composed", result),
    ], os.path.join(workdir, "contact_sheet.png"))


if __name__ == "__main__":
    main()

# TRIZ 解題助手改版：本機服務 + ComfyUI 漫畫插圖

## 背景

原本用 claude.ai Artifact 做的 TRIZ 解題助手（見 `2026-09-03-triz-ai-solver-design.md`），示意圖只能用內嵌 SVG 畫簡筆線稿，Steven 認為品質太差，希望達到「至少漫畫程度」的插圖品質。

研究結論：
- Artifact 內建的 `sample` capability 只能生成文字/JSON，不會畫圖
- Artifact 有 CSP 限制，無法呼叫外部文生圖 API，也連不到本機的 ComfyUI（`localhost:8188`）
- 本機的 Hermes（Ollama `qwen3-tw`）是純文字模型，不適用
- 本機已裝好的 ComfyUI 可用，但原本只有 `ltx-video-2b` 影片生成 checkpoint；經測試 ComfyUI 裡其實已經有 `sd_xl_base_1.0.safetensors`（SDXL base）可直接拿來生圖，不需要額外下載模型
- 實測：SDXL base + 漫畫風格 prompt（`comic book illustration, bold black ink outlines, flat cel shading`），640×640/20步，M4/16GB MPS，約 40-50 秒/張，品質達到清楚可辨識的漫畫插畫水準

因此決定放棄 Artifact 架構，改成跟其他 CCProject 工具一致的本機 Flask 服務。

## 架構

`~/CCProject/triz-solver/`，Flask，port **5980**，venv（python3.14）。

- `app.py`：主邏輯，矛盾矩陣查表 + Groq 生成建議 + 呼叫 ComfyUI 生圖
- `comfy_client.py`：封裝 ComfyUI HTTP API（送 workflow → 輪詢 → 取圖）
- `db.py`：SQLite（`triz.db`）存歷史紀錄
- `triz_data.json`：39 參數 / 40 原理 / 矛盾矩陣（沿用 Artifact 版本已整理好的資料，來源 MIT 授權的 `triz-engineering-solver`）
- `templates/index.html`：前端，AI 分析／手動查表／歷史紀錄三分頁，架構沿用 Artifact 版本的 UI 設計

## 資料流

1. 使用者描述問題 → `/api/analyze`（Groq `openai/gpt-oss-120b`）判斷矛盾參數
2. 使用者確認/修正參數 → `/api/suggest`：Groq 生成 2-4 個具體建議 + 每個建議的英文圖像生成 prompt，接著逐一呼叫 ComfyUI（`comfy_client.generate_image`）生成漫畫風插圖，存進 `static/images/`
3. 分析結果存進 SQLite，供「歷史紀錄」分頁瀏覽

## 已知限制與依賴

- **ComfyUI 不是常駐服務**：裝在外接硬碟 1TOWC，需要插著硬碟手動啟動（`/Volumes/1TOWC/ComfyUI/啟動ComfyUI.command` 或手動跑 `main.py`）。triz-solver 的 Flask 服務本身是 LaunchAgent 常駐，但呼叫 ComfyUI 前會先檢查 `comfy_client.is_available()`，ComfyUI 沒開時只顯示文字建議、插圖顯示明確錯誤訊息，不會讓整個請求失敗
- **生圖速度**：一個建議約 40-50 秒，4 個建議依序生成約 2.5-3 分鐘，前端有明確等待提示
- **Groq 模型變動**：原本沿用其他專案慣用的 `llama-3.3-70b-versatile` 已在 Groq 下架（模型清單查詢確認），改用 `openai/gpt-oss-120b`。**這個模型是推理模型，會消耗隱藏的 reasoning token**，`max_tokens` 需要抓夠大（本專案用 600-2000），否則會出現「AI 沒有回傳內容」的空字串情況——已知其他還在用 `llama-3.3-70b-versatile` 的服務（hola-quant、threads-daily）可能有一樣的下架問題，未來若這兩個服務出錯要優先懷疑這個

## 部署狀態（2026-09-03 完成）

- LaunchAgent `com.steven.triz-solver` 已建立並常駐
- 已加入 command-center TOOLS 卡片（連結 `/svc/5980/`）與 dashboard(5600) 監控清單
- command-center 的 `PROXY_PORTS` 白名單加入 5980（順手移除了已封存的 rabbit-care 殘留的 5200）
- 舊的 claude.ai Artifact（`https://claude.ai/code/artifact/61a12fe0-52f7-4718-beb8-466f32c6704e`）保留但不再是主要入口，command-center 卡片已指向新服務
- 已用 Playwright 驗證完整流程：AI 分析 → 生成建議＋插圖 → 歷史紀錄展開顯示

## 明確排除範圍

- 不做 MLX 加速（ComfyUI 現用 PyTorch/MPS），若未來速度成為痛點再評估
- 不追加下載漫畫特化 checkpoint（SDXL base 品質已達標）
- 不幫 ComfyUI 建 LaunchAgent（依賴外接硬碟，開機自動啟動會在硬碟未插著時持續報錯）

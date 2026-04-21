import os, time, subprocess, sys
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

PROFILE = os.path.expanduser('~/Library/Application Support/Firefox/Profiles/ro7nczf2.default-release')
URL = 'https://shopee.tw/%E7%BE%8E%E5%9C%8BRabbit-Hole-Hay%E5%85%94%E5%AD%90%E6%B4%9E%E7%89%A7%E8%8D%89%E4%B8%AD%E7%BA%96-%E8%BB%9F%E7%BA%96-%E6%9E%9C%E5%9C%92(%E8%A2%8B%E8%A3%9D285g%E3%80%81908g)-i.4430087.1385536293'
TARGET = '提摩西二切(軟纖)908g'
QTY = 2
TELEGRAM_TOKEN = '8666778924:AAFMAFKfsfx3opS2CfCBrDYMIx6vcJKACTk'
CHAT_ID = '7556217543'

def tg(msg):
    subprocess.run([
        'curl', '-s', '-X', 'POST',
        f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage',
        '-d', f'chat_id={CHAT_ID}',
        '--data-urlencode', f'text={msg}'
    ], capture_output=True)

opts = Options()
opts.add_argument('--headless')
opts.profile = PROFILE

driver = webdriver.Firefox(options=opts)
driver.set_window_size(1440, 900)
wait = WebDriverWait(driver, 15)

try:
    # 確認 session
    driver.get('https://shopee.tw')
    time.sleep(5)
    if 'stevenhung' not in driver.find_element(By.TAG_NAME, 'body').text:
        tg('⚠️ 蝦皮 session 已過期，庫存監控暫停。請重新登入 Firefox 蝦皮後繼續。')
        print('SESSION_EXPIRED')
        driver.quit()
        sys.exit(0)

    # 前往商品頁
    driver.get(URL)
    time.sleep(7)

    # 找規格按鈕，判斷庫存
    btn = driver.find_element(By.XPATH, f'//button[.//span[contains(text(),"{TARGET}")]]')
    cursor = driver.execute_script('return window.getComputedStyle(arguments[0]).cursor', btn)

    if cursor != 'pointer':
        print('SOLD_OUT')
        driver.quit()
        sys.exit(0)

    print(f'IN_STOCK: {TARGET}')

    # 選規格
    driver.execute_script('arguments[0].click()', btn)
    time.sleep(1.5)

    # 設數量
    try:
        qty_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input.suQW3X')))
    except Exception:
        # class 可能改過，用 type=number fallback
        qty_input = driver.find_element(By.XPATH, '//div[contains(@class,"product-qty")]//input[@type="number"]')
    qty_input.clear()
    for _ in range(5):
        qty_input.send_keys(Keys.BACKSPACE)
    qty_input.send_keys(str(QTY))
    time.sleep(1)

    # 加入購物車
    cart_btn = driver.find_element(By.XPATH, '//button[contains(@class,"btn-tinted") and contains(text(),"加入購物車")]')
    driver.execute_script('arguments[0].scrollIntoView()', cart_btn)
    driver.execute_script('arguments[0].click()', cart_btn)
    time.sleep(4)

    if 'verify' in driver.current_url or 'captcha' in driver.current_url:
        tg(f'🐰 軟纖提摩西有貨！\n⚠️ 加入購物車遇到驗證，請手動下單\n{URL}')
        print('CAPTCHA_BLOCK')
        driver.quit()
        sys.exit(0)

    # ── 前往購物車結帳 ─────────────────────────────
    driver.get('https://shopee.tw/cart')
    time.sleep(6)

    # 勾選 Rabbit Hole Hay 店家下的商品
    # 找包含 TARGET 文字附近的 checkbox
    try:
        item_checkbox = driver.find_element(
            By.XPATH,
            f'//div[contains(@class,"cart-item") or contains(@class,"CartItem")][.//*[contains(text(),"{TARGET[:6]}")]]//input[@type="checkbox"]'
        )
    except Exception:
        # fallback：勾選整個 Rabbit Hole Hay 店家
        item_checkbox = driver.find_element(
            By.XPATH,
            '//div[.//*[contains(text(),"Rabbit") or contains(text(),"rabbit")]]//input[@type="checkbox"]'
        )
    if not item_checkbox.is_selected():
        driver.execute_script('arguments[0].click()', item_checkbox)
    time.sleep(1.5)

    # 點結帳按鈕
    checkout_btn = driver.find_element(
        By.XPATH,
        '//button[contains(text(),"去結帳") or contains(text(),"結帳") or contains(text(),"Checkout")]'
    )
    driver.execute_script('arguments[0].click()', checkout_btn)
    time.sleep(6)

    if 'verify' in driver.current_url or 'captcha' in driver.current_url:
        tg(f'🐰 軟纖提摩西有貨，已加入購物車！\n⚠️ 結帳時遇到驗證，請手動完成\nhttps://shopee.tw/cart')
        print('CAPTCHA_CHECKOUT')
        driver.quit()
        sys.exit(0)

    # 結帳頁：確認貨到付款已選取
    page_text = driver.find_element(By.TAG_NAME, 'body').text
    if '貨到付款' not in page_text:
        tg(f'🐰 軟纖提摩西有貨，已加入購物車！\n⚠️ 結帳頁找不到貨到付款，請手動完成\nhttps://shopee.tw/cart')
        print('NO_COD_OPTION')
        driver.quit()
        sys.exit(0)

    # 若貨到付款尚未選取，嘗試點選
    try:
        cod = driver.find_element(
            By.XPATH,
            '//div[contains(text(),"貨到付款") or contains(@class,"payment")][not(contains(@class,"selected"))]'
        )
        driver.execute_script('arguments[0].click()', cod)
        time.sleep(1.5)
    except Exception:
        pass  # 已選取，不需動作

    # 提交訂單
    submit_btn = wait.until(EC.element_to_be_clickable((
        By.XPATH,
        '//button[contains(text(),"提交訂單") or contains(text(),"Place Order") or contains(text(),"下訂單")]'
    )))
    driver.execute_script('arguments[0].click()', submit_btn)
    time.sleep(6)

    # 判斷是否成功
    final_url = driver.current_url
    final_text = driver.find_element(By.TAG_NAME, 'body').text

    if 'verify' in final_url or 'captcha' in final_url:
        tg(f'🐰 軟纖提摩西有貨，已加入購物車！\n⚠️ 提交訂單時遇到驗證，請手動完成\nhttps://shopee.tw/cart')
        print('CAPTCHA_SUBMIT')
    elif any(k in final_text for k in ['訂單已成立', '訂購成功', '感謝您的訂購', 'Order Placed', 'order']):
        tg(
            f'🐰 軟纖提摩西有貨！\n'
            f'✅ {TARGET} x{QTY} 訂單已自動送出！\n'
            f'（貨到付款，請留意宅配通知）'
        )
        print('ORDER_PLACED')
    else:
        tg(
            f'🐰 軟纖提摩西有貨！\n'
            f'⚠️ 訂單狀態不確定，請確認蝦皮訂單頁\n'
            f'https://shopee.tw/user/purchase'
        )
        print('ORDER_UNCERTAIN')

except Exception as e:
    print(f'ERROR: {e}', file=sys.stderr)
    print('ERROR')
    raise

finally:
    driver.quit()

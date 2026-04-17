import os, time, subprocess
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

PROFILE = os.path.expanduser('~/Library/Application Support/Firefox/Profiles/ro7nczf2.default-release')
URL = 'https://shopee.tw/%E7%BE%8E%E5%9C%8BRabbit-Hole-Hay%E5%85%94%E5%AD%90%E6%B4%9E%E7%89%A7%E8%8D%89%E4%B8%AD%E7%BA%96-%E8%BB%9F%E7%BA%96-%E6%9E%9C%E5%9C%92(%E8%A2%8B%E8%A3%9D285g%E3%80%81908g)-i.4430087.1385536293'
TARGET = '提摩西二切(軟纖)908g'
QTY = 2
TELEGRAM_TOKEN = '8666778924:AAFMAFKfsfx3opS2CfCBrDYMIx6vcJKACTk'
CHAT_ID = '7556217543'

opts = Options()
opts.add_argument('--headless')
opts.profile = PROFILE

driver = webdriver.Firefox(options=opts)
driver.set_window_size(1440, 900)

try:
    # 先確認 session 是否有效
    driver.get('https://shopee.tw')
    time.sleep(5)
    home_body = driver.find_element(By.TAG_NAME, 'body').text
    session_ok = 'stevenhung' in home_body
    if not session_ok:
        subprocess.run([
            'curl', '-s', '-X', 'POST',
            f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage',
            '-d', f'chat_id={CHAT_ID}',
            '--data-urlencode', 'text=⚠️ 蝦皮 session 已過期，庫存監控暫停。請重新登入 Firefox 蝦皮後繼續。'
        ], capture_output=True)
        print('SESSION_EXPIRED')
        driver.quit()
        exit(0)

    driver.get(URL)
    time.sleep(7)

    # 檢查庫存（cursor: pointer = 有貨）
    btn = driver.find_element(By.XPATH, f'//button[.//span[contains(text(),"{TARGET}")]]')
    cursor = driver.execute_script('return window.getComputedStyle(arguments[0]).cursor', btn)

    if cursor != 'pointer':
        print('SOLD_OUT')
    else:
        print(f'IN_STOCK: {TARGET}')

        # 選規格
        driver.execute_script('arguments[0].click()', btn)
        time.sleep(1.5)

        # 設數量
        qty_input = driver.find_element(By.CSS_SELECTOR, 'input.suQW3X')
        qty_input.clear()
        for _ in range(5):
            qty_input.send_keys(Keys.BACKSPACE)
        qty_input.send_keys(str(QTY))
        time.sleep(1)

        # 加入購物車
        cart_btn = driver.find_element(By.XPATH, '//button[contains(@class,"btn-tinted") and contains(text(),"加入購物車")]')
        driver.execute_script('arguments[0].scrollIntoView()', cart_btn)
        driver.execute_script('arguments[0].click()', cart_btn)
        time.sleep(3)

        added = 'verify' not in driver.current_url and 'captcha' not in driver.current_url

        if added:
            msg = (
                f'🐰 Rabbit Hole Hay 軟纖提摩西有貨！\n'
                f'✅ {TARGET} x{QTY} 已加入購物車\n\n'
                f'請開蝦皮 App 前往購物車結帳\n'
                f'（店到店文德店 / 貨到付款）'
            )
        else:
            msg = (
                f'🐰 Rabbit Hole Hay 軟纖提摩西有貨！\n'
                f'⚠️ 加入購物車時遇到驗證，請手動下單\n\n'
                f'{URL}'
            )

        subprocess.run([
            'curl', '-s', '-X', 'POST',
            f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage',
            '-d', f'chat_id={CHAT_ID}',
            '--data-urlencode', f'text={msg}'
        ], capture_output=True)
        print('TELEGRAM_SENT')

finally:
    driver.quit()

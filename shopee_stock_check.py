import os, time, subprocess
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.by import By

PROFILE = os.path.expanduser('~/Library/Application Support/Firefox/Profiles/ro7nczf2.default-release')
URL = 'https://shopee.tw/%E7%BE%8E%E5%9C%8BRabbit-Hole-Hay%E5%85%94%E5%AD%90%E6%B4%9E%E7%89%A7%E8%8D%89%E4%B8%AD%E7%BA%96-%E8%BB%9F%E7%BA%96-%E6%9E%9C%E5%9C%92(%E8%A2%8B%E8%A3%9D285g%E3%80%81908g)-i.4430087.1385536293'
TARGETS = ['提摩西二切(軟纖)285g', '提摩西二切(軟纖)908g']

opts = Options()
opts.add_argument('--headless')
opts.profile = PROFILE

driver = webdriver.Firefox(options=opts)
results = {}
try:
    driver.get(URL)
    time.sleep(7)
    for name in TARGETS:
        try:
            btn = driver.find_element(By.XPATH, f'//button[.//span[contains(text(),"{name}")]]')
            cursor = driver.execute_script("return window.getComputedStyle(arguments[0]).cursor", btn)
            results[name] = 'IN_STOCK' if cursor == 'pointer' else 'SOLD_OUT'
        except Exception as e:
            results[name] = f'ERROR:{e}'
finally:
    driver.quit()

print('RESULTS:', results)
in_stock = [k for k, v in results.items() if v == 'IN_STOCK']
if in_stock:
    msg = '🐰 Rabbit Hole Hay 軟纖提摩西有貨了！\n' + '\n'.join(f'✅ {k}' for k in in_stock) + f'\n\n👉 {URL}'
    subprocess.run(['curl', '-s', '-X', 'POST',
        'https://api.telegram.org/bot8666778924:AAFMAFKfsfx3opS2CfCBrDYMIx6vcJKACTk/sendMessage',
        '-d', 'chat_id=7556217543',
        '--data-urlencode', f'text={msg}'], capture_output=True)
    print('TELEGRAM_SENT')

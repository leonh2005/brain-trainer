"""投資大老持股追蹤 — 設定檔"""

SEC_USER_AGENT = "Steven Personal Research steven.research.contact@gmail.com"

# type: '13f' 走 SEC EDGAR 13F-HR 申報；'ark' 走 ARK 每日持股 CSV
HOLDERS = [
    {"id": "buffett", "name": "Warren Buffett", "name_zh": "巴菲特",
     "type": "13f", "cik": "0001067983", "source": "Berkshire Hathaway Inc 13F-HR"},
    {"id": "cathie_wood", "name": "Cathie Wood", "name_zh": "木頭姐",
     "type": "ark", "cik": None, "source": "ARK Invest 每日持股 CSV（ARKK/ARKQ/ARKW/ARKG/ARKF）"},
    {"id": "dalio", "name": "Ray Dalio", "name_zh": "達里歐",
     "type": "13f", "cik": "0001350694", "source": "Bridgewater Associates LP 13F-HR"},
    {"id": "ackman", "name": "Bill Ackman", "name_zh": "艾克曼",
     "type": "13f", "cik": "0001336528", "source": "Pershing Square Capital Management LP 13F-HR"},
    {"id": "druckenmiller", "name": "Stanley Druckenmiller", "name_zh": "杜肯米勒",
     "type": "13f", "cik": "0001536411", "source": "Duquesne Family Office LLC 13F-HR"},
    {"id": "soros", "name": "George Soros", "name_zh": "索羅斯",
     "type": "13f", "cik": "0001029160", "source": "Soros Fund Management LLC 13F-HR"},
    {"id": "aschenbrenner", "name": "Leopold Aschenbrenner", "name_zh": "阿申布倫納（24歲AI股神）",
     "type": "13f", "cik": "0002038540", "source": "Situational Awareness Partners LP 13F-HR"},
    {"id": "icahn", "name": "Carl Icahn", "name_zh": "伊坎",
     "type": "13f", "cik": "0000921669", "source": "Icahn Carl C 13F-HR（個人申報主體，非 Icahn Enterprises 集團其他關聯體）"},
    {"id": "tepper", "name": "David Tepper", "name_zh": "泰珀",
     "type": "13f", "cik": "0001656456", "source": "Appaloosa LP 13F-HR"},
    {"id": "klarman", "name": "Seth Klarman", "name_zh": "克拉爾曼",
     "type": "13f", "cik": "0001061768", "source": "Baupost Group LLC/MA 13F-HR"},
    {"id": "li_lu", "name": "Li Lu", "name_zh": "李錄",
     "type": "13f", "cik": "0001709323", "source": "Himalaya Capital Management LLC 13F-HR"},
    {"id": "gates", "name": "Bill Gates", "name_zh": "比爾蓋茲",
     "type": "13f", "cik": "0001166559", "source": "Gates Foundation Trust 13F-HR"},
]

STEVEN_ZHOU_ID = "steven_zhou"
STEVEN_ZHOU_NAME_ZH = "Steven周"
STEVEN_ZHOU_THRESHOLD_DEFAULT = 5  # 至少N人同時持有才算進Steven周的交集

ARK_HOLDINGS_URLS = {
    "ARKK": "https://assets.ark-funds.com/fund-documents/funds-etf-csv/ARK_INNOVATION_ETF_ARKK_HOLDINGS.csv",
    "ARKQ": "https://assets.ark-funds.com/fund-documents/funds-etf-csv/ARK_AUTONOMOUS_TECH._&_ROBOTICS_ETF_ARKQ_HOLDINGS.csv",
    "ARKW": "https://assets.ark-funds.com/fund-documents/funds-etf-csv/ARK_NEXT_GENERATION_INTERNET_ETF_ARKW_HOLDINGS.csv",
    "ARKG": "https://assets.ark-funds.com/fund-documents/funds-etf-csv/ARK_GENOMIC_REVOLUTION_ETF_ARKG_HOLDINGS.csv",
    "ARKF": "https://assets.ark-funds.com/fund-documents/funds-etf-csv/ARK_FINTECH_INNOVATION_ETF_ARKF_HOLDINGS.csv",
}

OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"

DB_PATH = "guru_tracker.db"

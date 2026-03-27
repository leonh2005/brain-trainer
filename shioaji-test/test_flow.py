import shioaji as sj
from shioaji.constant import Action, StockPriceType, OrderType
from shioaji.constant import FuturesPriceType, FuturesOCType
from dotenv import load_dotenv
import os

load_dotenv()

API_KEY     = os.environ["API_KEY"]
SECRET_KEY  = os.environ["SECRET_KEY"]
CA_PATH     = os.environ["CA_CERT_PATH"]
CA_PASSWD   = os.environ["CA_PASSWORD"]


def login():
    api = sj.Shioaji(simulation=True)
    accounts = api.login(api_key=API_KEY, secret_key=SECRET_KEY)
    print(f"Shioaji {sj.__version__}")
    print(f"帳號清單: {accounts}")
    api.activate_ca(ca_path=CA_PATH, ca_passwd=CA_PASSWD)
    return api


def test_stock():
    api = login()
    contract = api.Contracts.Stocks["2890"]
    order = sj.order.StockOrder(
        action=Action.Buy,
        price=contract.reference,
        quantity=1,
        price_type=StockPriceType.LMT,
        order_type=OrderType.ROD,
        account=api.stock_account,
    )
    trade = api.place_order(contract=contract, order=order)
    api.update_status()
    print(f"股票委託狀態: {trade.status}")


def test_futures():
    api = login()
    contract = api.Contracts.Futures["TXFR1"]
    order = sj.order.FuturesOrder(
        action=Action.Buy,
        price=contract.reference,
        quantity=1,
        price_type=FuturesPriceType.LMT,
        order_type=OrderType.ROD,
        octype=FuturesOCType.Auto,
        account=api.futopt_account,
    )
    trade = api.place_order(contract=contract, order=order)
    api.update_status()
    print(f"期貨委託狀態: {trade.status}")


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "stock"
    if mode == "futures":
        test_futures()
    else:
        test_stock()

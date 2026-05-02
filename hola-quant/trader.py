"""
Shioaji 下單模組（盤中零股 IntradayOdd）
使用前需在 .env 補齊：
  SHIOAJI_CA_PATH    - 憑證檔路徑（.pfx）
  SHIOAJI_CA_PASSWD  - 憑證密碼
  SHIOAJI_PERSON_ID  - 身分證字號
"""
import logging
import os

import shioaji as sj
from shioaji.constant import Action, OrderType, StockOrderLot, StockPriceType

logger = logging.getLogger(__name__)

_api: sj.Shioaji | None = None


def _get_api() -> sj.Shioaji:
    global _api
    if _api is not None:
        return _api

    api = sj.Shioaji()
    api.login(
        api_key=os.environ["SHIOAJI_API_KEY"],
        secret_key=os.environ["SHIOAJI_SECRET_KEY"],
    )

    ca_path = os.environ.get("SHIOAJI_CA_PATH", "")
    ca_passwd = os.environ.get("SHIOAJI_CA_PASSWD", "")
    person_id = os.environ.get("SHIOAJI_PERSON_ID", "")
    if ca_path and ca_passwd:
        ok = api.activate_ca(ca_path=ca_path, ca_passwd=ca_passwd, person_id=person_id)
        if not ok:
            raise RuntimeError("CA 憑證啟動失敗，請確認路徑與密碼")

    _api = api
    return _api


def place_intraday_odd(symbol: str, price: float, shares: int) -> dict:
    """
    盤中零股買進委託
    回傳 {"status": "ok"|"error", "order_id": str, "msg": str}
    """
    try:
        api = _get_api()
        contract = api.Contracts.Stocks[symbol]
        stock_account = next(
            acc for acc in api.list_accounts()
            if isinstance(acc, sj.account.StockAccount)
        )
        order = sj.Order(
            price=round(price, 2),
            quantity=shares,
            action=Action.Buy,
            price_type=StockPriceType.LMT,
            order_type=OrderType.ROD,
            order_lot=StockOrderLot.IntradayOdd,
            account=stock_account,
        )
        trade = api.place_order(contract, order)
        order_id = trade.order.id
        logger.info(f"[trader] 下單成功 {symbol} x{shares}股 @{price} → {order_id}")
        return {"status": "ok", "order_id": order_id, "msg": f"{symbol} x{shares}股 @{price}"}
    except Exception as e:
        logger.error(f"[trader] 下單失敗 {symbol}: {e}")
        return {"status": "error", "order_id": "", "msg": str(e)}


def logout():
    global _api
    if _api:
        _api.logout()
        _api = None

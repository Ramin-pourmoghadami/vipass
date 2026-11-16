import re
import httpx
import time
from typing import Tuple
from config import get_tronscan_api_key
import db

OFFICIAL_TRC20_USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
TRON_ADDR_REGEX = re.compile(r"T[1-9A-HJ-NP-Za-km-z]{33}")

def extract_tron_wallets(text: str) -> list[str]:
    return TRON_ADDR_REGEX.findall(text or "")

async def verify_trc20_txid(txid: str, expected_wallet: str, min_usdt: float, user_id: int = 0) -> Tuple[bool, str]:
    clean_hash = txid.strip().lower()
    if len(clean_hash) != 64 or not all(c in "0123456789abcdef" for c in clean_hash):
        return False, "فرمت هش تراکنش (TXID) باید ۶۴ کاراکتر هگزادسیمال معتبر باشد."

    if not db.claim_txid_atomically(clean_hash, user_id):
        return False, "⛔ این هش تراکنش (TXID) قبلاً در سیستم ثبت و استفاده شده است!"

    wallet_tokens = extract_tron_wallets(expected_wallet)
    headers = {}
    api_key = get_tronscan_api_key()
    if api_key:
        headers["TRON-PRO-API-KEY"] = api_key

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://apilist.tronscanapi.com/api/transaction-info?hash={clean_hash}",
                headers=headers
            )
            if resp.status_code != 200:
                db.release_txid(clean_hash)
                return False, "استعلام خودکار از شبکه ترون در دسترس نیست؛ برای تایید دستی ارسال شد."
            data = resp.json()
            if not data or data.get("contractRet") != "SUCCESS":
                db.release_txid(clean_hash)
                return False, "تراکنش در شبکه ترون تایید نشده یا ناموفق است."
            tx_ts_ms = int(data.get("timestamp", 0))
            if tx_ts_ms > 0 and (time.time() - (tx_ts_ms / 1000.0)) > (48 * 3600):
                db.release_txid(clean_hash)
                return False, "این تراکنش قدیمی است (مربوط به بیش از ۴۸ ساعت قبل)."
            trc20_list = data.get("trc20TransferInfo") or []
            for item in trc20_list:
                to_addr = (item.get("to_address") or "").strip()
                contract_addr = (item.get("contract_address") or "").strip()
                symbol = (item.get("symbol") or "").upper()
                decimals = int(item.get("decimals", 6))
                raw_amt = float(item.get("amount_str", 0)) / (10 ** decimals)
                is_official_usdt = (contract_addr == OFFICIAL_TRC20_USDT_CONTRACT) or (not contract_addr and symbol == "USDT")
                if is_official_usdt and to_addr in wallet_tokens and raw_amt >= (min_usdt * 0.98):
                    return True, f"تایید خودکار بلاک‌چین ({raw_amt} USDT)"
            db.release_txid(clean_hash)
            return False, "آدرس گیرنده، قرارداد تتر یا مبلغ USDT مطابقت ندارد؛ برای بررسی دستی ارسال شد."
    except Exception:
        db.release_txid(clean_hash)
        return False, "خطا در ارتباط با نود بلاک‌چین؛ رسید برای تایید دستی ارسال شد."

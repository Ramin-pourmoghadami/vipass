import os
from dataclasses import dataclass

def read_env_dict(path=".env") -> dict:
    env = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env

def set_env_key(key: str, value: str, path=".env") -> None:
    lines = []
    found = False
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith(f"{key}="):
                    lines.append(f"{key}={value}\n")
                    found = True
                else:
                    lines.append(line if line.endswith("\n") else line + "\n")
    if not found:
        lines.append(f"{key}={value}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    os.environ[key] = str(value)

for k, v in read_env_dict().items():
    os.environ.setdefault(k, v)

def get_usd_toman_rate() -> int:
    raw = read_env_dict().get("USD_TOMAN_RATE") or os.getenv("USD_TOMAN_RATE", "270000")
    cleaned = raw.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    for ch in (",", "_", " ", "تومان"):
        cleaned = cleaned.replace(ch, "")
    try:
        return max(1000, int(float(cleaned)))
    except ValueError:
        return 270000

def get_star_toman_rate() -> int:
    return max(100, round(get_usd_toman_rate() * 0.02))

def get_platform_admin_id() -> int:
    raw = read_env_dict().get("PLATFORM_ADMIN_ID") or os.getenv("PLATFORM_ADMIN_ID", "0")
    try:
        return int(raw.strip())
    except ValueError:
        return 0

def is_platform_admin(user_id: int) -> bool:
    admin_id = get_platform_admin_id()
    return admin_id > 0 and user_id == admin_id

def get_admin_api_secret() -> str:
    val = (read_env_dict().get("ADMIN_API_SECRET") or os.getenv("ADMIN_API_SECRET", "")).strip()
    if not val or val == "change_this_secret_key":
        return ""
    return val

def get_tronscan_api_key() -> str:
    return (read_env_dict().get("TRONSCAN_API_KEY") or os.getenv("TRONSCAN_API_KEY", "")).strip()

@dataclass
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
    base_url: str = os.getenv("BASE_URL", "http://localhost:8000")
    zarinpal_sandbox: bool = os.getenv("ZARINPAL_SANDBOX", "true").lower() == "true"
    port: int = int(os.getenv("PORT", "8000"))
    db_path: str = os.getenv("DB_PATH", "vip_bot.db")
    platform_card: str = os.getenv("PLATFORM_CARD", "6037-9970-0000-0000")
    platform_crypto_wallet: str = os.getenv("PLATFORM_CRYPTO_WALLET", "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb")
    platform_gateway_type: str = os.getenv("PLATFORM_GATEWAY_TYPE", "zarinpal")
    platform_merchant_id: str = os.getenv("PLATFORM_MERCHANT_ID", "")

settings = Settings()

BASE_SAAS_TIERS = {
    "free": {
        "key": "free",
        "title": "Starter (رایگان)",
        "short_desc": "مناسب تست و شروع کانال‌های نوپا",
        "quota": 10,
        "price_usd": 0.0,
        "price_stars": 0,
        "has_branding": True,
        "features": [
            "پذیرش خودکار تا ۱۰ سفارش موفق در هر ماه",
            "اتصال هم‌زمان کارت‌به‌کارت، تتر (TRC20) و استارز تلگرام",
            "قیمت‌گذاری با تخفیف (خط‌خورده) و لینک ورود یک‌بارمصرف",
            "دارای امضای تبلیغاتی کوچک ربات در پایین پیام‌ها"
        ]
    },
    "growth": {
        "key": "growth",
        "title": "Growth (پایه - ۵۰ سفارش/ماه)",
        "short_desc": "مناسب کانال‌های فعال با فروش روزانه",
        "quota": 50,
        "price_usd": 5.0,
        "price_stars": 250,
        "has_branding": True,
        "features": [
            "افزایش ظرفیت پذیرش به ۵۰ سفارش موفق در هر ماه",
            "باز شدن ابزار ارسال پیام همگانی (Broadcast) به اعضای فعال و منقضی",
            "باز شدن سیستم ساخت کد تخفیف درصدی (Promo Codes) و اهدای اشتراک دستی",
            "اتصال هم‌زمان گروه گفتگو به کانال VIP در تمام پلن‌ها",
            "دارای امضای کوچک ربات در پایین پیام‌ها"
        ]
    },
    "pro": {
        "key": "pro",
        "title": "Pro (حرفه‌ای - ۲۰۰ سفارش/ماه + حذف تبلیغ)",
        "short_desc": "حذف کامل نام و تبلیغ ربات + مخفی‌سازی کانال",
        "quota": 200,
        "price_usd": 12.0,
        "price_stars": 600,
        "has_branding": False,
        "features": [
            "ظرفیت بالا تا ۲۰۰ سفارش موفق در هر ماه",
            "حذف ۱۰۰٪ نام و امضای تبلیغاتی ربات از زیر تمام پیام‌های کانال شما",
            "امکان مخفی‌سازی کانال از ویترین عمومی ربات (فروش فقط با لینک اختصاصی)",
            "تمامی امکانات پیام همگانی، کد تخفیف و تایید خودکار تتر (TRC20)"
        ]
    },
    "vip": {
        "key": "vip",
        "title": "VIP White-Label (نامحدود + توکن اختصاصی)",
        "short_desc": "ظرفیت بی‌نهایت + اجرای ربات با توکن و برند اختصاصی شما",
        "quota": 999999,
        "price_usd": 29.0,
        "price_stars": 1450,
        "has_branding": False,
        "features": [
            "ظرفیت پذیرش سفارش و عضویت کاملاً نامحدود (∞) در ماه",
            "امکان اتصال توکن اختصاصی BotFather (اجرای ربات با نام و لوگوی خودتان)",
            "حذف کامل تمام نام‌ها و لینک‌های پلتفرم",
            "مدیریت هم‌زمان چندین کانال VIP + اولویت پشتیبانی ویژه"
        ]
    },
}

class DynamicSaaSTiers(dict):
    def _enrich(self, item: dict) -> dict:
        d = dict(item)
        d["price_toman"] = int(round(d["price_usd"] * get_usd_toman_rate()))
        return d

    def get(self, key, default=None):
        if key in BASE_SAAS_TIERS:
            return self._enrich(BASE_SAAS_TIERS[key])
        return default

    def __getitem__(self, key):
        return self._enrich(BASE_SAAS_TIERS[key])

SAAS_TIERS = DynamicSaaSTiers(BASE_SAAS_TIERS)

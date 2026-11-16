import sqlite3
import time
from typing import Optional, List, Dict, Any
from config import settings, SAAS_TIERS, get_usd_toman_rate, get_star_toman_rate

def get_conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or settings.db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn

def backup_database_to_file(dest_path: str, db_path: Optional[str] = None) -> None:
    src = get_conn(db_path)
    dst = sqlite3.connect(dest_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

def init_db(db_path: Optional[str] = None) -> None:
    with get_conn(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS channels (
                chat_id INTEGER PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                linked_group_id INTEGER DEFAULT 0,
                linked_group_title TEXT DEFAULT '',
                gateway_type TEXT DEFAULT 'zarinpal',
                merchant_id TEXT DEFAULT '',
                card_number TEXT DEFAULT '',
                crypto_wallet TEXT DEFAULT '',
                support_handle TEXT DEFAULT '',
                is_public INTEGER DEFAULT 1,
                stars_enabled INTEGER DEFAULT 1,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                duration_days INTEGER NOT NULL,
                original_price_toman INTEGER DEFAULT 0,
                price_toman INTEGER NOT NULL,
                price_stars INTEGER DEFAULT 0,
                price_usdt REAL DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                FOREIGN KEY (chat_id) REFERENCES channels(chat_id)
            );

            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                plan_id INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                reminded INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                PRIMARY KEY (user_id, chat_id)
            );

            CREATE TABLE IF NOT EXISTS payments (
                authority TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                plan_id INTEGER NOT NULL,
                amount_toman INTEGER NOT NULL,
                amount_usdt REAL DEFAULT 0,
                promo_code TEXT DEFAULT '',
                method TEXT NOT NULL,
                status TEXT DEFAULT 'initiated',
                ref_id TEXT DEFAULT '',
                receipt_text TEXT DEFAULT '',
                receipt_photo TEXT DEFAULT '',
                receipt_doc TEXT DEFAULT '',
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS admin_accounts (
                owner_id INTEGER PRIMARY KEY,
                tier TEXT DEFAULT 'free',
                tier_expires_at INTEGER DEFAULT 0,
                monthly_orders_used INTEGER DEFAULT 0,
                cycle_started_at INTEGER NOT NULL,
                referred_by INTEGER DEFAULT 0,
                wallet_balance_usd REAL DEFAULT 0.0,
                total_earned_usd REAL DEFAULT 0.0,
                whitelabel_token TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS platform_payments (
                authority TEXT PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                target_tier TEXT NOT NULL,
                amount_toman INTEGER NOT NULL,
                amount_usd REAL NOT NULL,
                amount_stars INTEGER NOT NULL,
                method TEXT NOT NULL,
                status TEXT DEFAULT 'initiated',
                ref_id TEXT DEFAULT '',
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS payouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                amount_usd REAL NOT NULL,
                destination TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS used_txids (
                txid TEXT PRIMARY KEY,
                used_by INTEGER NOT NULL,
                used_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS promo_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                discount_pct INTEGER NOT NULL,
                max_uses INTEGER NOT NULL,
                used_count INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                UNIQUE(chat_id, code)
            );

            CREATE TABLE IF NOT EXISTS promo_uses (
                promo_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                used_at INTEGER NOT NULL,
                PRIMARY KEY (promo_id, user_id)
            );
        """)
        cols_ch = [r["name"] for r in conn.execute("PRAGMA table_info(channels)").fetchall()]
        for col, cdef in [
            ("linked_group_id", "INTEGER DEFAULT 0"),
            ("linked_group_title", "TEXT DEFAULT ''"),
            ("gateway_type", "TEXT DEFAULT 'zarinpal'"),
            ("stars_enabled", "INTEGER DEFAULT 1"),
            ("crypto_wallet", "TEXT DEFAULT ''"),
            ("support_handle", "TEXT DEFAULT ''"),
            ("is_public", "INTEGER DEFAULT 1")
        ]:
            if col not in cols_ch:
                conn.execute(f"ALTER TABLE channels ADD COLUMN {col} {cdef}")

        cols_pl = [r["name"] for r in conn.execute("PRAGMA table_info(plans)").fetchall()]
        for col, cdef in [
            ("original_price_toman", "INTEGER DEFAULT 0"),
            ("price_stars", "INTEGER DEFAULT 0"),
            ("price_usdt", "REAL DEFAULT 0"),
            ("is_active", "INTEGER DEFAULT 1")
        ]:
            if col not in cols_pl:
                conn.execute(f"ALTER TABLE plans ADD COLUMN {col} {cdef}")

        cols_pay = [r["name"] for r in conn.execute("PRAGMA table_info(payments)").fetchall()]
        for col, cdef in [
            ("amount_usdt", "REAL DEFAULT 0"),
            ("promo_code", "TEXT DEFAULT ''"),
            ("receipt_text", "TEXT DEFAULT ''"),
            ("receipt_photo", "TEXT DEFAULT ''"),
            ("receipt_doc", "TEXT DEFAULT ''")
        ]:
            if col not in cols_pay:
                conn.execute(f"ALTER TABLE payments ADD COLUMN {col} {cdef}")

        cols_adm = [r["name"] for r in conn.execute("PRAGMA table_info(admin_accounts)").fetchall()]
        if "whitelabel_token" not in cols_adm:
            conn.execute("ALTER TABLE admin_accounts ADD COLUMN whitelabel_token TEXT DEFAULT ''")

        conn.execute("UPDATE payments SET status = 'initiated' WHERE status = 'pending' AND receipt_text = '' AND receipt_photo = '' AND receipt_doc = ''")

def claim_txid_atomically(txid: str, user_id: int, db_path: Optional[str] = None) -> bool:
    clean = txid.strip().lower()
    if len(clean) != 64:
        return False
    try:
        with get_conn(db_path) as conn:
            conn.execute(
                "INSERT INTO used_txids (txid, used_by, used_at) VALUES (?, ?, ?)",
                (clean, user_id, int(time.time()))
            )
        return True
    except sqlite3.IntegrityError:
        return False

def release_txid(txid: str, db_path: Optional[str] = None) -> None:
    clean = txid.strip().lower()
    with get_conn(db_path) as conn:
        conn.execute("DELETE FROM used_txids WHERE txid = ?", (clean,))

def is_txid_already_used(txid: str, db_path: Optional[str] = None) -> bool:
    clean = txid.strip().lower()
    if not clean:
        return False
    with get_conn(db_path) as conn:
        r0 = conn.execute("SELECT 1 FROM used_txids WHERE txid = ?", (clean,)).fetchone()
        r1 = conn.execute("SELECT 1 FROM payments WHERE status = 'paid' AND LOWER(ref_id) = ?", (clean,)).fetchone()
        r2 = conn.execute("SELECT 1 FROM platform_payments WHERE status = 'paid' AND LOWER(ref_id) = ?", (clean,)).fetchone()
        return bool(r0 or r1 or r2)

def verify_channel_owner(chat_id: int, user_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT * FROM channels WHERE chat_id = ? AND owner_id = ?", (chat_id, user_id)).fetchone()
        return dict(row) if row else None

def ensure_admin_account(owner_id: int, referred_by: int = 0, db_path: Optional[str] = None) -> Dict[str, Any]:
    now = int(time.time())
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT * FROM admin_accounts WHERE owner_id = ?", (owner_id,)).fetchone()
        if not row:
            ref = referred_by if (referred_by and referred_by != owner_id) else 0
            conn.execute(
                "INSERT INTO admin_accounts (owner_id, tier, tier_expires_at, monthly_orders_used, cycle_started_at, referred_by, wallet_balance_usd, total_earned_usd, whitelabel_token) "
                "VALUES (?, 'free', 0, 0, ?, ?, 0.0, 0.0, '')",
                (owner_id, now, ref)
            )
            row = conn.execute("SELECT * FROM admin_accounts WHERE owner_id = ?", (owner_id,)).fetchone()
        acc = dict(row)
        if acc["tier"] != "free" and acc["tier_expires_at"] > 0 and acc["tier_expires_at"] <= now:
            conn.execute(
                "UPDATE admin_accounts SET tier = 'free', tier_expires_at = 0, monthly_orders_used = 0, cycle_started_at = ?, whitelabel_token = '' WHERE owner_id = ?",
                (now, owner_id)
            )
            acc = dict(conn.execute("SELECT * FROM admin_accounts WHERE owner_id = ?", (owner_id,)).fetchone())
        elif now - acc["cycle_started_at"] >= 30 * 86400:
            conn.execute(
                "UPDATE admin_accounts SET monthly_orders_used = 0, cycle_started_at = ? WHERE owner_id = ?",
                (now, owner_id)
            )
            acc = dict(conn.execute("SELECT * FROM admin_accounts WHERE owner_id = ?", (owner_id,)).fetchone())
        return acc

def get_admin_quota_status(owner_id: int, db_path: Optional[str] = None) -> Dict[str, Any]:
    acc = ensure_admin_account(owner_id, db_path=db_path)
    tier_info = SAAS_TIERS.get(acc["tier"], SAAS_TIERS["free"])
    quota = tier_info["quota"]
    used = acc["monthly_orders_used"]
    remaining = max(0, quota - used)
    return {
        "owner_id": owner_id,
        "tier": acc["tier"],
        "tier_title": tier_info["title"],
        "tier_expires_at": acc["tier_expires_at"],
        "quota": quota,
        "used": used,
        "remaining": remaining,
        "is_locked": used >= quota,
        "has_branding": tier_info["has_branding"],
        "wallet_balance_usd": round(acc["wallet_balance_usd"], 2),
        "total_earned_usd": round(acc["total_earned_usd"], 2),
        "referred_by": acc["referred_by"],
        "whitelabel_token": acc.get("whitelabel_token", ""),
    }

def set_admin_whitelabel_token(owner_id: int, token: str, db_path: Optional[str] = None) -> None:
    with get_conn(db_path) as conn:
        conn.execute("UPDATE admin_accounts SET whitelabel_token = ? WHERE owner_id = ?", (token.strip(), owner_id))

def get_all_whitelabel_tokens(db_path: Optional[str] = None) -> List[str]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT whitelabel_token FROM admin_accounts WHERE tier = 'vip' AND whitelabel_token != ''"
        ).fetchall()
        return [r["whitelabel_token"] for r in rows]

def set_admin_tier_manually(owner_id: int, target_tier: str, days: int = 30, db_path: Optional[str] = None) -> bool:
    if target_tier not in SAAS_TIERS:
        return False
    ensure_admin_account(owner_id, db_path=db_path)
    now = int(time.time())
    exp = 0 if target_tier == "free" else now + (days * 86400)
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE admin_accounts SET tier = ?, tier_expires_at = ?, monthly_orders_used = 0, cycle_started_at = ? WHERE owner_id = ?",
            (target_tier, exp, now, owner_id)
        )
    return True

def get_affiliate_stats(owner_id: int, db_path: Optional[str] = None) -> Dict[str, Any]:
    acc = ensure_admin_account(owner_id, db_path=db_path)
    with get_conn(db_path) as conn:
        invited_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM admin_accounts WHERE referred_by = ?", (owner_id,)
        ).fetchone()["cnt"]
        paid_referrals = conn.execute(
            "SELECT COUNT(*) as cnt FROM admin_accounts WHERE referred_by = ? AND tier != 'free'", (owner_id,)
        ).fetchone()["cnt"]
    return {
        "invited_count": invited_count,
        "paid_referrals": paid_referrals,
        "wallet_balance_usd": round(acc["wallet_balance_usd"], 2),
        "total_earned_usd": round(acc["total_earned_usd"], 2),
    }

def create_payout_request(owner_id: int, destination: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    acc = ensure_admin_account(owner_id, db_path=db_path)
    bal = round(acc["wallet_balance_usd"], 2)
    if bal < 5.0:
        return None
    now = int(time.time())
    with get_conn(db_path) as conn:
        conn.execute("UPDATE admin_accounts SET wallet_balance_usd = 0.0 WHERE owner_id = ?", (owner_id,))
        cur = conn.execute(
            "INSERT INTO payouts (owner_id, amount_usd, destination, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
            (owner_id, bal, destination, now)
        )
        pid = cur.lastrowid
        return dict(conn.execute("SELECT * FROM payouts WHERE id = ?", (pid,)).fetchone())

def mark_payout_done(payout_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        conn.execute("UPDATE payouts SET status = 'paid' WHERE id = ?", (payout_id,))
        row = conn.execute("SELECT * FROM payouts WHERE id = ?", (payout_id,)).fetchone()
        return dict(row) if row else None

def get_superadmin_stats(db_path: Optional[str] = None) -> Dict[str, Any]:
    with get_conn(db_path) as conn:
        total_admins = conn.execute("SELECT COUNT(*) as cnt FROM admin_accounts").fetchone()["cnt"]
        free_admins = conn.execute("SELECT COUNT(*) as cnt FROM admin_accounts WHERE tier = 'free'").fetchone()["cnt"]
        growth_admins = conn.execute("SELECT COUNT(*) as cnt FROM admin_accounts WHERE tier = 'growth'").fetchone()["cnt"]
        pro_admins = conn.execute("SELECT COUNT(*) as cnt FROM admin_accounts WHERE tier = 'pro'").fetchone()["cnt"]
        vip_admins = conn.execute("SELECT COUNT(*) as cnt FROM admin_accounts WHERE tier = 'vip'").fetchone()["cnt"]
        total_channels = conn.execute("SELECT COUNT(*) as cnt FROM channels").fetchone()["cnt"]
        total_subs = conn.execute("SELECT COUNT(*) as cnt FROM subscriptions WHERE status = 'active'").fetchone()["cnt"]
        plat_usd = conn.execute("SELECT COALESCE(SUM(amount_usd), 0) as s FROM platform_payments WHERE status = 'paid'").fetchone()["s"]
        return {
            "total_admins": total_admins,
            "free_admins": free_admins,
            "growth_admins": growth_admins,
            "pro_admins": pro_admins,
            "vip_admins": vip_admins,
            "total_channels": total_channels,
            "total_subs": total_subs,
            "plat_usd": round(plat_usd, 2),
        }

def create_platform_payment(
    authority: str, owner_id: int, target_tier: str, method: str, db_path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    ensure_admin_account(owner_id, db_path=db_path)
    t = SAAS_TIERS.get(target_tier)
    if not t:
        return None
    with get_conn(db_path) as conn:
        conn.execute("DELETE FROM platform_payments WHERE owner_id = ? AND status = 'initiated'", (owner_id,))
        conn.execute(
            "INSERT INTO platform_payments (authority, owner_id, target_tier, amount_toman, amount_usd, amount_stars, method, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'initiated', ?)",
            (authority, owner_id, target_tier, t["price_toman"], t["price_usd"], t["price_stars"], method, int(time.time()))
        )
        row = conn.execute("SELECT * FROM platform_payments WHERE authority = ?", (authority,)).fetchone()
        return dict(row) if row else None

def get_platform_payment(authority: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT * FROM platform_payments WHERE authority = ?", (authority,)).fetchone()
        return dict(row) if row else None

def get_latest_pending_platform_manual(owner_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM platform_payments WHERE owner_id = ? AND method IN ('card', 'crypto') AND status IN ('initiated', 'submitted') ORDER BY created_at DESC LIMIT 1",
            (owner_id,)
        ).fetchone()
        return dict(row) if row else None

def mark_platform_payment_paid(authority: str, ref_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    now = int(time.time())
    with get_conn(db_path) as conn:
        pp = conn.execute("SELECT * FROM platform_payments WHERE authority = ?", (authority,)).fetchone()
        if not pp:
            return None
        pp = dict(pp)
        if pp["status"] == "paid":
            return pp

        owner_id = pp["owner_id"]
        target_tier = pp["target_tier"]
        acc = ensure_admin_account(owner_id, db_path=db_path)
        base_exp = max(now, acc["tier_expires_at"]) if acc["tier"] == target_tier else now
        new_exp = base_exp + (30 * 86400)

        conn.execute("UPDATE platform_payments SET status = 'paid', ref_id = ? WHERE authority = ?", (ref_id, authority))
        conn.execute(
            "UPDATE admin_accounts SET tier = ?, tier_expires_at = ?, monthly_orders_used = 0, cycle_started_at = ? WHERE owner_id = ?",
            (target_tier, new_exp, now, owner_id)
        )

        commission_usd = 0.0
        referrer_id = acc.get("referred_by", 0)
        if referrer_id and referrer_id != owner_id and pp["amount_usd"] > 0:
            ensure_admin_account(referrer_id, db_path=db_path)
            commission_usd = round(pp["amount_usd"] * 0.30, 2)
            conn.execute(
                "UPDATE admin_accounts SET wallet_balance_usd = wallet_balance_usd + ?, total_earned_usd = total_earned_usd + ? WHERE owner_id = ?",
                (commission_usd, commission_usd, referrer_id)
            )

        updated = dict(conn.execute("SELECT * FROM platform_payments WHERE authority = ?", (authority,)).fetchone())
        updated["commission_usd"] = commission_usd
        updated["referrer_id"] = referrer_id
        return updated

def upgrade_tier_with_wallet(owner_id: int, target_tier: str, db_path: Optional[str] = None) -> bool:
    acc = ensure_admin_account(owner_id, db_path=db_path)
    t = SAAS_TIERS.get(target_tier)
    if not t or t["price_usd"] <= 0 or acc["wallet_balance_usd"] < t["price_usd"]:
        return False
    now = int(time.time())
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE admin_accounts SET wallet_balance_usd = wallet_balance_usd - ?, tier = ?, tier_expires_at = ?, monthly_orders_used = 0, cycle_started_at = ? WHERE owner_id = ?",
            (t["price_usd"], target_tier, now + 30 * 86400, now, owner_id)
        )
    return True

def upsert_channel(chat_id: int, owner_id: int, title: str, db_path: Optional[str] = None) -> bool:
    ensure_admin_account(owner_id, db_path=db_path)
    now = int(time.time())
    with get_conn(db_path) as conn:
        existing = conn.execute("SELECT * FROM channels WHERE chat_id = ?", (chat_id,)).fetchone()
        if existing:
            if existing["owner_id"] != owner_id:
                return False
            conn.execute("UPDATE channels SET title = ? WHERE chat_id = ? AND owner_id = ?", (title, chat_id, owner_id))
            return True
        conn.execute(
            "INSERT INTO channels (chat_id, owner_id, title, linked_group_id, linked_group_title, gateway_type, merchant_id, card_number, crypto_wallet, support_handle, is_public, stars_enabled, created_at) "
            "VALUES (?, ?, ?, 0, '', 'zarinpal', '', '', '', '', 1, 1, ?)",
            (chat_id, owner_id, title, now)
        )
        return True

def set_channel_linked_group(chat_id: int, owner_id: int, group_id: int, group_title: str, db_path: Optional[str] = None) -> bool:
    with get_conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE channels SET linked_group_id = ?, linked_group_title = ? WHERE chat_id = ? AND owner_id = ?",
            (group_id, group_title, chat_id, owner_id)
        )
        return cur.rowcount > 0

def update_channel_card(chat_id: int, owner_id: int, card_number: str, db_path: Optional[str] = None) -> bool:
    with get_conn(db_path) as conn:
        cur = conn.execute("UPDATE channels SET card_number = ? WHERE chat_id = ? AND owner_id = ?", (card_number, chat_id, owner_id))
        return cur.rowcount > 0

def update_channel_crypto(chat_id: int, owner_id: int, crypto_wallet: str, db_path: Optional[str] = None) -> bool:
    with get_conn(db_path) as conn:
        cur = conn.execute("UPDATE channels SET crypto_wallet = ? WHERE chat_id = ? AND owner_id = ?", (crypto_wallet, chat_id, owner_id))
        return cur.rowcount > 0

def update_channel_support(chat_id: int, owner_id: int, support_handle: str, db_path: Optional[str] = None) -> bool:
    with get_conn(db_path) as conn:
        cur = conn.execute("UPDATE channels SET support_handle = ? WHERE chat_id = ? AND owner_id = ?", (support_handle, chat_id, owner_id))
        return cur.rowcount > 0

def toggle_channel_public(chat_id: int, owner_id: int, is_public: int, db_path: Optional[str] = None) -> bool:
    with get_conn(db_path) as conn:
        cur = conn.execute("UPDATE channels SET is_public = ? WHERE chat_id = ? AND owner_id = ?", (is_public, chat_id, owner_id))
        return cur.rowcount > 0

def toggle_channel_stars(chat_id: int, owner_id: int, enabled: int, db_path: Optional[str] = None) -> bool:
    with get_conn(db_path) as conn:
        cur = conn.execute("UPDATE channels SET stars_enabled = ? WHERE chat_id = ? AND owner_id = ?", (enabled, chat_id, owner_id))
        return cur.rowcount > 0

def get_channel(chat_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT * FROM channels WHERE chat_id = ?", (chat_id,)).fetchone()
        return dict(row) if row else None

def _enrich_plan_live_rates(p: Dict[str, Any]) -> Dict[str, Any]:
    usd_rate = get_usd_toman_rate()
    star_rate = get_star_toman_rate()
    sell_toman = p["price_toman"]
    orig_toman = p.get("original_price_toman") or 0
    if orig_toman <= sell_toman:
        orig_toman = 0
    p["original_price_toman"] = orig_toman
    p["discount_pct"] = round((1 - (sell_toman / orig_toman)) * 100) if orig_toman > sell_toman else 0
    p["price_usdt"] = round(max(0.5, sell_toman / usd_rate), 2)
    p["price_stars"] = max(1, round(sell_toman / star_rate))
    p["orig_price_usdt"] = round(max(0.5, orig_toman / usd_rate), 2) if orig_toman > 0 else 0.0
    p["orig_price_stars"] = max(1, round(orig_toman / star_rate)) if orig_toman > 0 else 0
    return p

def create_plan(
    chat_id: int, name: str, duration_days: int, price_toman: int,
    original_price_toman: int = 0, price_stars: int = 0, price_usdt: float = 0.0,
    db_path: Optional[str] = None
) -> int:
    if original_price_toman <= price_toman:
        original_price_toman = 0
    usd_rate = get_usd_toman_rate()
    star_rate = get_star_toman_rate()
    if price_stars <= 0:
        price_stars = max(1, round(price_toman / star_rate))
    if price_usdt <= 0:
        price_usdt = round(max(0.5, price_toman / usd_rate), 2)
    with get_conn(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO plans (chat_id, name, duration_days, original_price_toman, price_toman, price_stars, price_usdt, is_active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (chat_id, name, duration_days, original_price_toman, price_toman, price_stars, price_usdt)
        )
        return cur.lastrowid

def update_plan_pricing(plan_id: int, owner_id: int, new_price_toman: int, new_original_toman: int = 0, db_path: Optional[str] = None) -> Optional[int]:
    if new_original_toman <= new_price_toman:
        new_original_toman = 0
    usd_rate = get_usd_toman_rate()
    star_rate = get_star_toman_rate()
    auto_stars = max(1, round(new_price_toman / star_rate))
    auto_usdt = round(max(0.5, new_price_toman / usd_rate), 2)
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT p.chat_id FROM plans p JOIN channels c ON p.chat_id = c.chat_id WHERE p.id = ? AND c.owner_id = ?",
            (plan_id, owner_id)
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE plans SET original_price_toman = ?, price_toman = ?, price_stars = ?, price_usdt = ? WHERE id = ?",
            (new_original_toman, new_price_toman, auto_stars, auto_usdt, plan_id)
        )
        return row["chat_id"]

def delete_plan(plan_id: int, owner_id: int, db_path: Optional[str] = None) -> Optional[int]:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT p.chat_id FROM plans p JOIN channels c ON p.chat_id = c.chat_id WHERE p.id = ? AND c.owner_id = ?",
            (plan_id, owner_id)
        ).fetchone()
        if not row:
            return None
        conn.execute("UPDATE plans SET is_active = 0 WHERE id = ?", (plan_id,))
        return row["chat_id"]

def get_channel_plans(chat_id: int, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        rows = conn.execute("SELECT * FROM plans WHERE chat_id = ? AND is_active = 1 ORDER BY price_toman ASC", (chat_id,)).fetchall()
        return [_enrich_plan_live_rates(dict(r)) for r in rows]

def get_plan(plan_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT p.*, c.title as channel_title, c.linked_group_id, c.linked_group_title, c.gateway_type, c.merchant_id, c.card_number, c.crypto_wallet, c.support_handle, c.is_public, c.stars_enabled, c.owner_id "
            "FROM plans p JOIN channels c ON p.chat_id = c.chat_id WHERE p.id = ?",
            (plan_id,)
        ).fetchone()
        return _enrich_plan_live_rates(dict(row)) if row else None

def create_promo_code(chat_id: int, code: str, discount_pct: int, max_uses: int, db_path: Optional[str] = None) -> bool:
    clean_code = code.strip().upper()
    if not clean_code or discount_pct <= 0 or discount_pct > 95 or max_uses <= 0:
        return False
    with get_conn(db_path) as conn:
        conn.execute(
            """INSERT INTO promo_codes (chat_id, code, discount_pct, max_uses, used_count, is_active)
               VALUES (?, ?, ?, ?, 0, 1)
               ON CONFLICT(chat_id, code) DO UPDATE SET
               discount_pct = excluded.discount_pct, max_uses = excluded.max_uses, used_count = 0, is_active = 1""",
            (chat_id, clean_code, discount_pct, max_uses)
        )
        return True

def get_channel_promos(chat_id: int, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        rows = conn.execute("SELECT * FROM promo_codes WHERE chat_id = ? AND is_active = 1 ORDER BY id DESC", (chat_id,)).fetchall()
        return [dict(r) for r in rows]

def delete_promo_code(promo_id: int, owner_id: int, db_path: Optional[str] = None) -> Optional[int]:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT pr.chat_id FROM promo_codes pr JOIN channels c ON pr.chat_id = c.chat_id WHERE pr.id = ? AND c.owner_id = ?",
            (promo_id, owner_id)
        ).fetchone()
        if not row:
            return None
        conn.execute("UPDATE promo_codes SET is_active = 0 WHERE id = ?", (promo_id,))
        return row["chat_id"]

def validate_promo_code(chat_id: int, user_id: int, code: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    clean_code = code.strip().upper()
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM promo_codes WHERE chat_id = ? AND code = ? AND is_active = 1 AND used_count < max_uses",
            (chat_id, clean_code)
        ).fetchone()
        if not row:
            return None
        promo = dict(row)
        already = conn.execute("SELECT 1 FROM promo_uses WHERE promo_id = ? AND user_id = ?", (promo["id"], user_id)).fetchone()
        if already:
            return None
        return promo

def get_owner_channels(owner_id: int, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        rows = conn.execute("SELECT * FROM channels WHERE owner_id = ?", (owner_id,)).fetchall()
        return [dict(r) for r in rows]

def get_public_channels(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT c.chat_id, c.title FROM channels c JOIN plans p ON c.chat_id = p.chat_id WHERE c.is_public = 1 AND p.is_active = 1 ORDER BY c.created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

def get_channel_stats(chat_id: int, db_path: Optional[str] = None) -> Dict[str, Any]:
    now = int(time.time())
    with get_conn(db_path) as conn:
        active_subs = conn.execute(
            "SELECT COUNT(*) as cnt FROM subscriptions WHERE chat_id = ? AND status = 'active' AND expires_at > ?",
            (chat_id, now)
        ).fetchone()["cnt"]
        expired_subs = conn.execute(
            "SELECT COUNT(*) as cnt FROM subscriptions WHERE chat_id = ? AND (status = 'expired' OR expires_at <= ?)",
            (chat_id, now)
        ).fetchone()["cnt"]
        revenue = conn.execute(
            "SELECT COALESCE(SUM(amount_toman), 0) as total FROM payments WHERE chat_id = ? AND status = 'paid' AND method = 'card'",
            (chat_id,)
        ).fetchone()["total"]
        stars_rev = conn.execute(
            "SELECT COALESCE(SUM(amount_toman), 0) as total FROM payments WHERE chat_id = ? AND status = 'paid' AND method = 'stars'",
            (chat_id,)
        ).fetchone()["total"]
        crypto_row = conn.execute(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(amount_usdt), 0) as usdt_sum FROM payments WHERE chat_id = ? AND status = 'paid' AND method = 'crypto'",
            (chat_id,)
        ).fetchone()
        pending = conn.execute(
            "SELECT COUNT(*) as cnt FROM payments WHERE chat_id = ? AND status = 'submitted' AND method IN ('card', 'crypto')",
            (chat_id,)
        ).fetchone()["cnt"]
        return {
            "active_subs": active_subs,
            "expired_subs": expired_subs,
            "revenue": revenue,
            "stars_rev": stars_rev,
            "crypto_count": crypto_row["cnt"],
            "crypto_usdt": round(crypto_row["usdt_sum"], 2),
            "pending_manual": pending
        }

def get_submitted_receipts_for_channel(chat_id: int, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT pay.*, p.name as plan_name, c.title as channel_title "
            "FROM payments pay LEFT JOIN plans p ON pay.plan_id = p.id JOIN channels c ON pay.chat_id = c.chat_id "
            "WHERE pay.chat_id = ? AND pay.status = 'submitted' ORDER BY pay.created_at ASC",
            (chat_id,)
        ).fetchall()
        return [dict(r) for r in rows]

def get_user_submitted_receipts(user_id: int, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT pay.*, c.title as channel_title, p.name as plan_name "
            "FROM payments pay JOIN channels c ON pay.chat_id = c.chat_id LEFT JOIN plans p ON pay.plan_id = p.id "
            "WHERE pay.user_id = ? AND pay.status = 'submitted' ORDER BY pay.created_at DESC",
            (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]

def get_channel_Subscriber_ids(chat_id: int, target_group: str = "active", db_path: Optional[str] = None) -> List[int]:
    now = int(time.time())
    with get_conn(db_path) as conn:
        if target_group == "active":
            rows = conn.execute(
                "SELECT DISTINCT user_id FROM subscriptions WHERE chat_id = ? AND status = 'active' AND expires_at > ?",
                (chat_id, now)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT user_id FROM subscriptions WHERE chat_id = ? AND (status = 'expired' OR expires_at <= ?)",
                (chat_id, now)
            ).fetchall()
        return [r["user_id"] for r in rows]

def grant_manual_subscription(user_id: int, chat_id: int, owner_id: int, days: int, db_path: Optional[str] = None) -> int:
    now = int(time.time())
    duration_sec = days * 86400
    with get_conn(db_path) as conn:
        existing = conn.execute(
            "SELECT expires_at FROM subscriptions WHERE user_id = ? AND chat_id = ? AND status = 'active'",
            (user_id, chat_id)
        ).fetchone()
        base_time = max(now, existing["expires_at"]) if existing and existing["expires_at"] > now else now
        new_expiry = base_time + duration_sec
        conn.execute(
            """INSERT INTO subscriptions (user_id, chat_id, plan_id, expires_at, reminded, status)
               VALUES (?, ?, 0, ?, 0, 'active')
               ON CONFLICT(user_id, chat_id) DO UPDATE SET
               expires_at = excluded.expires_at, reminded = 0, status = 'active' """,
            (user_id, chat_id, new_expiry)
        )
        conn.execute(
            "UPDATE admin_accounts SET monthly_orders_used = monthly_orders_used + 1 WHERE owner_id = ?",
            (owner_id,)
        )
        return new_expiry

def get_user_subscriptions(user_id: int, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    now = int(time.time())
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT s.*, c.title as channel_title, c.linked_group_id, c.linked_group_title, c.support_handle, p.name as plan_name "
            "FROM subscriptions s "
            "JOIN channels c ON s.chat_id = c.chat_id "
            "LEFT JOIN plans p ON s.plan_id = p.id "
            "WHERE s.user_id = ? AND s.status = 'active' AND s.expires_at > ?",
            (user_id, now)
        ).fetchall()
        return [dict(r) for r in rows]

def cancel_abandoned_initiations(user_id: int, db_path: Optional[str] = None) -> None:
    with get_conn(db_path) as conn:
        conn.execute("DELETE FROM payments WHERE user_id = ? AND status = 'initiated'", (user_id,))

def get_latest_pending_manual(user_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM payments WHERE user_id = ? AND method IN ('card', 'crypto') AND status = 'initiated' ORDER BY created_at DESC LIMIT 1",
            (user_id,)
        ).fetchone()
        return dict(row) if row else None

def create_payment(
    authority: str, user_id: int, chat_id: int, plan_id: int,
    amount_toman: int, method: str, amount_usdt: float = 0.0, promo_code: str = "",
    db_path: Optional[str] = None
) -> None:
    cancel_abandoned_initiations(user_id, db_path=db_path)
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO payments (authority, user_id, chat_id, plan_id, amount_toman, amount_usdt, promo_code, method, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'initiated', ?)",
            (authority, user_id, chat_id, plan_id, amount_toman, amount_usdt, promo_code.upper(), method, int(time.time()))
        )

def mark_payment_submitted(
    authority: str, receipt_text: str = "", receipt_photo: str = "", receipt_doc: str = "",
    db_path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE payments SET status = 'submitted', receipt_text = ?, receipt_photo = ?, receipt_doc = ? WHERE authority = ? AND status = 'initiated'",
            (receipt_text, receipt_photo, receipt_doc, authority)
        )
        row = conn.execute("SELECT * FROM payments WHERE authority = ?", (authority,)).fetchone()
        return dict(row) if row else None

def mark_payment_rejected(authority: str, expected_owner_id: Optional[int] = None, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        if expected_owner_id is not None:
            owner_check = conn.execute(
                "SELECT c.owner_id FROM payments p JOIN channels c ON p.chat_id = c.chat_id WHERE p.authority = ?",
                (authority,)
            ).fetchone()
            if not owner_check or owner_check["owner_id"] != expected_owner_id:
                return None
        conn.execute("UPDATE payments SET status = 'rejected' WHERE authority = ? AND status = 'submitted'", (authority,))
        row = conn.execute("SELECT * FROM payments WHERE authority = ?", (authority,)).fetchone()
        return dict(row) if row else None

def get_payment(authority: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT * FROM payments WHERE authority = ?", (authority,)).fetchone()
        return dict(row) if row else None

def mark_payment_paid(authority: str, ref_id: str, expected_owner_id: Optional[int] = None, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        payment = conn.execute("SELECT * FROM payments WHERE authority = ?", (authority,)).fetchone()
        if not payment:
            return None
        payment = dict(payment)
        plan = conn.execute(
            "SELECT p.*, c.owner_id, c.title as channel_title FROM plans p JOIN channels c ON p.chat_id = c.chat_id WHERE p.id = ?",
            (payment["plan_id"],)
        ).fetchone()
        if not plan:
            return None
        owner_id = plan["owner_id"]
        if expected_owner_id is not None and owner_id != expected_owner_id:
            return None
        if payment["status"] == "paid":
            return payment

        ensure_admin_account(owner_id, db_path=db_path)
        now = int(time.time())
        duration_sec = plan["duration_days"] * 86400
        existing = conn.execute(
            "SELECT expires_at FROM subscriptions WHERE user_id = ? AND chat_id = ? AND status = 'active'",
            (payment["user_id"], payment["chat_id"])
        ).fetchone()
        base_time = max(now, existing["expires_at"]) if existing and existing["expires_at"] > now else now
        new_expiry = base_time + duration_sec

        conn.execute("UPDATE payments SET status = 'paid', ref_id = ? WHERE authority = ?", (ref_id, authority))
        conn.execute(
            """INSERT INTO subscriptions (user_id, chat_id, plan_id, expires_at, reminded, status)
               VALUES (?, ?, ?, ?, 0, 'active')
               ON CONFLICT(user_id, chat_id) DO UPDATE SET
               plan_id = excluded.plan_id, expires_at = excluded.expires_at, reminded = 0, status = 'active' """,
            (payment["user_id"], payment["chat_id"], payment["plan_id"], new_expiry)
        )
        conn.execute(
            "UPDATE admin_accounts SET monthly_orders_used = monthly_orders_used + 1 WHERE owner_id = ?",
            (owner_id,)
        )
        if payment.get("promo_code"):
            pr = conn.execute(
                "SELECT id FROM promo_codes WHERE chat_id = ? AND code = ?",
                (payment["chat_id"], payment["promo_code"])
            ).fetchone()
            if pr:
                conn.execute("UPDATE promo_codes SET used_count = used_count + 1 WHERE id = ?", (pr["id"],))
                conn.execute(
                    "INSERT OR IGNORE INTO promo_uses (promo_id, user_id, used_at) VALUES (?, ?, ?)",
                    (pr["id"], payment["user_id"], now)
                )

        updated = dict(conn.execute("SELECT * FROM payments WHERE authority = ?", (authority,)).fetchone())
        updated["owner_id"] = owner_id
        updated["channel_title"] = plan["channel_title"]
        return updated

def is_subscription_active(user_id: int, any_chat_id: int, db_path: Optional[str] = None) -> bool:
    now = int(time.time())
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT s.expires_at FROM subscriptions s "
            "JOIN channels c ON s.chat_id = c.chat_id "
            "WHERE s.user_id = ? AND (s.chat_id = ? OR c.linked_group_id = ?) AND s.status = 'active'",
            (user_id, any_chat_id, any_chat_id)
        ).fetchone()
        return bool(row and row["expires_at"] > now)

def get_due_reminders(now: Optional[int] = None, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    now = now or int(time.time())
    soon = now + 86400
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT s.*, c.title as channel_title, c.owner_id FROM subscriptions s JOIN channels c ON s.chat_id = c.chat_id "
            "WHERE s.status = 'active' AND s.reminded = 0 AND s.expires_at > ? AND s.expires_at <= ?",
            (now, soon)
        ).fetchall()
        return [dict(r) for r in rows]

def mark_reminded(user_id: int, chat_id: int, db_path: Optional[str] = None) -> None:
    with get_conn(db_path) as conn:
        conn.execute("UPDATE subscriptions SET reminded = 1 WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))

def get_due_expirations(now: Optional[int] = None, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    now = now or int(time.time())
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT s.*, c.owner_id, c.linked_group_id FROM subscriptions s JOIN channels c ON s.chat_id = c.chat_id WHERE s.status = 'active' AND s.expires_at <= ?",
            (now,)
        ).fetchall()
        return [dict(r) for r in rows]

def mark_subscription_expired(user_id: int, chat_id: int, db_path: Optional[str] = None) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE subscriptions SET status = 'expired' WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        )

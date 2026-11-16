import os
import config
import db
import gateways

def run_checks():
    test_db = "test_check_v8_ultimate.db"
    if os.path.exists(test_db):
        os.remove(test_db)

    db.init_db(test_db)

    orig_admin = os.environ.get("PLATFORM_ADMIN_ID", "")
    os.environ["PLATFORM_ADMIN_ID"] = "0"
    assert config.is_platform_admin(0) is False
    assert config.is_platform_admin(123456) is False
    if orig_admin:
        os.environ["PLATFORM_ADMIN_ID"] = orig_admin

    assert db.upsert_channel(-100999, 111, "کانال اصلی VIP", test_db) is True
    assert db.upsert_channel(-100999, 222, "کانال هکر", test_db) is False
    assert db.set_channel_linked_group(-100999, 111, -100888, "گروه پرسش و پاسخ VIP", test_db) is True

    p1 = db.create_plan(-100999, "اشتراک ۳ ماهه الماسی", 90, price_toman=8000000, original_price_toman=10000000, db_path=test_db)
    plan = db.get_plan(p1, test_db)
    assert plan["original_price_toman"] == 10000000
    assert plan["price_toman"] == 8000000
    assert plan["discount_pct"] == 20

    db.create_payment("ORD-PDF-1", 333, -100999, p1, 8000000, "card", db_path=test_db)
    db.mark_payment_submitted("ORD-PDF-1", "رسید بانکی", "", "DOC_PDF_ID_1", test_db)
    db.delete_plan(p1, 111, test_db)
    assert len(db.get_channel_plans(-100999, test_db)) == 0
    pending = db.get_submitted_receipts_for_channel(-100999, test_db)
    assert len(pending) == 1 and pending[0]["receipt_doc"] == "DOC_PDF_ID_1"

    paid = db.mark_payment_paid("ORD-PDF-1", "MANUAL-OK", expected_owner_id=111, db_path=test_db)
    assert paid is not None
    assert db.is_subscription_active(333, -100999, test_db) is True
    assert db.is_subscription_active(333, -100888, test_db) is True

    assert db.create_promo_code(-100999, "VIP25", 25, 10, test_db) is True
    pr = db.validate_promo_code(-100999, 444, "vip25", test_db)
    assert pr is not None and pr["discount_pct"] == 25

    wallets = gateways.extract_tron_wallets("TRC20: (T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb)")
    assert wallets == ["T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb"]
    tx = "b" * 64
    assert db.claim_txid_atomically(tx, 333, test_db) is True
    assert db.claim_txid_atomically(tx, 444, test_db) is False

    os.remove(test_db)
    print("ALL V8 ULTIMATE SELF-CHECKS PASSED 100%.")

if __name__ == "__main__":
    run_checks()

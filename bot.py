import os
import asyncio
import html
import time
import uuid
from contextlib import suppress
from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramUnauthorizedError
from aiogram.types import (
    Message, CallbackQuery, ChatJoinRequest, PreCheckoutQuery, LabeledPrice,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, FSInputFile
)
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import (
    settings, SAAS_TIERS, get_usd_toman_rate, get_star_toman_rate,
    set_env_key, is_platform_admin, get_platform_admin_id
)
import db
import gateways

bot = Bot(token=settings.bot_token)
dp = Dispatcher()
router = Router()
dp.include_router(router)

PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

def parse_clean_int(text: str) -> int:
    cleaned = (text or "").translate(PERSIAN_DIGITS)
    for ch in (",", " ", "_", "،", "تومان", "روز", "ستاره", "%", "درصد"):
        cleaned = cleaned.replace(ch, "")
    return int(cleaned.strip())

async def safe_edit(message: Message, text: str, reply_markup=None):
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            return
        await message.answer(text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)

async def get_viral_footer(owner_id: int, current_bot: Bot) -> str:
    qs = db.get_admin_quota_status(owner_id)
    if not qs["has_branding"]:
        return ""
    me = await current_bot.get_me()
    ref_link = f"https://t.me/{me.username}?start=ref_{owner_id}"
    return f"\n\n━━━━━━━━━━━━━━\n⚡️ مدیریت خودکار اشتراک با <a href='{ref_link}'>ربات سازنده VIP (رایگان بسازید)</a>"

async def start_whitelabel_bots() -> list[Bot]:
    extra = []
    for tok in db.get_all_whitelabel_tokens():
        if tok == settings.bot_token:
            continue
        try:
            b = Bot(token=tok)
            await b.get_me()
            extra.append(b)
        except Exception:
            pass
    return extra

class AdminFlow(StatesGroup):
    plan_name = State()
    plan_days = State()
    plan_orig_price = State()
    plan_sell_price = State()
    edit_plan_orig = State()
    edit_plan_sell = State()
    edit_card = State()
    edit_crypto = State()
    edit_support = State()
    link_group = State()
    promo_code = State()
    promo_pct = State()
    promo_uses = State()
    waiting_platform_receipt = State()
    edit_usd_rate = State()
    gift_user_id = State()
    gift_days = State()
    broadcast_msg = State()
    payout_dest = State()
    superadmin_grant = State()
    whitelabel_token = State()

class BuyerFlow(StatesGroup):
    waiting_receipt = State()
    waiting_promo = State()

BTN_BUY_VIP = "🌟 خرید اشتراک VIP"
BTN_MY_SUBS = "👤 اشتراک‌های فعال من"
BTN_CREATOR = "🚀 ساخت پنل فروش VIP"

BTN_ADD_PLAN = "➕ افزودن پلن اشتراک"
BTN_LIST_PLANS = "📋 مدیریت و ویرایش پلن‌ها"
BTN_PAY_SETTINGS = "💳 تنظیمات پرداخت و کانال"
BTN_STATS = "📊 آمار و ابزارهای کانال"
BTN_SAAS_TIER = "🚀 ارتقای پلن ربات (ظرفیت)"
BTN_AFFILIATE = "🎁 همکاری در فروش (۳۰٪ دلاری)"

ALL_MENU_BUTTONS = {
    BTN_BUY_VIP, BTN_MY_SUBS, BTN_CREATOR,
    BTN_ADD_PLAN, BTN_LIST_PLANS, BTN_PAY_SETTINGS, BTN_STATS,
    BTN_SAAS_TIER, BTN_AFFILIATE
}

def bottom_menu_kb(user_id: int) -> ReplyKeyboardMarkup:
    owned = db.get_owner_channels(user_id)
    if owned:
        kb = [
            [KeyboardButton(text=BTN_ADD_PLAN), KeyboardButton(text=BTN_LIST_PLANS)],
            [KeyboardButton(text=BTN_PAY_SETTINGS), KeyboardButton(text=BTN_STATS)],
            [KeyboardButton(text=BTN_SAAS_TIER), KeyboardButton(text=BTN_AFFILIATE)],
            [KeyboardButton(text=BTN_BUY_VIP), KeyboardButton(text=BTN_MY_SUBS)]
        ]
    else:
        kb = [
            [KeyboardButton(text=BTN_BUY_VIP), KeyboardButton(text=BTN_MY_SUBS)],
            [KeyboardButton(text=BTN_CREATOR), KeyboardButton(text=BTN_AFFILIATE)]
        ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def cancel_kb(target_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ انصراف و بازگشت", callback_data=target_cb)]
    ])

def channel_admin_kb(chat_id: int) -> InlineKeyboardMarkup:
    st = db.get_channel_stats(chat_id)
    pending_cnt = st["pending_manual"]
    pending_label = f"⏳ بررسی فیش‌های منتظر تایید ({pending_cnt} مورد)" if pending_cnt > 0 else "⏳ فیش‌های منتظر تایید (۰)"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=BTN_ADD_PLAN, callback_data=f"wiz_plan:{chat_id}"), InlineKeyboardButton(text=BTN_LIST_PLANS, callback_data=f"list_plans:{chat_id}")],
        [InlineKeyboardButton(text=pending_label, callback_data=f"pending_list:{chat_id}")],
        [InlineKeyboardButton(text="🎟 کدهای تخفیف", callback_data=f"promo_hub:{chat_id}"), InlineKeyboardButton(text="🎁 اهدای اشتراک دستی", callback_data=f"gift_sub:{chat_id}")],
        [InlineKeyboardButton(text="📣 پیام همگانی به اعضا", callback_data=f"bcast_menu:{chat_id}"), InlineKeyboardButton(text=BTN_PAY_SETTINGS, callback_data=f"pay_hub:{chat_id}")],
        [InlineKeyboardButton(text="📊 بروزرسانی آمار", callback_data=f"stats:{chat_id}"), InlineKeyboardButton(text=BTN_SAAS_TIER, callback_data="saas_hub")],
        [InlineKeyboardButton(text="🏠 بازگشت به صفحه اصلی", callback_data="home")]
    ])

def pay_settings_kb(ch: dict) -> InlineKeyboardMarkup:
    cid = ch["chat_id"]
    stars_on = bool(ch.get("stars_enabled", 1))
    pub_on = bool(ch.get("is_public", 1))
    stars_label = "⭐️ استارز تلگرام: فعال (جهت غیرفعال‌سازی بزنید)" if stars_on else "⭐️ استارز تلگرام: غیرفعال (جهت فعال‌سازی بزنید)"
    pub_label = "👁 نمایش در ویترین عمومی: فعال" if pub_on else "🔒 نمایش در ویترین عمومی: مخفی (ویژه Pro/VIP)"
    grp_label = f"👥 گروه متصل: {ch['linked_group_title']}" if ch.get("linked_group_id") else "👥 اتصال گروه گفتگو به کانال VIP"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 تنظیم / ویرایش کارت‌به‌کارت", callback_data=f"set_card:{cid}")],
        [InlineKeyboardButton(text="💎 تنظیم / ویرایش کیف پول کریپتو (USDT TRC20)", callback_data=f"set_crypto:{cid}")],
        [InlineKeyboardButton(text=stars_label, callback_data=f"toggle_stars:{cid}")],
        [InlineKeyboardButton(text=grp_label, callback_data=f"link_grp:{cid}")],
        [InlineKeyboardButton(text="📞 تنظیم آیدی پشتیبانی کانال", callback_data=f"set_sup:{cid}")],
        [InlineKeyboardButton(text=pub_label, callback_data=f"toggle_pub:{cid}")],
        [InlineKeyboardButton(text="🔙 بازگشت به پنل کانال", callback_data=f"manage:{cid}")]
    ])

async def render_saas_hub(user_id: int, target: Message, edit: bool = False):
    qs = db.get_admin_quota_status(user_id)
    exp_str = "دائمی (پلن رایگان)"
    if qs["tier"] != "free" and qs["tier_expires_at"] > int(time.time()):
        rem_d = max(1, (qs["tier_expires_at"] - int(time.time())) // 86400)
        exp_str = f"{rem_d} روز باقی‌مانده"

    quota_label = "نامحدود (∞)" if qs["quota"] > 100000 else f"{qs['quota']} سفارش در ماه"
    branding_str = "دارد (حذف خودکار در پلن Pro و VIP)" if qs["has_branding"] else "✅ حذف شده (بدون تبلیغ)"
    usd_rate = get_usd_toman_rate()
    g, p, v = SAAS_TIERS["growth"], SAAS_TIERS["pro"], SAAS_TIERS["vip"]

    text = (
        f"🚀 <b>مرکز ارتقای پلن و افزایش ظرفیت ربات (SaaS Billing)</b>\n\n"
        f"🏷 <b>پلن فعلی شما:</b> {qs['tier_title']}\n"
        f"⏳ <b>اعتبار زمانی:</b> {exp_str}\n"
        f"📊 <b>سفارش‌های مصرف‌شده این ماه:</b> <b>{qs['used']}</b> از <b>{quota_label}</b>\n"
        f"📢 <b>وضعیت برندینگ:</b> {branding_str}\n"
        f"👛 <b>موجودی کیف پول پورسانت:</b> <code>${qs['wallet_balance_usd']:.2f}</code>\n"
        f"💱 <b>نرخ روز دلار:</b> <code>{usd_rate:,} تومان</code>\n"
        f"━━━━━━━━━━━━━━\n"
        f"🟢 <b>۱. Starter (رایگان):</b> تا ۱۰ سفارش/ماه | قیمت‌گذاری با تخفیف\n"
        f"📈 <b>۲. Growth ($5 | {g['price_toman']:,} ت):</b> تا ۵۰ سفارش + پیام همگانی + کد تخفیف + گروه هم‌زمان\n"
        f"💎 <b>۳. Pro ($12 | {p['price_toman']:,} ت):</b> تا ۲۰۰ سفارش + <b>حذف کامل تبلیغ ربات</b> + مخفی‌سازی کانال\n"
        f"👑 <b>۴. VIP White-Label ($29 | {v['price_toman']:,} ت):</b> <b>نامحدود (∞) + اتصال توکن ربات اختصاصی شما</b>\n\n"
        f"👇 <b>جهت مشاهده جزئیات کامل و خرید، روی پلن مورد نظر کلیک کنید:</b>"
    )
    rows = [
        [InlineKeyboardButton(text=f"📈 انتخاب پلن Growth ($5 | {g['price_toman']:,} ت)", callback_data="saas_buy:growth")],
        [InlineKeyboardButton(text=f"💎 انتخاب پلن Pro ($12 | {p['price_toman']:,} ت + حذف تبلیغ)", callback_data="saas_buy:pro")],
        [InlineKeyboardButton(text=f"👑 انتخاب پلن VIP ($29 | {v['price_toman']:,} ت + نامحدود)", callback_data="saas_buy:vip")],
    ]
    if qs["tier"] == "vip":
        rows.append([InlineKeyboardButton(text="🤖 تنظیم توکن ربات اختصاصی (White-Label)", callback_data="set_wl_token")])
    rows.append([InlineKeyboardButton(text="🎁 کسب درآمد دلاری (معرفی به سایر ادمین‌ها)", callback_data="aff_hub")])
    if is_platform_admin(user_id):
        rows.append([InlineKeyboardButton(text=f"💱 تغییر نرخ دلار روز ({usd_rate:,} ت)", callback_data="set_usd_rate")])
    rows.append([InlineKeyboardButton(text="🏠 بازگشت به صفحه اصلی", callback_data="home")])

    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    if edit:
        await safe_edit(target, text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "set_wl_token")
async def cb_set_wl_token(call: CallbackQuery, state: FSMContext):
    qs = db.get_admin_quota_status(call.from_user.id)
    if qs["tier"] != "vip":
        await call.answer("این قابلیت ویژه پلن VIP White-Label ($29) است.", show_alert=True)
        return
    await call.answer()
    await state.set_state(AdminFlow.whitelabel_token)
    await safe_edit(
        call.message,
        "🤖 <b>اتصال توکن ربات اختصاصی (White-Label)</b>\n\n"
        "توکن رباتی که در <code>@BotFather</code> ساخته‌اید را ارسال کنید تا ربات با نام، لوگو و یوزرنیم اختصاصی شما فعال شود:\n"
        "(پس از ثبت توکن، با ری‌استارت بعدی سرور ربات اختصاصی شما به صورت خودکار آنلاین می‌شود)",
        reply_markup=cancel_kb("saas_hub")
    )

@router.message(StateFilter(AdminFlow.whitelabel_token))
async def step_save_wl_token(message: Message, state: FSMContext):
    tok = (message.text or "").strip()
    if ":" not in tok or len(tok) < 30:
        await message.answer("فرمت توکن BotFather نامعتبر است. دوباره ارسال کنید:", reply_markup=cancel_kb("saas_hub"))
        return
    try:
        test_bot = Bot(token=tok)
        info = await test_bot.get_me()
        await test_bot.session.close()
    except TelegramUnauthorizedError:
        await message.answer("❌ این توکن توسط تلگرام تایید نشد (نامعتبر است). لطفاً توکن صحیح را بفرستید:", reply_markup=cancel_kb("saas_hub"))
        return
    except Exception:
        await message.answer("خطا در بررسی توکن. دوباره تلاش کنید:", reply_markup=cancel_kb("saas_hub"))
        return

    db.set_admin_whitelabel_token(message.from_user.id, tok)
    await state.clear()
    await message.answer(
        f"✅ توکن ربات اختصاصی <b>@{html.escape(info.username)}</b> با موفقیت ثبت شد!\n"
        f"ربات اختصاصی شما در چرخه سرویس قرار گرفت.",
        parse_mode="HTML"
    )
    await render_saas_hub(message.from_user.id, message, edit=False)

async def render_affiliate_hub(user_id: int, target: Message, edit: bool = False):
    st = db.get_affiliate_stats(user_id)
    me = await target.bot.get_me()
    ref_link = f"https://t.me/{me.username}?start=ref_{user_id}"
    text = (
        f"🎁 <b>سیستم همکاری در فروش و پاداش دلاری (۳۰٪ پورسانت تکرارشونده)</b>\n\n"
        f"با معرفی این ربات به مدیران یا پشتیبان‌های سایر کانال‌های VIP، در تمام خریدها و تمدیدهای ماهانه آن‌ها شریک شوید:\n"
        f"• <b>پلن Growth ($5):</b> <code>$1.50</code> پاداش ماهانه برای شما\n"
        f"• <b>پلن Pro ($12):</b> <code>$3.60</code> پاداش ماهانه برای شما\n"
        f"• <b>پلن VIP ($29):</b> <code>$8.70</code> پاداش ماهانه برای شما\n\n"
        f"📊 <b>آمار شما تا این لحظه:</b>\n"
        f"👥 ادمین‌های دعوت‌شده با لینک شما: <b>{st['invited_count']} نفر</b>\n"
        f"💎 ادمین‌های دارای پلن پولی: <b>{st['paid_referrals']} نفر</b>\n"
        f"💵 مجموع پاداش کسب‌شده: <b>${st['total_earned_usd']:.2f}</b>\n"
        f"👛 موجودی قابل استفاده / برداشت: <b>${st['wallet_balance_usd']:.2f}</b>\n\n"
        f"🔗 <b>لینک اختصاصی دعوت شما (با یک لمس کپی می‌شود):</b>\n"
        f"<code>{ref_link}</code>"
    )
    rows = [
        [InlineKeyboardButton(text="🚀 استفاده از موجودی برای ارتقای پلن خودم", callback_data="saas_hub")],
        [InlineKeyboardButton(text="💸 درخواست تسویه و برداشت نقدی پورسانت (حداقل $5)", callback_data="req_payout")],
        [InlineKeyboardButton(text="🏠 بازگشت به صفحه اصلی", callback_data="home")]
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    if edit:
        await safe_edit(target, text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")

async def render_pay_hub(target: Message, chat_id: int, edit: bool = False):
    ch = db.get_channel(chat_id)
    if not ch:
        return
    card_txt = f"<code>{html.escape(ch['card_number'])}</code>" if ch.get("card_number") else "❌ تنظیم نشده"
    crypto_txt = f"<code>{html.escape(ch['crypto_wallet'])}</code>" if ch.get("crypto_wallet") else "❌ تنظیم نشده"
    stars_txt = "✅ فعال (پرداخت درون‌برنامه‌ای تلگرام)" if ch.get("stars_enabled", 1) else "❌ غیرفعال"
    grp_txt = f"✅ {html.escape(ch['linked_group_title'])}" if ch.get("linked_group_id") else "❌ متصل نشده"
    sup_txt = html.escape(ch.get("support_handle") or "ثبت نشده")
    pub_txt = "عمومی (نمایش در صفحه اول ربات)" if ch.get("is_public", 1) else "خصوصی (فقط قابل ورود با لینک اختصاصی)"

    text = (
        f"💳 <b>تنظیمات پرداخت و کانال «{html.escape(ch['title'])}»</b>\n\n"
        f"۱. <b>کارت‌به‌کارت:</b> {card_txt}\n"
        f"۲. <b>پرداخت کریپتو (USDT TRC20):</b> {crypto_txt}\n"
        f"۳. <b>پرداخت با Telegram Stars:</b> {stars_txt}\n"
        f"۴. <b>گروه گفتگوی متصل به کانال:</b> {grp_txt}\n"
        f"۵. <b>آیدی پشتیبانی کانال:</b> {sup_txt}\n"
        f"۶. <b>وضعیت نمایش کانال:</b> {pub_txt}\n\n"
        f"👇 هر بخش را به صورت کاملاً مجزا تنظیم یا ویرایش کنید:"
    )
    if edit:
        await safe_edit(target, text, reply_markup=pay_settings_kb(ch))
    else:
        await target.answer(text, reply_markup=pay_settings_kb(ch), parse_mode="HTML")

async def resolve_admin_channel(message: Message, action_prefix: str) -> int | None:
    channels = db.get_owner_channels(message.from_user.id)
    if not channels:
        await message.answer(
            "شما هنوز کانالی ثبت نکرده‌اید. ابتدا ربات را در کانال خصوصی خود Admin کنید و یک پست از آن به اینجا Forward کنید.",
            reply_markup=bottom_menu_kb(message.from_user.id)
        )
        return None
    if len(channels) == 1:
        return channels[0]["chat_id"]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📢 {c['title']}", callback_data=f"{action_prefix}:{c['chat_id']}")]
        for c in channels
    ])
    await message.answer("کدام کانال را می‌خواهید مدیریت کنید؟", reply_markup=kb)
    return None

def format_plan_line(p: dict) -> str:
    if p.get("discount_pct", 0) > 0:
        return (
            f"🔥 <b>{html.escape(p['name'])}</b> ({p['duration_days']} روز) — <b>{p['discount_pct']}٪ تخفیف!</b>\n"
            f"   💰 <s>{p['original_price_toman']:,}</s> ➔ <b>{p['price_toman']:,} تومان</b> "
            f"(💎 {p['price_usdt']:g} USDT | ⭐️ {p['price_stars']})"
        )
    return (
        f"• <b>{html.escape(p['name'])}</b> ({p['duration_days']} روز) : "
        f"<b>{p['price_toman']:,} تومان</b> (💎 {p['price_usdt']:g} USDT | ⭐️ {p['price_stars']})"
    )

async def render_channel_store(target: Message, chat_id: int, edit: bool = False):
    ch = db.get_channel(chat_id)
    plans = db.get_channel_plans(chat_id)
    if not ch or not plans:
        txt = "هنوز پلن اشتراکی برای این کانال تعریف نشده است."
        if edit:
            await safe_edit(target, txt)
        else:
            await target.answer(txt)
        return
    rows = []
    plan_descriptions = []
    for p in plans:
        plan_descriptions.append(format_plan_line(p))
        if p.get("discount_pct", 0) > 0:
            btn_txt = f"🔥 {p['name']} ({p['duration_days']} روز) — {p['discount_pct']}٪ تخفیف: {p['price_toman']:,} ت"
        else:
            btn_txt = f"💎 {p['name']} ({p['duration_days']} روز) : {p['price_toman']:,} ت"
        rows.append([InlineKeyboardButton(text=btn_txt, callback_data=f"buy:{p['id']}")])
    rows.append([InlineKeyboardButton(text=BTN_MY_SUBS, callback_data="my_subs")])

    grp_line = f"\n👥 به همراه عضویت هم‌زمان در گروه «<b>{html.escape(ch['linked_group_title'])}</b>»" if ch.get("linked_group_id") else ""
    sup_line = f"\n📞 پشتیبانی کانال: {html.escape(ch['support_handle'])}" if ch.get("support_handle") else ""
    footer = await get_viral_footer(ch["owner_id"], target.bot)

    text = (
        f"🌟 <b>عضویت ویژه در کانال «{html.escape(ch['title'])}»</b>\n\n"
        f"✅ دریافت آنی لینک ورود اختصاصی یک‌بارمصرف پس از پرداخت{grp_line}{sup_line}\n"
        f"━━━━━━━━━━━━━━\n"
        + "\n\n".join(plan_descriptions)
        + f"\n━━━━━━━━━━━━━━\n"
        f"👇 روی دکمه پلن مورد نظر خود کلیک کنید:{footer}"
    )
    if edit:
        await safe_edit(target, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    else:
        await target.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML", disable_web_page_preview=True)

async def render_home(user_id: int, target: Message, edit: bool = False):
    owned = db.get_owner_channels(user_id)
    public_chs = db.get_public_channels()
    rows = []

    for c in public_chs:
        rows.append([InlineKeyboardButton(text=f"🌟 خرید اشتراک «{c['title']}»", callback_data=f"store:{c['chat_id']}")])

    rows.append([InlineKeyboardButton(text="👤 اشتراک‌های فعال من (تمدید / دریافت لینک)", callback_data="my_subs")])

    if owned:
        qs = db.get_admin_quota_status(user_id)
        for c in owned:
            rows.append([InlineKeyboardButton(text=f"⚙️ پنل مدیریت «{c['title']}»", callback_data=f"manage:{c['chat_id']}")])
        rows.append([
            InlineKeyboardButton(text=f"🚀 پلن من: {qs['tier'].upper()} ({qs['used']}/{qs['quota'] if qs['quota']<100000 else '∞'})", callback_data="saas_hub"),
            InlineKeyboardButton(text="🎁 همکاری در فروش (۳۰٪)", callback_data="aff_hub")
        ])
        rows.append([InlineKeyboardButton(text="➕ اتصال کانال VIP جدید", callback_data="creator_guide")])
        text = (
            "👋 <b>به پیشخوان مدیریت و فروشگاه VIP خوش آمدید!</b>\n\n"
            "از دکمه‌های شیشه‌ای زیر یا <b>کیبورد پایین صفحه</b> می‌توانید کانال‌های خود را مدیریت کنید یا اشتراک تهیه نمایید:"
        )
    else:
        rows.append([InlineKeyboardButton(text="🚀 صاحب کانال هستید؟ ساخت پنل فروش VIP (رایگان)", callback_data="creator_guide")])
        rows.append([InlineKeyboardButton(text="🎁 کسب درآمد دلاری با معرفی به ادمین‌ها (۳۰٪ پورسانت)", callback_data="aff_hub")])
        text = (
            "👋 <b>خوش آمدید!</b>\n\n"
            "برای تهیه اشتراک ویژه، بررسی وضعیت عضویت یا ساخت ربات فروش برای کانال خود، از گزینه‌های زیر استفاده کنید:"
        )

    if is_platform_admin(user_id):
        rows.append([InlineKeyboardButton(text="👑 پنل سوپرادمین پلتفرم (/admin)", callback_data="super_admin")])

    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    if edit:
        await safe_edit(target, text, reply_markup=kb)
    else:
        await target.answer("منوی دسترسی سریع در پایین صفحه فعال شد 👇", reply_markup=bottom_menu_kb(user_id))
        await target.answer(text, reply_markup=kb, parse_mode="HTML")

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    db.cancel_abandoned_initiations(message.from_user.id)
    await message.answer("✅ عملیات جاری لغو شد.", reply_markup=bottom_menu_kb(message.from_user.id))

@router.message(Command("myid"))
async def cmd_myid(message: Message):
    await message.answer(
        f"🆔 آیدی عددی اکانت تلگرام شما:\n<code>{message.from_user.id}</code>\n\n"
        f"💡 برای فعال کردن دسترسی سوپرادمین پلتفرم، این عدد را در فایل <code>.env</code> جلوی <code>PLATFORM_ADMIN_ID=</code> قرار دهید و ربات را ری‌استارت کنید.",
        parse_mode="HTML"
    )

@router.message(Command("admin"))
async def cmd_super_admin(message: Message, state: FSMContext):
    if not is_platform_admin(message.from_user.id):
        return
    await state.clear()
    await render_super_admin(message, edit=False)

@router.callback_query(F.data == "super_admin")
async def cb_super_admin(call: CallbackQuery, state: FSMContext):
    if not is_platform_admin(call.from_user.id):
        await call.answer("عدم دسترسی.", show_alert=True)
        return
    await state.clear()
    await call.answer()
    await render_super_admin(call.message, edit=True)

async def render_super_admin(target: Message, edit: bool = False):
    st = db.get_superadmin_stats()
    usd_rate = get_usd_toman_rate()
    text = (
        f"👑 <b>داشبورد سوپرادمین کل پلتفرم</b>\n\n"
        f"👥 <b>کل ادمین‌های ثبت‌شده:</b> {st['total_admins']} نفر\n"
        f"  ├ 🟢 پلن Free: <b>{st['free_admins']}</b>\n"
        f"  ├ 📈 پلن Growth ($5): <b>{st['growth_admins']}</b>\n"
        f"  ├ 💎 پلن Pro ($12): <b>{st['pro_admins']}</b>\n"
        f"  └ 👑 پلن VIP ($29): <b>{st['vip_admins']}</b>\n\n"
        f"📢 <b>کل کانال‌های متصل:</b> {st['total_channels']} کانال\n"
        f"👤 <b>کل اشتراک‌های فعال کاربران:</b> {st['total_subs']} اشتراک\n"
        f"💵 <b>مجموع فروش پلن‌های پلتفرم:</b> ${st['plat_usd']:.2f}\n"
        f"💱 <b>نرخ روز دلار در سیستم:</b> <code>{usd_rate:,} تومان</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💱 تغییر نرخ دلار روز ({usd_rate:,} ت)", callback_data="set_usd_rate")],
        [InlineKeyboardButton(text="🎁 ارتقای دستی پلن یک ادمین (هدیه/تست)", callback_data="sa_grant_start")],
        [InlineKeyboardButton(text="💾 دریافت فوری بکاپ دیتابیس", callback_data="sa_backup_now")],
        [InlineKeyboardButton(text="🏠 بازگشت به صفحه اصلی", callback_data="home")]
    ])
    if edit:
        await safe_edit(target, text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "sa_backup_now")
async def cb_sa_backup_now(call: CallbackQuery):
    if not is_platform_admin(call.from_user.id):
        await call.answer("عدم دسترسی.", show_alert=True)
        return
    await call.answer("در حال تهیه بکاپ...")
    b_path = f"backup_manual_{int(time.time())}.db"
    try:
        db.backup_database_to_file(b_path)
        await call.message.answer_document(FSInputFile(b_path), caption="💾 بکاپ کامل دیتابیس SQLite پلتفرم")
    finally:
        if os.path.exists(b_path):
            os.remove(b_path)

@router.callback_query(F.data == "sa_grant_start")
async def cb_sa_grant_start(call: CallbackQuery, state: FSMContext):
    if not is_platform_admin(call.from_user.id):
        await call.answer("عدم دسترسی.", show_alert=True)
        return
    await call.answer()
    await state.set_state(AdminFlow.superadmin_grant)
    await safe_edit(
        call.message,
        "🎁 <b>ارتقای دستی پلن ادمین</b>\n\n"
        "آیدی عددی ادمین و نام پلن (`free`, `growth`, `pro`, `vip`) را با یک فاصله ارسال کنید:\n"
        "مثال:\n<code>123456789 pro</code>",
        reply_markup=cancel_kb("super_admin")
    )

@router.message(StateFilter(AdminFlow.superadmin_grant))
async def step_sa_grant_save(message: Message, state: FSMContext):
    if not is_platform_admin(message.from_user.id):
        await state.clear()
        return
    parts = (message.text or "").strip().split()
    if len(parts) < 2 or parts[1].lower() not in SAAS_TIERS:
        await message.answer("فرمت نادرست است. مثال: <code>123456789 pro</code>", parse_mode="HTML", reply_markup=cancel_kb("super_admin"))
        return
    try:
        target_uid = parse_clean_int(parts[0])
    except ValueError:
        await message.answer("آیدی عددی نامعتبر است.", reply_markup=cancel_kb("super_admin"))
        return
    tier_key = parts[1].lower()
    db.set_admin_tier_manually(target_uid, tier_key, 30)
    await state.clear()
    with suppress(Exception):
        await message.bot.send_message(target_uid, f"🎉 پلن اکانت شما توسط مدیریت پلتفرم به <b>{SAAS_TIERS[tier_key]['title']}</b> ارتقا یافت!", parse_mode="HTML")
    await message.answer(f"✅ پلن کاربر <code>{target_uid}</code> به <b>{tier_key.upper()}</b> تغییر یافت.", parse_mode="HTML")
    await render_super_admin(message, edit=False)

@router.message(Command("setrate"))
async def cmd_setrate(message: Message):
    if not is_platform_admin(message.from_user.id):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(f"نرخ فعلی دلار: <code>{get_usd_toman_rate():,}</code> تومان\nفرمت تغییر: <code>/setrate 270000</code>", parse_mode="HTML")
        return
    try:
        new_rate = parse_clean_int(parts[1])
        if new_rate < 1000:
            raise ValueError
    except ValueError:
        await message.answer("لطفاً یک عدد معتبر به تومان وارد کنید (مثلاً 270000).")
        return
    set_env_key("USD_TOMAN_RATE", str(new_rate))
    await message.answer(f"✅ نرخ دلار در فایل <code>.env</code> به <b>{new_rate:,} تومان</b> بروزرسانی شد.", parse_mode="HTML")

@router.callback_query(F.data == "set_usd_rate")
async def cb_set_usd_rate(call: CallbackQuery, state: FSMContext):
    if not is_platform_admin(call.from_user.id):
        await call.answer("عدم دسترسی.", show_alert=True)
        return
    await call.answer()
    await state.set_state(AdminFlow.edit_usd_rate)
    await safe_edit(
        call.message,
        f"💱 <b>تنظیم نرخ روز دلار (تومان)</b>\n\n"
        f"نرخ فعلی در فایل <code>.env</code>: <b>{get_usd_toman_rate():,} تومان</b>\n"
        f"لطفاً قیمت جدید هر ۱ دلار را به تومان تایپ و ارسال کنید (مثلاً <code>270000</code>):",
        reply_markup=cancel_kb("saas_hub")
    )

@router.message(StateFilter(AdminFlow.edit_usd_rate))
async def step_save_usd_rate(message: Message, state: FSMContext):
    if not is_platform_admin(message.from_user.id):
        await state.clear()
        return
    try:
        new_rate = parse_clean_int(message.text or "")
        if new_rate < 1000:
            raise ValueError
    except ValueError:
        await message.answer("لطفاً یک عدد معتبر بزرگ‌تر از ۱۰۰۰ وارد کنید (مثلاً 270000):", reply_markup=cancel_kb("saas_hub"))
        return
    set_env_key("USD_TOMAN_RATE", str(new_rate))
    await state.clear()
    await message.answer(f"✅ نرخ روز دلار به <b>{new_rate:,} تومان</b> تغییر یافت و قیمت تمام پلن‌ها بروز شد!", parse_mode="HTML")
    await render_saas_hub(message.from_user.id, message, edit=False)

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    db.cancel_abandoned_initiations(message.from_user.id)
    args = (message.text or "").split(maxsplit=1)
    if len(args) > 1:
        param = args[1].strip()
        if param.startswith("ref_"):
            try:
                ref_id = int(param.replace("ref_", ""))
                db.ensure_admin_account(message.from_user.id, referred_by=ref_id)
            except ValueError:
                pass
            await message.answer(
                "🎉 <b>خوش آمدید!</b> شما با لینک دعوت ویژه وارد شدید.\n"
                "تا <b>۱۰ سفارش اول در هر ماه کاملاً رایگان</b> است. همین الان کانال VIP خود را در ۳۰ ثانیه متصل کنید:",
                reply_markup=bottom_menu_kb(message.from_user.id),
                parse_mode="HTML"
            )
            await render_home(message.from_user.id, message, edit=False)
            return

        if param.startswith("ch_"):
            try:
                chat_id = int(param.replace("ch_", ""))
            except ValueError:
                await message.answer("لینک کانال نامعتبر است.", reply_markup=bottom_menu_kb(message.from_user.id))
                return
            await message.answer("به فروشگاه کانال خوش آمدید 👇", reply_markup=bottom_menu_kb(message.from_user.id))
            await render_channel_store(message, chat_id, edit=False)
            return

    db.ensure_admin_account(message.from_user.id)
    await render_home(message.from_user.id, message, edit=False)

@router.message(F.text == BTN_BUY_VIP)
async def msg_buy_vip(message: Message, state: FSMContext):
    await state.clear()
    db.cancel_abandoned_initiations(message.from_user.id)
    public_chs = db.get_public_channels()
    if not public_chs:
        await message.answer("هنوز هیچ کانال عمومی فعالی ثبت نشده است.", reply_markup=bottom_menu_kb(message.from_user.id))
        return
    if len(public_chs) == 1:
        await render_channel_store(message, public_chs[0]["chat_id"], edit=False)
        return
    rows = [
        [InlineKeyboardButton(text=f"🌟 {c['title']}", callback_data=f"store:{c['chat_id']}")]
        for c in public_chs
    ]
    await message.answer("کانال مورد نظر خود را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

@router.message(F.text == BTN_MY_SUBS)
async def msg_my_subs(message: Message, state: FSMContext):
    await state.clear()
    db.cancel_abandoned_initiations(message.from_user.id)
    subs = db.get_user_subscriptions(message.from_user.id)
    submitted = db.get_user_submitted_receipts(message.from_user.id)
    now = int(time.time())
    if not subs and not submitted:
        await message.answer("شما در حال حاضر هیچ اشتراک فعالی ندارید.", reply_markup=bottom_menu_kb(message.from_user.id))
        return
    lines = []
    kb_rows = []
    for s in subs:
        rem_days = max(1, (s["expires_at"] - now) // 86400)
        sup = f" | پشتیبانی: {html.escape(s['support_handle'])}" if s.get("support_handle") else ""
        lines.append(f"✅ <b>{html.escape(s['channel_title'])}</b>: {rem_days} روز باقی‌مانده{sup}")
        kb_rows.append([
            InlineKeyboardButton(text=f"🔗 لینک ورود «{s['channel_title']}»", callback_data=f"relink:{s['chat_id']}"),
            InlineKeyboardButton(text=f"🔄 تمدید", callback_data=f"renew_ch:{s['chat_id']}")
        ])
    for sub_rec in submitted:
        lines.append(f"⏳ <b>{html.escape(sub_rec['channel_title'])}</b>: رسید شما ثبت شده و در انتظار تایید مدیر کانال است (کد: <code>{sub_rec['authority']}</code>)")
    await message.answer(
        "👤 <b>وضعیت اشتراک‌ها و سفارش‌های شما:</b>\n\n" + "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows) if kb_rows else None,
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("relink:"))
async def cb_reissue_invite_link(call: CallbackQuery):
    chat_id = int(call.data.split(":")[1])
    if not db.is_subscription_active(call.from_user.id, chat_id):
        await call.answer("اشتراک شما در این کانال منقضی شده است.", show_alert=True)
        return
    await call.answer("لینک ورود جدید ساخته شد.")
    await issue_invite_link(call.from_user.id, chat_id, call.bot, count_quota=False)

@router.message(F.text == BTN_CREATOR)
async def msg_creator_guide(message: Message, state: FSMContext):
    await state.clear()
    db.cancel_abandoned_initiations(message.from_user.id)
    await message.answer(
        "🚀 <b>راه‌اندازی فروش خودکار برای کانال VIP شما (در ۳۰ ثانیه):</b>\n\n"
        "۱. این ربات را در کانال یا گروه خصوصی خود <b>Admin</b> کنید (دسترسی <code>Invite Users</code> و <code>Ban Users</code> فعال باشد).\n"
        "۲. یک پست دلخواه از داخل کانال خود به همین چت <b>Forward</b> کنید.\n\n"
        "🎁 <b>تا ۱۰ سفارش موفق در هر ماه کاملاً رایگان است!</b>",
        reply_markup=bottom_menu_kb(message.from_user.id),
        parse_mode="HTML"
    )

@router.message(F.text == BTN_SAAS_TIER)
async def msg_saas_hub(message: Message, state: FSMContext):
    await state.clear()
    db.cancel_abandoned_initiations(message.from_user.id)
    await render_saas_hub(message.from_user.id, message, edit=False)

@router.message(F.text == BTN_AFFILIATE)
async def msg_aff_hub(message: Message, state: FSMContext):
    await state.clear()
    db.cancel_abandoned_initiations(message.from_user.id)
    await render_affiliate_hub(message.from_user.id, message, edit=False)

@router.message(F.text == BTN_ADD_PLAN)
async def msg_add_plan(message: Message, state: FSMContext):
    await state.clear()
    db.cancel_abandoned_initiations(message.from_user.id)
    chat_id = await resolve_admin_channel(message, "wiz_plan")
    if chat_id:
        await state.update_data(chat_id=chat_id)
        await state.set_state(AdminFlow.plan_name)
        await message.answer(
            "۱/۴ <b>نام پلن اشتراک</b> را تایپ و ارسال کنید (مثلاً: اشتراک ۳ ماهه الماسی):",
            reply_markup=cancel_kb(f"manage:{chat_id}"),
            parse_mode="HTML"
        )

@router.message(F.text == BTN_LIST_PLANS)
async def msg_list_plans(message: Message, state: FSMContext):
    await state.clear()
    db.cancel_abandoned_initiations(message.from_user.id)
    chat_id = await resolve_admin_channel(message, "list_plans")
    if chat_id:
        await render_plans_manager(message, chat_id, edit=False)

@router.message(F.text == BTN_PAY_SETTINGS)
async def msg_pay_settings(message: Message, state: FSMContext):
    await state.clear()
    db.cancel_abandoned_initiations(message.from_user.id)
    chat_id = await resolve_admin_channel(message, "pay_hub")
    if chat_id:
        await render_pay_hub(message, chat_id, edit=False)

@router.message(F.text == BTN_STATS)
async def msg_stats(message: Message, state: FSMContext):
    await state.clear()
    db.cancel_abandoned_initiations(message.from_user.id)
    chat_id = await resolve_admin_channel(message, "stats")
    if chat_id:
        await render_channel_stats_view(message, message.from_user.id, chat_id, edit=False)

@router.callback_query(F.data == "saas_hub")
async def cb_saas_hub(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    await render_saas_hub(call.from_user.id, call.message, edit=True)

@router.callback_query(F.data == "aff_hub")
async def cb_aff_hub(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    await render_affiliate_hub(call.from_user.id, call.message, edit=True)

@router.callback_query(F.data == "req_payout")
async def cb_req_payout(call: CallbackQuery, state: FSMContext):
    st = db.get_affiliate_stats(call.from_user.id)
    if st["wallet_balance_usd"] < 5.0:
        await call.answer(f"حداقل موجودی برای برداشت نقدی ۵ دلار است (موجودی فعلی: ${st['wallet_balance_usd']:.2f}).", show_alert=True)
        return
    if get_platform_admin_id() <= 0:
        await call.answer("سیستم تسویه دستی هنوز توسط مدیر پلتفرم پیکربندی نشده است.", show_alert=True)
        return
    await call.answer()
    await state.set_state(AdminFlow.payout_dest)
    await safe_edit(
        call.message,
        f"💸 <b>درخواست تسویه پورسانت (${st['wallet_balance_usd']:.2f})</b>\n\n"
        f"لطفاً آدرس کیف پول تتر (TRC20) یا شماره کارت ۱۶ رقمی و نام صاحب حساب خود را جهت واریز ارسال کنید:",
        reply_markup=cancel_kb("aff_hub")
    )

@router.message(StateFilter(AdminFlow.payout_dest))
async def step_save_payout_req(message: Message, state: FSMContext):
    dest = (message.text or "").strip()
    if len(dest) < 8:
        await message.answer("لطفاً یک آدرس کیف پول یا شماره کارت معتبر وارد کنید:", reply_markup=cancel_kb("aff_hub"))
        return
    admin_id = get_platform_admin_id()
    if admin_id <= 0:
        await state.clear()
        return
    payout = db.create_payout_request(message.from_user.id, dest)
    await state.clear()
    if not payout:
        await message.answer("موجودی شما کمتر از ۵ دلار است.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ واریز انجام شد (تایید تسویه)", callback_data=f"pay_done:{payout['id']}")]
    ])
    with suppress(Exception):
        await bot.send_message(
            admin_id,
            f"💸 <b>درخواست تسویه پورسانت جدید (#{payout['id']})</b>\n\n"
            f"👤 کاربر: <code>{payout['owner_id']}</code>\n"
            f"💵 مبلغ: <b>${payout['amount_usd']:.2f}</b>\n"
            f"💳 مقصد واریز:\n<code>{html.escape(dest)}</code>",
            reply_markup=kb,
            parse_mode="HTML"
        )
    await message.answer("✅ درخواست تسویه شما ثبت و برای امور مالی ارسال شد.")
    await render_affiliate_hub(message.from_user.id, message, edit=False)

@router.callback_query(F.data.startswith("pay_done:"))
async def cb_mark_payout_done(call: CallbackQuery):
    if not is_platform_admin(call.from_user.id):
        await call.answer("عدم دسترسی.", show_alert=True)
        return
    pid = int(call.data.split(":")[1])
    po = db.mark_payout_done(pid)
    await call.answer("تسویه ثبت شد.")
    if po:
        with suppress(Exception):
            await bot.send_message(po["owner_id"], f"✅ پورسانت شما به مبلغ <b>${po['amount_usd']:.2f}</b> واریز و تسویه گردید!", parse_mode="HTML")
    await safe_edit(call.message, f"✅ درخواست تسویه #{pid} انجام شد.")

@router.callback_query(F.data.startswith("saas_buy:"))
async def cb_saas_buy_tier(call: CallbackQuery, state: FSMContext):
    await state.clear()
    tier_key = call.data.split(":")[1]
    t = SAAS_TIERS.get(tier_key)
    if not t:
        return
    await call.answer()
    qs = db.get_admin_quota_status(call.from_user.id)

    buttons = []
    if qs["wallet_balance_usd"] >= t["price_usd"]:
        buttons.append([InlineKeyboardButton(
            text=f"👛 پرداخت آنی از کیف پول پورسانت (${t['price_usd']:.2f})",
            callback_data=f"saas_wallet:{tier_key}"
        )])

    buttons.append([InlineKeyboardButton(
        text=f"💎 پرداخت ارزی با کریپتو ({t['price_usd']:g} USDT TRC20)",
        callback_data=f"saas_crypto:{tier_key}"
    )])

    buttons.append([InlineKeyboardButton(
        text=f"⭐️ پرداخت آنی با استارز تلگرام ({t['price_stars']} Stars)",
        callback_data=f"saas_stars:{tier_key}"
    )])

    if get_platform_admin_id() > 0:
        buttons.append([InlineKeyboardButton(
            text=f"💳 کارت به کارت ({t['price_toman']:,} تومان)",
            callback_data=f"saas_c2c:{tier_key}"
        )])

    buttons.append([InlineKeyboardButton(text="🔙 بازگشت به مقایسه پلن‌ها", callback_data="saas_hub")])

    features_bullets = "\n".join([f"✅ {feat}" for feat in t.get("features", [])])
    q_label = "نامحدود (∞)" if t["quota"] > 100000 else f"{t['quota']} سفارش موفق در ماه"

    await safe_edit(
        call.message,
        f"🚀 <b>جزئیات و خرید پلن «{t['title']}»</b>\n"
        f"<i>{t.get('short_desc', '')}</i>\n"
        f"━━━━━━━━━━━━━━\n"
        f"{features_bullets}\n"
        f"━━━━━━━━━━━━━━\n"
        f"📊 <b>سقف مجاز فروش ماهانه:</b> {q_label}\n"
        f"⏳ <b>مدت اعتبار پلن:</b> ۳۰ روز کامل\n"
        f"💵 <b>مبلغ ارزی:</b> <code>${t['price_usd']:g} USDT</code> | <code>{t['price_stars']} ⭐️</code>\n"
        f"💰 <b>مبلغ ریالی (با دلار {get_usd_toman_rate():,} ت):</b> <code>{t['price_toman']:,} تومان</code>\n\n"
        f"👇 روش پرداخت مورد نظر خود را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )

@router.callback_query(F.data.startswith("saas_wallet:"))
async def cb_saas_pay_wallet(call: CallbackQuery):
    tier_key = call.data.split(":")[1]
    ok = db.upgrade_tier_with_wallet(call.from_user.id, tier_key)
    if not ok:
        await call.answer("موجودی کیف پول پورسانت شما کافی نیست.", show_alert=True)
        return
    await call.answer("پلن شما با موفقیت ارتقا یافت!", show_alert=True)
    await render_saas_hub(call.from_user.id, call.message, edit=True)

@router.callback_query(F.data.startswith("saas_stars:"))
async def cb_saas_pay_stars(call: CallbackQuery):
    tier_key = call.data.split(":")[1]
    t = SAAS_TIERS.get(tier_key)
    if not t:
        return
    auth = f"PSTR-{uuid.uuid4().hex[:10].upper()}"
    db.create_platform_payment(auth, call.from_user.id, tier_key, "stars")
    await call.answer()
    inv_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⭐️ پرداخت {t['price_stars']} ستاره", pay=True)],
        [InlineKeyboardButton(text="🔙 انصراف و بازگشت", callback_data="del_inv")]
    ])
    await call.bot.send_invoice(
        chat_id=call.from_user.id,
        title=f"ارتقای ربات به {t['title']}",
        description=f"افزایش سقف فروش ماهانه به مدت ۳۰ روز ({t['title']})",
        payload=auth,
        currency="XTR",
        prices=[LabeledPrice(label=t["title"], amount=t["price_stars"])],
        reply_markup=inv_kb
    )

@router.callback_query(F.data == "del_inv")
async def cb_delete_invoice_message(call: CallbackQuery, state: FSMContext):
    await state.clear()
    db.cancel_abandoned_initiations(call.from_user.id)
    await call.answer("فاکتور لغو شد.")
    with suppress(Exception):
        await call.message.delete()

@router.callback_query(F.data.startswith("saas_crypto:"))
async def cb_saas_pay_crypto(call: CallbackQuery, state: FSMContext):
    tier_key = call.data.split(":")[1]
    t = SAAS_TIERS.get(tier_key)
    if not t:
        return
    await call.answer()
    auth = f"PCRY-{uuid.uuid4().hex[:8].upper()}"
    db.create_platform_payment(auth, call.from_user.id, tier_key, "crypto")
    await state.set_state(AdminFlow.waiting_platform_receipt)
    await state.update_data(p_auth=auth)
    await safe_edit(
        call.message,
        f"💎 <b>پرداخت ارزی پلن «{t['title']}»</b>\n\n"
        f"لطفاً دقیقاً مبلغ <b>{t['price_usd']:g} USDT</b> (خالص پس از کسر کارمزد صرافی - شبکه TRC20) را به کیف پول پلتفرم واریز کنید:\n\n"
        f"<code>{html.escape(settings.platform_crypto_wallet)}</code>\n\n"
        f"🔎 <b>پس از واریز، هش تراکنش ۶۴ رقمی (TXID) یا تصویر/PDF رسید را مستقیماً در همین چت ارسال کنید:</b>\n"
        f"کد فاکتور: <code>{auth}</code>",
        reply_markup=cancel_kb(f"saas_buy:{tier_key}")
    )

@router.callback_query(F.data.startswith("saas_c2c:"))
async def cb_saas_pay_c2c(call: CallbackQuery, state: FSMContext):
    if get_platform_admin_id() <= 0:
        await call.answer("کارت‌به‌کارت پلتفرم هنوز فعال نشده است؛ لطفاً از استارز یا تتر استفاده کنید.", show_alert=True)
        return
    tier_key = call.data.split(":")[1]
    t = SAAS_TIERS.get(tier_key)
    if not t:
        return
    await call.answer()
    auth = f"PC2C-{uuid.uuid4().hex[:8].upper()}"
    db.create_platform_payment(auth, call.from_user.id, tier_key, "card")
    await state.set_state(AdminFlow.waiting_platform_receipt)
    await state.update_data(p_auth=auth)
    await safe_edit(
        call.message,
        f"💳 <b>پرداخت کارت‌به‌کارت پلن «{t['title']}»</b>\n\n"
        f"لطفاً مبلغ <b>{t['price_toman']:,} تومان</b> را به شماره کارت زیر واریز کنید:\n\n"
        f"<code>{html.escape(settings.platform_card)}</code>\n\n"
        f"📸 <b>سپس تصویر/PDF رسید یا کد پیگیری را مستقیماً در همین چت ارسال کنید:</b>\n"
        f"کد فاکتور: <code>{auth}</code>",
        reply_markup=cancel_kb(f"saas_buy:{tier_key}")
    )

@router.message(StateFilter(AdminFlow.waiting_platform_receipt))
async def handle_platform_receipt_submission(message: Message, state: FSMContext):
    if message.text and message.text.strip() in ALL_MENU_BUTTONS:
        await state.clear()
        return

    data = await state.get_data()
    p_auth = data.get("p_auth")
    if not p_auth:
        pending = db.get_latest_pending_platform_manual(message.from_user.id)
        if pending:
            p_auth = pending["authority"]
    if not p_auth:
        return

    pp = db.get_platform_payment(p_auth)
    if not pp or pp["status"] == "paid":
        await state.clear()
        await message.answer("این فاکتور قبلاً پردازش شده است.")
        return

    raw_txt = (message.text or message.caption or "").strip()

    if pp["method"] == "crypto" and len(raw_txt) == 64 and " " not in raw_txt:
        ok_chain, chain_msg = await gateways.verify_trc20_txid(
            raw_txt, settings.platform_crypto_wallet, float(pp["amount_usd"]), message.from_user.id
        )
        if ok_chain:
            await state.clear()
            paid = db.mark_platform_payment_paid(p_auth, raw_txt.lower())
            if paid:
                await notify_platform_upgrade_success(paid)
            return
        elif "قبلاً در سیستم ثبت" in chain_msg:
            await message.answer(chain_msg)
            return

    await state.clear()
    admin_id = get_platform_admin_id()
    if admin_id <= 0:
        await message.answer("❌ تایید دستی پلتفرم در حال حاضر غیرفعال است.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ تایید و فعال‌سازی پلن ادمین", callback_data=f"papr:{p_auth}"),
        InlineKeyboardButton(text="❌ رد رسید", callback_data=f"prej:{p_auth}")
    ]])
    caption_text = (
        f"🔔 <b>درخواست ارتقای پلن SaaS جدید</b>\n"
        f"👤 ادمین: <code>{pp['owner_id']}</code>\n"
        f"🚀 پلن درخواستی: <b>{pp['target_tier'].upper()}</b> (${pp['amount_usd']:g} / {pp['amount_toman']:,} ت)\n"
        f"🔖 کد فاکتور: <code>{p_auth}</code>\n"
        f"💬 پیام / هش: <code>{html.escape(raw_txt or '[فایل رسید]')}</code>"
    )
    if message.photo:
        await bot.send_photo(admin_id, message.photo[-1].file_id, caption=caption_text, reply_markup=kb, parse_mode="HTML")
    elif message.document:
        await bot.send_document(admin_id, message.document.file_id, caption=caption_text, reply_markup=kb, parse_mode="HTML")
    else:
        await bot.send_message(admin_id, caption_text, reply_markup=kb, parse_mode="HTML")

    await message.answer("✅ رسید شما برای مدیریت پلتفرم ارسال شد و به محض تایید، پلن شما ارتقا می‌یابد.")

async def notify_platform_upgrade_success(paid_pp: dict):
    t = SAAS_TIERS.get(paid_pp["target_tier"], SAAS_TIERS["growth"])
    with suppress(Exception):
        await bot.send_message(
            paid_pp["owner_id"],
            f"🎉 <b>تبریک! اکانت شما به پلن «{t['title']}» ارتقا یافت.</b>\n\n"
            f"✅ سقف سفارش‌های ماهانه شما باز شد و تمام امکانات پلن جدید به مدت ۳۰ روز فعال گردید.",
            parse_mode="HTML"
        )
    if paid_pp.get("commission_usd", 0) > 0 and paid_pp.get("referrer_id"):
        with suppress(Exception):
            await bot.send_message(
                paid_pp["referrer_id"],
                f"🎁 <b>پاداش همکاری در فروش جدید!</b>\n\n"
                f"یکی از ادمین‌های دعوت‌شده توسط شما پلن خود را به <b>{t['title']}</b> ارتقا داد و مبلغ "
                f"<b>${paid_pp['commission_usd']:.2f} (۳۰٪ پورسانت)</b> به کیف پول شما اضافه شد!",
                parse_mode="HTML"
            )

@router.callback_query(F.data.startswith("papr:"))
async def cb_approve_platform_manual(call: CallbackQuery):
    if not is_platform_admin(call.from_user.id):
        await call.answer("عدم دسترسی.", show_alert=True)
        return
    p_auth = call.data.split(":")[1]
    paid = db.mark_platform_payment_paid(p_auth, "MANUAL-PLATFORM")
    if not paid:
        await call.answer("خطا در تایید فاکتور.", show_alert=True)
        return
    await call.answer("پلن ادمین فعال شد.")
    await notify_platform_upgrade_success(paid)
    with suppress(TelegramBadRequest):
        if call.message.photo or call.message.document:
            await call.message.edit_caption(caption=f"✅ فاکتور {p_auth} تایید و پلن ادمین فعال شد.")
        else:
            await call.message.edit_text(f"✅ فاکتور {p_auth} تایید و پلن ادمین فعال شد.")

@router.callback_query(F.data.startswith("prej:"))
async def cb_reject_platform_manual(call: CallbackQuery):
    if not is_platform_admin(call.from_user.id):
        await call.answer("عدم دسترسی.", show_alert=True)
        return
    p_auth = call.data.split(":")[1]
    pp = db.get_platform_payment(p_auth)
    await call.answer("رد شد.")
    if pp:
        with suppress(Exception):
            await bot.send_message(pp["owner_id"], f"❌ رسید فاکتور <code>{p_auth}</code> تایید نشد.", parse_mode="HTML")
    with suppress(TelegramBadRequest):
        if call.message.photo or call.message.document:
            await call.message.edit_caption(caption=f"❌ فاکتور {p_auth} رد شد.")
        else:
            await call.message.edit_text(f"❌ فاکتور {p_auth} رد شد.")

@router.callback_query(F.data == "home")
async def cb_home(call: CallbackQuery, state: FSMContext):
    await state.clear()
    db.cancel_abandoned_initiations(call.from_user.id)
    await call.answer()
    await render_home(call.from_user.id, call.message, edit=True)

@router.callback_query(F.data.startswith("store:"))
async def cb_open_store(call: CallbackQuery, state: FSMContext):
    await state.clear()
    db.cancel_abandoned_initiations(call.from_user.id)
    await call.answer()
    chat_id = int(call.data.split(":")[1])
    await render_channel_store(call.message, chat_id, edit=True)

@router.callback_query(F.data == "creator_guide")
async def cb_creator_guide(call: CallbackQuery):
    await call.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 بازگشت به صفحه اصلی", callback_data="home")]
    ])
    await safe_edit(
        call.message,
        "🚀 <b>راه‌اندازی فروش خودکار برای کانال VIP شما (در ۳۰ ثانیه):</b>\n\n"
        "۱. این ربات را در کانال یا گروه خصوصی خود <b>Admin</b> کنید (دسترسی <code>Invite Users</code> و <code>Ban Users</code> فعال باشد).\n"
        "۲. یک پست دلخواه از داخل کانال خود به همین چت <b>Forward</b> کنید.\n\n"
        "🎁 <b>تا ۱۰ سفارش اول در هر ماه کاملاً رایگان است!</b>",
        reply_markup=kb
    )

@router.callback_query(F.data == "my_subs")
async def cb_my_subs(call: CallbackQuery):
    subs = db.get_user_subscriptions(call.from_user.id)
    submitted = db.get_user_submitted_receipts(call.from_user.id)
    now = int(time.time())
    if not subs and not submitted:
        await call.answer("شما در حال حاضر هیچ اشتراک فعالی ندارید.", show_alert=True)
        return
    await call.answer()
    lines = []
    kb_rows = []
    for s in subs:
        rem_days = max(1, (s["expires_at"] - now) // 86400)
        sup = f" | پشتیبانی: {html.escape(s['support_handle'])}" if s.get("support_handle") else ""
        lines.append(f"✅ <b>{html.escape(s['channel_title'])}</b>: {rem_days} روز باقی‌مانده{sup}")
        kb_rows.append([
            InlineKeyboardButton(text=f"🔗 لینک ورود «{s['channel_title']}»", callback_data=f"relink:{s['chat_id']}"),
            InlineKeyboardButton(text=f"🔄 تمدید", callback_data=f"renew_ch:{s['chat_id']}")
        ])
    for sub_rec in submitted:
        lines.append(f"⏳ <b>{html.escape(sub_rec['channel_title'])}</b>: رسید شما در انتظار تایید مدیر کانال است (کد: <code>{sub_rec['authority']}</code>)")
    kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="home")])
    await safe_edit(
        call.message,
        "👤 <b>وضعیت اشتراک‌ها و سفارش‌های شما:</b>\n\n" + "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows)
    )

@router.callback_query(F.data.startswith("renew_ch:"))
async def cb_renew_channel(call: CallbackQuery):
    await call.answer()
    chat_id = int(call.data.split(":")[1])
    await render_channel_store(call.message, chat_id, edit=True)

@router.message(StateFilter(None), F.forward_from_chat)
async def register_forwarded_channel(message: Message, state: FSMContext):
    chat = message.forward_from_chat
    if chat.type not in ("channel", "supergroup"):
        await message.answer("لطفاً پیام را از یک کانال یا سوپرگروه فوروارد کنید.")
        return
    try:
        member = await message.bot.get_chat_member(chat.id, message.from_user.id)
        if member.status not in ("creator", "administrator"):
            await message.answer("شما ادمین این کانال نیستید.")
            return
        me = await message.bot.get_me()
        bot_member = await message.bot.get_chat_member(chat.id, me.id)
        if bot_member.status != "administrator" or not getattr(bot_member, "can_invite_users", False) or not getattr(bot_member, "can_restrict_members", False):
            await message.answer(
                "⚠️ <b>دسترسی‌های ربات در کانال ناقص است!</b>\n\n"
                "لطفاً وارد تنظیمات Admin کانال شوید و مطمئن شوید دو دسترسی زیر برای ربات روشن هستند:\n"
                "1️⃣ <code>Invite Users via Link</code> (افزودن عضو)\n"
                "2️⃣ <code>Ban Users</code> (حذف/مسدود کردن کاربر)\n\n"
                "سپس دوباره یک پست از کانال فوروارد کنید.",
                parse_mode="HTML"
            )
            return
    except Exception:
        await message.answer("ربات هنوز در این کانال ادمین نشده است. ابتدا ربات را با دسترسی Invite و Ban ادمین کنید.")
        return

    title = chat.title or "VIP Channel"
    if not db.upsert_channel(chat.id, message.from_user.id, title):
        await message.answer("⛔ این کانال قبلاً توسط ادمین دیگری در سیستم ثبت شده است.")
        return
    deep_link = f"https://t.me/{me.username}?start=ch_{chat.id}"
    await message.answer("کیبورد مدیریت ادمین در پایین صفحه فعال شد 👇", reply_markup=bottom_menu_kb(message.from_user.id))
    await message.answer(
        f"✅ کانال «{html.escape(title)}» با دسترسی‌های کامل تایید و ثبت شد.\n\n"
        f"🔗 لینک اختصاصی سفارش کانال VIP (روی لینک بزنید تا کپی شود):\n"
        f"<code>{deep_link}</code>\n\n"
        f"از دکمه‌های زیر برای ساخت پلن یا تنظیم روش‌های پرداخت استفاده کنید:",
        reply_markup=channel_admin_kb(chat.id),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("link_grp:"))
async def cb_link_group_start(call: CallbackQuery, state: FSMContext):
    chat_id = int(call.data.split(":")[1])
    if not db.verify_channel_owner(chat_id, call.from_user.id):
        await call.answer("عدم دسترسی.", show_alert=True)
        return
    qs = db.get_admin_quota_status(call.from_user.id)
    if qs["tier"] == "free":
        await call.answer("اتصال گروه گفتگو ویژه پلن‌های Growth، Pro و VIP است.", show_alert=True)
        return
    await call.answer()
    await state.update_data(chat_id=chat_id)
    await state.set_state(AdminFlow.link_group)
    await safe_edit(
        call.message,
        "👥 <b>اتصال هم‌زمان گروه گفتگو به کانال VIP</b>\n\n"
        "۱. ربات را در گروه خصوصی خود با دسترسی <code>Invite Users</code> و <code>Ban Users</code> ادمین کنید.\n"
        "۲. یک پیام دلخواه از داخل آن گروه به همین چت <b>Forward</b> کنید (یا برای قطع اتصال، عبارت <code>حذف</code> را بفرستید):",
        reply_markup=cancel_kb(f"pay_hub:{chat_id}")
    )

@router.message(StateFilter(AdminFlow.link_group))
async def step_save_linked_group(message: Message, state: FSMContext):
    data = await state.get_data()
    chat_id = data["chat_id"]
    if not db.verify_channel_owner(chat_id, message.from_user.id):
        await state.clear()
        return
    if message.text and message.text.strip() in ("حذف", "delete", "-"):
        db.set_channel_linked_group(chat_id, message.from_user.id, 0, "")
        await state.clear()
        await message.answer("✅ اتصال گروه گفتگو حذف شد.")
        await render_pay_hub(message, chat_id, edit=False)
        return
    if not message.forward_from_chat or message.forward_from_chat.type not in ("group", "supergroup"):
        await message.answer("لطفاً یک پیام از داخل یک <b>گروه یا سوپرگروه</b> به اینجا Forward کنید:", parse_mode="HTML", reply_markup=cancel_kb(f"pay_hub:{chat_id}"))
        return
    grp = message.forward_from_chat
    try:
        me = await message.bot.get_me()
        bm = await message.bot.get_chat_member(grp.id, me.id)
        if bm.status != "administrator" or not getattr(bm, "can_invite_users", False):
            await message.answer("ربات در این گروه دسترسی Invite Users ندارد. ابتدا دسترسی ادمین را اصلاح کنید.", reply_markup=cancel_kb(f"pay_hub:{chat_id}"))
            return
    except Exception:
        await message.answer("ربات هنوز در این گروه عضو/ادمین نیست.", reply_markup=cancel_kb(f"pay_hub:{chat_id}"))
        return

    db.set_channel_linked_group(chat_id, message.from_user.id, grp.id, grp.title or "VIP Group")
    await state.clear()
    await message.answer(f"✅ گروه «<b>{html.escape(grp.title or 'VIP Group')}</b>» با موفقیت به کانال متصل شد!", parse_mode="HTML")
    await render_pay_hub(message, chat_id, edit=False)

@router.callback_query(F.data.startswith("manage:"))
async def cb_manage_channel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    chat_id = int(call.data.split(":")[1])
    if not db.verify_channel_owner(chat_id, call.from_user.id):
        await call.answer("عدم دسترسی.", show_alert=True)
        return
    await call.answer()
    await render_channel_stats_view(call.message, call.from_user.id, chat_id, edit=True)

async def render_channel_stats_view(target: Message, owner_id: int, chat_id: int, edit: bool = False):
    ch = db.verify_channel_owner(chat_id, owner_id)
    if not ch:
        return
    st = db.get_channel_stats(chat_id)
    qs = db.get_admin_quota_status(owner_id)
    me = await target.bot.get_me()
    deep_link = f"https://t.me/{me.username}?start=ch_{chat_id}"
    q_str = "نامحدود (∞)" if qs["quota"] > 100000 else f"{qs['used']} از {qs['quota']}"
    text = (
        f"⚙️ <b>پیشخوان مدیریت کانال «{html.escape(ch['title'])}»</b>\n\n"
        f"🏷 <b>پلن فعلی ربات شما:</b> {qs['tier_title']} (مصرف ماهانه: {q_str})\n"
        f"👥 <b>اعضای دارای اشتراک فعال:</b> {st['active_subs']} نفر (منقضی‌شده: {st['expired_subs']} نفر)\n"
        f"💰 <b>فروش کارت‌به‌کارت تاییدشده:</b> {st['revenue']:,} تومان\n"
        f"💎 <b>فروش کریپتو تاییدشده:</b> {st['crypto_usdt']:g} USDT ({st['crypto_count']} تراکنش)\n"
        f"⭐️ <b>فروش استارز تلگرام:</b> {st['stars_rev']:,} ستاره\n"
        f"⏳ <b>رسیدهای ارسال‌شده منتظر تایید شما:</b> <b>{st['pending_manual']} مورد</b>\n\n"
        f"🔗 <b>لینک اختصاصی فروش کانال (با لمس کپی می‌شود):</b>\n<code>{deep_link}</code>"
    )
    kb = channel_admin_kb(chat_id)
    if edit:
        await safe_edit(target, text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("stats:"))
async def cb_channel_stats(call: CallbackQuery):
    chat_id = int(call.data.split(":")[1])
    if not db.verify_channel_owner(chat_id, call.from_user.id):
        await call.answer("عدم دسترسی.", show_alert=True)
        return
    await call.answer("آمار بروزرسانی شد.")
    await render_channel_stats_view(call.message, call.from_user.id, chat_id, edit=True)

@router.callback_query(F.data.startswith("pending_list:"))
async def cb_pending_receipts_list(call: CallbackQuery):
    chat_id = int(call.data.split(":")[1])
    if not db.verify_channel_owner(chat_id, call.from_user.id):
        await call.answer("عدم دسترسی.", show_alert=True)
        return
    items = db.get_submitted_receipts_for_channel(chat_id)
    if not items:
        await call.answer("هیچ رسید بررسی‌نشده‌ای در صف انتظار وجود ندارد!", show_alert=True)
        return
    await call.answer(f"در حال نمایش {len(items)} رسید منتظر بررسی...")
    for item in items:
        auth = item["authority"]
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ تایید و ارسال لینک دعوت", callback_data=f"apr:{auth}"),
            InlineKeyboardButton(text="❌ رد رسید", callback_data=f"rej:{auth}")
        ]])
        method_badge = "💎 کریپتو" if item["method"] == "crypto" else "💳 کارت‌به‌کارت"
        p_name = item.get("plan_name") or "پلن اشتراک"
        caption_text = (
            f"⏳ <b>رسید منتظر بررسی ({method_badge})</b>\n"
            f"📢 کانال: «{html.escape(item['channel_title'])}»\n"
            f"👤 آیدی خریدار: <code>{item['user_id']}</code>\n"
            f"📦 پلن: {html.escape(p_name)} ({item['amount_toman']:,} تومان)\n"
            f"🔖 کد سفارش: <code>{auth}</code>\n"
            f"💬 توضیحات / هش: <code>{html.escape(item['receipt_text'] or '[فایل رسید]')}</code>"
        )
        if item.get("receipt_photo"):
            with suppress(Exception):
                await call.bot.send_photo(call.from_user.id, item["receipt_photo"], caption=caption_text, reply_markup=kb, parse_mode="HTML")
                continue
        if item.get("receipt_doc"):
            with suppress(Exception):
                await call.bot.send_document(call.from_user.id, item["receipt_doc"], caption=caption_text, reply_markup=kb, parse_mode="HTML")
                continue
        await call.bot.send_message(call.from_user.id, caption_text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("promo_hub:"))
async def cb_promo_hub(call: CallbackQuery):
    chat_id = int(call.data.split(":")[1])
    if not db.verify_channel_owner(chat_id, call.from_user.id):
        await call.answer("عدم دسترسی.", show_alert=True)
        return
    qs = db.get_admin_quota_status(call.from_user.id)
    if qs["tier"] == "free":
        await call.answer("ساخت کد تخفیف ویژه پلن‌های Growth، Pro و VIP است.", show_alert=True)
        return
    await call.answer()
    promos = db.get_channel_promos(chat_id)
    rows = [[InlineKeyboardButton(text="➕ ساخت کد تخفیف جدید", callback_data=f"new_promo:{chat_id}")]]
    if not promos:
        txt = "🎟 <b>کدهای تخفیف کانال</b>\n\nهنوز هیچ کد تخفیفی نساخته‌اید."
    else:
        lines = []
        for pr in promos:
            lines.append(f"• کد <code>{pr['code']}</code> — <b>{pr['discount_pct']}٪ تخفیف</b> (مصرف: {pr['used_count']}/{pr['max_uses']})")
            rows.append([InlineKeyboardButton(text=f"🗑 حذف کد {pr['code']}", callback_data=f"del_promo:{pr['id']}")])
        txt = "🎟 <b>کدهای تخفیف فعال کانال:</b>\n\n" + "\n".join(lines)
    rows.append([InlineKeyboardButton(text="🔙 بازگشت به پنل کانال", callback_data=f"manage:{chat_id}")])
    await safe_edit(call.message, txt, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

@router.callback_query(F.data.startswith("del_promo:"))
async def cb_del_promo(call: CallbackQuery):
    pid = int(call.data.split(":")[1])
    cid = db.delete_promo_code(pid, call.from_user.id)
    if not cid:
        await call.answer("کد یافت نشد.", show_alert=True)
        return
    await call.answer("کد تخفیف حذف شد.")
    call.data = f"promo_hub:{cid}"
    await cb_promo_hub(call)

@router.callback_query(F.data.startswith("new_promo:"))
async def cb_new_promo_start(call: CallbackQuery, state: FSMContext):
    chat_id = int(call.data.split(":")[1])
    if not db.verify_channel_owner(chat_id, call.from_user.id):
        return
    await call.answer()
    await state.update_data(chat_id=chat_id)
    await state.set_state(AdminFlow.promo_code)
    await safe_edit(
        call.message,
        "🎟 <b>ساخت کد تخفیف جدید</b>\n\n"
        "۱/۳ نام کد تخفیف را به انگلیسی وارد کنید (مثلاً <code>VIP20</code>):",
        reply_markup=cancel_kb(f"promo_hub:{chat_id}")
    )

@router.message(StateFilter(AdminFlow.promo_code))
async def step_promo_code(message: Message, state: FSMContext):
    code = (message.text or "").strip().upper()
    data = await state.get_data()
    if not code or len(code) > 20 or " " in code:
        await message.answer("کد تخفیف باید بدون فاصله و حداکثر ۲۰ کاراکتر باشد (مثلاً OFF25):", reply_markup=cancel_kb(f"promo_hub:{data['chat_id']}"))
        return
    await state.update_data(p_code=code)
    await state.set_state(AdminFlow.promo_pct)
    await message.answer("۲/۳ درصد تخفیف را وارد کنید (عددی بین ۵ تا ۹۰؛ مثلاً 20):", reply_markup=cancel_kb(f"promo_hub:{data['chat_id']}"))

@router.message(StateFilter(AdminFlow.promo_pct))
async def step_promo_pct(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        pct = parse_clean_int(message.text or "")
        if pct < 1 or pct > 95:
            raise ValueError
    except ValueError:
        await message.answer("درصد تخفیف باید عددی بین ۱ تا ۹۵ باشد:", reply_markup=cancel_kb(f"promo_hub:{data['chat_id']}"))
        return
    await state.update_data(p_pct=pct)
    await state.set_state(AdminFlow.promo_uses)
    await message.answer("۳/۳ حداکثر تعداد قابل استفاده (ظرفیت نفرات) را وارد کنید (مثلاً 50):", reply_markup=cancel_kb(f"promo_hub:{data['chat_id']}"))

@router.message(StateFilter(AdminFlow.promo_uses))
async def step_promo_uses(message: Message, state: FSMContext):
    data = await state.get_data()
    chat_id = data["chat_id"]
    if not db.verify_channel_owner(chat_id, message.from_user.id):
        await state.clear()
        return
    try:
        uses = parse_clean_int(message.text or "")
        if uses < 1:
            raise ValueError
    except ValueError:
        await message.answer("تعداد باید حداقل ۱ باشد:", reply_markup=cancel_kb(f"promo_hub:{chat_id}"))
        return
    await state.clear()
    db.create_promo_code(chat_id, data["p_code"], data["p_pct"], uses)
    await message.answer(
        f"✅ کد تخفیف <code>{data['p_code']}</code> با <b>{data['p_pct']}٪ تخفیف</b> (ظرفیت {uses} نفر) ساخته شد!",
        reply_markup=channel_admin_kb(chat_id),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("gift_sub:"))
async def cb_gift_sub_start(call: CallbackQuery, state: FSMContext):
    chat_id = int(call.data.split(":")[1])
    if not db.verify_channel_owner(chat_id, call.from_user.id):
        await call.answer("عدم دسترسی.", show_alert=True)
        return
    qs = db.get_admin_quota_status(call.from_user.id)
    if qs["tier"] == "free":
        await call.answer("اهدای اشتراک دستی ویژه پلن‌های Growth، Pro و VIP است.", show_alert=True)
        return
    if qs["is_locked"]:
        await call.answer("سقف سفارش ماهانه شما پر شده است. ابتدا پلن را ارتقا دهید.", show_alert=True)
        return
    await state.update_data(chat_id=chat_id)
    await state.set_state(AdminFlow.gift_user_id)
    await call.answer()
    await safe_edit(
        call.message,
        "🎁 <b>اهدای اشتراک دستی به کاربر (بدون نیاز به پرداخت)</b>\n\n"
        "۱/۲ لطفاً <b>آیدی عددی (User ID)</b> کاربر مورد نظر را وارد کنید:",
        reply_markup=cancel_kb(f"manage:{chat_id}")
    )

@router.message(StateFilter(AdminFlow.gift_user_id))
async def step_gift_uid(message: Message, state: FSMContext):
    data = await state.get_data()
    if not db.verify_channel_owner(data["chat_id"], message.from_user.id):
        await state.clear()
        return
    try:
        target_uid = parse_clean_int(message.text or "")
    except ValueError:
        await message.answer("لطفاً فقط آیدی عددی کاربر را وارد کنید:", reply_markup=cancel_kb(f"manage:{data['chat_id']}"))
        return
    await state.update_data(target_uid=target_uid)
    await state.set_state(AdminFlow.gift_days)
    await message.answer(
        f"۲/۲ تعداد روز اعتبار اشتراک برای کاربر <code>{target_uid}</code> را وارد کنید (مثلاً 30):",
        reply_markup=cancel_kb(f"manage:{data['chat_id']}"),
        parse_mode="HTML"
    )

@router.message(StateFilter(AdminFlow.gift_days))
async def step_gift_days(message: Message, state: FSMContext):
    data = await state.get_data()
    chat_id = data["chat_id"]
    if not db.verify_channel_owner(chat_id, message.from_user.id):
        await state.clear()
        return
    target_uid = data["target_uid"]
    try:
        days = parse_clean_int(message.text or "")
        if days <= 0 or days > 3650:
            raise ValueError
    except ValueError:
        await message.answer("لطفاً یک عدد صحیح بین ۱ تا ۳۶۵۰ وارد کنید:", reply_markup=cancel_kb(f"manage:{chat_id}"))
        return
    await state.clear()
    db.grant_manual_subscription(target_uid, chat_id, message.from_user.id, days)
    await issue_invite_link(target_uid, chat_id, message.bot, count_quota=True)
    await message.answer(
        f"✅ اشتراک <b>{days} روزه</b> برای کاربر <code>{target_uid}</code> فعال شد و لینک ورود یک‌بارمصرف برایش ارسال گردید.",
        reply_markup=channel_admin_kb(chat_id),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("bcast_menu:"))
async def cb_bcast_menu(call: CallbackQuery):
    chat_id = int(call.data.split(":")[1])
    if not db.verify_channel_owner(chat_id, call.from_user.id):
        await call.answer("عدم دسترسی.", show_alert=True)
        return
    qs = db.get_admin_quota_status(call.from_user.id)
    if qs["tier"] == "free":
        await call.answer("ارسال پیام همگانی ویژه پلن‌های Growth، Pro و VIP است.", show_alert=True)
        return
    st = db.get_channel_stats(chat_id)
    await call.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🟢 ارسال به اعضای فعال ({st['active_subs']} نفر)", callback_data=f"bcast_go:active:{chat_id}")],
        [InlineKeyboardButton(text=f"🟠 ارسال به اعضای منقضی‌شده ({st['expired_subs']} نفر - بازاریابی مجدد)", callback_data=f"bcast_go:expired:{chat_id}")],
        [InlineKeyboardButton(text="🔙 بازگشت به پنل کانال", callback_data=f"manage:{chat_id}")]
    ])
    await safe_edit(
        call.message,
        "📣 <b>ارسال پیام همگانی (Broadcast) به مخاطبان کانال</b>\n\n"
        "می‌توانید اطلاعیه مهم را برای اعضای فعال بفرستید، یا یک پیشنهاد تخفیف برای اعضای منقضی‌شده ارسال کنید تا دوباره اشتراک بخرند:",
        reply_markup=kb
    )

@router.callback_query(F.data.startswith("bcast_go:"))
async def cb_bcast_start(call: CallbackQuery, state: FSMContext):
    _, group, cid_str = call.data.split(":")
    chat_id = int(cid_str)
    if not db.verify_channel_owner(chat_id, call.from_user.id):
        return
    uids = db.get_channel_Subscriber_ids(chat_id, group)
    if not uids:
        await call.answer("هیچ کاربری در این گروه وجود ندارد.", show_alert=True)
        return
    await call.answer()
    await state.update_data(chat_id=chat_id, bcast_group=group)
    await state.set_state(AdminFlow.broadcast_msg)
    g_name = "اعضای فعال" if group == "active" else "اعضای منقضی‌شده"
    await safe_edit(
        call.message,
        f"✍️ متن یا تصویر پیام همگانی خود را برای ارسال به <b>{len(uids)} نفر ({g_name})</b> بفرستید:",
        reply_markup=cancel_kb(f"bcast_menu:{chat_id}")
    )

@router.message(StateFilter(AdminFlow.broadcast_msg))
async def step_bcast_send(message: Message, state: FSMContext):
    if message.text and message.text.strip() in ALL_MENU_BUTTONS:
        await state.clear()
        return
    data = await state.get_data()
    chat_id = data["chat_id"]
    ch = db.verify_channel_owner(chat_id, message.from_user.id)
    if not ch:
        await state.clear()
        return
    group = data["bcast_group"]
    await state.clear()

    uids = db.get_channel_Subscriber_ids(chat_id, group)
    sent_ok = 0
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🌟 مشاهده پلن‌های «{ch['title']}»", callback_data=f"store:{chat_id}")]
    ])
    for uid in uids:
        with suppress(Exception):
            if message.photo:
                await message.bot.send_photo(uid, message.photo[-1].file_id, caption=message.caption or "", reply_markup=kb)
            else:
                await message.bot.send_message(uid, message.text or "", reply_markup=kb)
            sent_ok += 1
            await asyncio.sleep(0.05)

    await message.answer(
        f"✅ پیام همگانی با موفقیت برای <b>{sent_ok} نفر</b> ارسال شد.",
        reply_markup=channel_admin_kb(chat_id),
        parse_mode="HTML"
    )

async def render_plans_manager(target: Message, chat_id: int, edit: bool = False):
    plans = db.get_channel_plans(chat_id)
    rows = []
    if not plans:
        text = "هنوز هیچ پلنی برای این کانال نساخته‌اید."
    else:
        lines = [format_plan_line(p) for p in plans]
        for p in plans:
            rows.append([
                InlineKeyboardButton(text=f"✏️ تغییر قیمت «{p['name']}»", callback_data=f"edit_plan:{p['id']}"),
                InlineKeyboardButton(text="🗑 حذف", callback_data=f"del_plan:{p['id']}")
            ])
        text = "📋 <b>مدیریت، ویرایش قیمت و حذف پلن‌های کانال:</b>\n\n" + "\n\n".join(lines)
    rows.append([InlineKeyboardButton(text="➕ افزودن پلن جدید", callback_data=f"wiz_plan:{chat_id}")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت به پنل کانال", callback_data=f"manage:{chat_id}")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    if edit:
        await safe_edit(target, text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("list_plans:"))
async def cb_list_plans(call: CallbackQuery):
    chat_id = int(call.data.split(":")[1])
    if not db.verify_channel_owner(chat_id, call.from_user.id):
        await call.answer("عدم دسترسی.", show_alert=True)
        return
    await call.answer()
    await render_plans_manager(call.message, chat_id, edit=True)

@router.callback_query(F.data.startswith("edit_plan:"))
async def cb_edit_plan_start(call: CallbackQuery, state: FSMContext):
    plan_id = int(call.data.split(":")[1])
    plan = db.get_plan(plan_id)
    if not plan or plan["owner_id"] != call.from_user.id:
        await call.answer("پلن یافت نشد.", show_alert=True)
        return
    await call.answer()
    await state.update_data(edit_plan_id=plan_id, chat_id=plan["chat_id"])
    await state.set_state(AdminFlow.edit_plan_orig)
    await safe_edit(
        call.message,
        f"✏️ <b>ویرایش قیمت پلن «{html.escape(plan['name'])}»</b>\n\n"
        f"۱/۲ ابتدا <b>قیمت اصلی (قبل از تخفیف)</b> را به تومان وارد کنید (مثلاً <code>10000000</code>):",
        reply_markup=cancel_kb(f"list_plans:{plan['chat_id']}")
    )

@router.message(StateFilter(AdminFlow.edit_plan_orig))
async def step_edit_plan_orig(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        orig_price = parse_clean_int(message.text or "")
        if orig_price < 1000:
            raise ValueError
    except ValueError:
        await message.answer("لطفاً یک عدد معتبر حداقل ۱۰۰۰ تومان وارد کنید:", reply_markup=cancel_kb(f"list_plans:{data['chat_id']}"))
        return
    await state.update_data(edit_orig=orig_price)
    await state.set_state(AdminFlow.edit_plan_sell)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"بدون تخفیف (همان {orig_price:,} تومان)", callback_data="edit_no_disc")],
        [InlineKeyboardButton(text="❌ انصراف", callback_data=f"list_plans:{data['chat_id']}")]
    ])
    await message.answer(
        f"۲/۲ حالا <b>قیمت نهایی فروش (با تخفیف)</b> را به تومان وارد کنید (مثلاً <code>8000000</code>) یا دکمه بدون تخفیف را بزنید:",
        reply_markup=kb,
        parse_mode="HTML"
    )

@router.callback_query(StateFilter(AdminFlow.edit_plan_sell), F.data == "edit_no_disc")
async def cb_edit_no_disc(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    await call.answer("بروزرسانی شد.")
    chat_id = db.update_plan_pricing(data["edit_plan_id"], call.from_user.id, data["edit_orig"], 0)
    if chat_id:
        await render_plans_manager(call.message, chat_id, edit=True)

@router.message(StateFilter(AdminFlow.edit_plan_sell))
async def step_edit_plan_sell_save(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        sell_price = parse_clean_int(message.text or "")
        if sell_price < 1000:
            raise ValueError
    except ValueError:
        await message.answer("لطفاً یک عدد معتبر حداقل ۱۰۰۰ تومان وارد کنید:", reply_markup=cancel_kb(f"list_plans:{data['chat_id']}"))
        return
    await state.clear()
    chat_id = db.update_plan_pricing(data["edit_plan_id"], message.from_user.id, sell_price, data["edit_orig"])
    await message.answer("✅ قیمت پلن با موفقیت بروزرسانی شد.")
    if chat_id:
        await render_plans_manager(message, chat_id, edit=False)

@router.callback_query(F.data.startswith("del_plan:"))
async def cb_delete_plan(call: CallbackQuery):
    plan_id = int(call.data.split(":")[1])
    chat_id = db.delete_plan(plan_id, call.from_user.id)
    if not chat_id:
        await call.answer("پلن یافت نشد.", show_alert=True)
        return
    await call.answer("پلن آرشیو و حذف شد.")
    await render_plans_manager(call.message, chat_id, edit=True)

@router.callback_query(F.data.startswith("wiz_plan:"))
async def cb_start_plan_wizard(call: CallbackQuery, state: FSMContext):
    chat_id = int(call.data.split(":")[1])
    if not db.verify_channel_owner(chat_id, call.from_user.id):
        await call.answer("عدم دسترسی.", show_alert=True)
        return
    await state.update_data(chat_id=chat_id)
    await state.set_state(AdminFlow.plan_name)
    await safe_edit(
        call.message,
        "۱/۴ <b>نام پلن اشتراک</b> را تایپ و ارسال کنید (مثلاً: اشتراک ۳ ماهه الماسی):",
        reply_markup=cancel_kb(f"manage:{chat_id}")
    )
    await call.answer()

@router.message(StateFilter(AdminFlow.plan_name))
async def step_plan_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    data = await state.get_data()
    if not name:
        await message.answer("لطفاً یک نام معتبر وارد کنید:", reply_markup=cancel_kb(f"manage:{data['chat_id']}"))
        return
    await state.update_data(name=name)
    await state.set_state(AdminFlow.plan_days)
    await message.answer(
        "۲/۴ <b>مدت اعتبار اشتراک (به روز)</b> را به صورت عدد وارد کنید (مثلاً: 90):",
        reply_markup=cancel_kb(f"manage:{data['chat_id']}"),
        parse_mode="HTML"
    )

@router.message(StateFilter(AdminFlow.plan_days))
async def step_plan_days_text(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        days = parse_clean_int(message.text or "")
        if days <= 0 or days > 3650:
            raise ValueError
    except ValueError:
        await message.answer("لطفاً فقط عدد روز را به صورت صحیح وارد کنید (مثلاً 30):", reply_markup=cancel_kb(f"manage:{data['chat_id']}"))
        return
    await state.update_data(days=days)
    await state.set_state(AdminFlow.plan_orig_price)
    await message.answer(
        f"مدت ثبت‌شده: <b>{days} روز</b>.\n\n"
        f"۳/۴ <b>قیمت اصلی پلن (به تومان)</b> را وارد کنید (مثلاً: 10000000):",
        reply_markup=cancel_kb(f"manage:{data['chat_id']}"),
        parse_mode="HTML"
    )

@router.message(StateFilter(AdminFlow.plan_orig_price))
async def step_plan_orig_price_text(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        orig_price = parse_clean_int(message.text or "")
        if orig_price < 1000:
            raise ValueError
    except ValueError:
        await message.answer("مبلغ باید حداقل ۱۰۰۰ تومان باشد:", reply_markup=cancel_kb(f"manage:{data['chat_id']}"))
        return
    await state.update_data(orig_price=orig_price)
    await state.set_state(AdminFlow.plan_sell_price)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ بدون تخفیف (فروش به همان {orig_price:,} تومان)", callback_data="plan_no_disc")],
        [InlineKeyboardButton(text="❌ انصراف", callback_data=f"manage:{data['chat_id']}")]
    ])
    await message.answer(
        f"قیمت اصلی: <b>{orig_price:,} تومان</b>.\n\n"
        f"۴/۴ اگر می‌خواهید این پلن را با <b>تخفیف ویژه</b> بفروشید، <b>قیمت نهایی فروش (مثلاً 8000000)</b> را تایپ کنید تا قیمت قبلی خط‌خورده نمایش داده شود؛ در غیر این صورت دکمه زیر را بزنید:",
        reply_markup=kb,
        parse_mode="HTML"
    )

async def finalize_plan_creation(target_msg: Message, state: FSMContext, sell_price: int):
    data = await state.get_data()
    await state.clear()
    orig_price = data["orig_price"]
    db.create_plan(data["chat_id"], data["name"], data["days"], sell_price, orig_price)
    me = await target_msg.bot.get_me()
    deep_link = f"https://t.me/{me.username}?start=ch_{data['chat_id']}"
    disc_line = ""
    if orig_price > sell_price:
        pct = round((1 - (sell_price / orig_price)) * 100)
        disc_line = f"🔥 قیمت اصلی: <s>{orig_price:,} تومان</s> ({pct}٪ تخفیف ویژه!)\n"
    await target_msg.answer(
        f"✅ پلن «<b>{html.escape(data['name'])}</b>» ({data['days']} روز) با موفقیت ساخته شد!\n\n"
        f"{disc_line}"
        f"💰 <b>قیمت نهایی فروش:</b> {sell_price:,} تومان\n\n"
        f"🔗 لینک سفارش کانال VIP (قابل کپی):\n<code>{deep_link}</code>",
        reply_markup=channel_admin_kb(data["chat_id"]),
        parse_mode="HTML"
    )

@router.callback_query(StateFilter(AdminFlow.plan_sell_price), F.data == "plan_no_disc")
async def cb_plan_no_discount(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not db.verify_channel_owner(data["chat_id"], call.from_user.id):
        await state.clear()
        return
    await call.answer()
    await finalize_plan_creation(call.message, state, data["orig_price"])

@router.message(StateFilter(AdminFlow.plan_sell_price))
async def step_plan_sell_price_text(message: Message, state: FSMContext):
    data = await state.get_data()
    if not db.verify_channel_owner(data["chat_id"], message.from_user.id):
        await state.clear()
        return
    try:
        sell_price = parse_clean_int(message.text or "")
        if sell_price < 1000:
            raise ValueError
    except ValueError:
        await message.answer("مبلغ فروش باید حداقل ۱۰۰۰ تومان باشد:", reply_markup=cancel_kb(f"manage:{data['chat_id']}"))
        return
    await finalize_plan_creation(message, state, sell_price)

@router.callback_query(F.data.startswith("pay_hub:"))
async def cb_pay_hub(call: CallbackQuery, state: FSMContext):
    await state.clear()
    chat_id = int(call.data.split(":")[1])
    if not db.verify_channel_owner(chat_id, call.from_user.id):
        await call.answer("عدم دسترسی.", show_alert=True)
        return
    await call.answer()
    await render_pay_hub(call.message, chat_id, edit=True)

@router.callback_query(F.data.startswith("set_card:"))
async def cb_set_card_start(call: CallbackQuery, state: FSMContext):
    chat_id = int(call.data.split(":")[1])
    if not db.verify_channel_owner(chat_id, call.from_user.id):
        return
    await state.update_data(chat_id=chat_id)
    await state.set_state(AdminFlow.edit_card)
    await safe_edit(
        call.message,
        "💳 <b>شماره کارت ۱۶ رقمی</b> و نام صاحب حساب را ارسال کنید (یا <code>حذف</code>):",
        reply_markup=cancel_kb(f"pay_hub:{chat_id}")
    )
    await call.answer()

@router.message(StateFilter(AdminFlow.edit_card))
async def step_save_card(message: Message, state: FSMContext):
    data = await state.get_data()
    chat_id = data["chat_id"]
    raw = (message.text or "").strip()
    card_val = "" if raw in ("حذف", "delete", "-") else raw
    db.update_channel_card(chat_id, message.from_user.id, card_val)
    await state.clear()
    await message.answer("✅ تنظیمات کارت‌به‌کارت بروزرسانی شد.")
    await render_pay_hub(message, chat_id, edit=False)

@router.callback_query(F.data.startswith("set_crypto:"))
async def cb_set_crypto_start(call: CallbackQuery, state: FSMContext):
    chat_id = int(call.data.split(":")[1])
    if not db.verify_channel_owner(chat_id, call.from_user.id):
        return
    await state.update_data(chat_id=chat_id)
    await state.set_state(AdminFlow.edit_crypto)
    await safe_edit(
        call.message,
        "💎 <b>آدرس کیف پول تتر (USDT TRC20)</b> خود را ارسال کنید (یا <code>حذف</code>):",
        reply_markup=cancel_kb(f"pay_hub:{chat_id}")
    )
    await call.answer()

@router.message(StateFilter(AdminFlow.edit_crypto))
async def step_save_crypto(message: Message, state: FSMContext):
    data = await state.get_data()
    chat_id = data["chat_id"]
    raw = (message.text or "").strip()
    wallet_val = "" if raw in ("حذف", "delete", "-") else raw
    db.update_channel_crypto(chat_id, message.from_user.id, wallet_val)
    await state.clear()
    await message.answer("✅ آدرس کیف پول کریپتو با موفقیت ذخیره شد.")
    await render_pay_hub(message, chat_id, edit=False)

@router.callback_query(F.data.startswith("set_sup:"))
async def cb_set_support_start(call: CallbackQuery, state: FSMContext):
    chat_id = int(call.data.split(":")[1])
    if not db.verify_channel_owner(chat_id, call.from_user.id):
        return
    await state.update_data(chat_id=chat_id)
    await state.set_state(AdminFlow.edit_support)
    await safe_edit(
        call.message,
        "📞 <b>آیدی تلگرام پشتیبانی کانال</b> را وارد کنید (مثلاً <code>@MySupportID</code> یا <code>حذف</code>):",
        reply_markup=cancel_kb(f"pay_hub:{chat_id}")
    )
    await call.answer()

@router.message(StateFilter(AdminFlow.edit_support))
async def step_save_support(message: Message, state: FSMContext):
    data = await state.get_data()
    chat_id = data["chat_id"]
    raw = (message.text or "").strip()
    sup_val = "" if raw in ("حذف", "delete", "-") else raw
    db.update_channel_support(chat_id, message.from_user.id, sup_val)
    await state.clear()
    await message.answer("✅ آیدی پشتیبانی کانال ذخیره شد.")
    await render_pay_hub(message, chat_id, edit=False)

@router.callback_query(F.data.startswith("toggle_pub:"))
async def cb_toggle_public(call: CallbackQuery):
    chat_id = int(call.data.split(":")[1])
    ch = db.verify_channel_owner(chat_id, call.from_user.id)
    if not ch:
        return
    qs = db.get_admin_quota_status(call.from_user.id)
    if ch.get("is_public", 1) == 1 and qs["tier"] not in ("pro", "vip"):
        await call.answer("مخفی‌سازی کانال از ویترین عمومی ویژه پلن‌های Pro و VIP است.", show_alert=True)
        return
    new_val = 0 if ch.get("is_public", 1) else 1
    db.toggle_channel_public(chat_id, call.from_user.id, new_val)
    await call.answer("وضعیت نمایش عمومی کانال تغییر کرد.")
    await render_pay_hub(call.message, chat_id, edit=True)

@router.callback_query(F.data.startswith("toggle_stars:"))
async def cb_toggle_stars(call: CallbackQuery):
    chat_id = int(call.data.split(":")[1])
    ch = db.verify_channel_owner(chat_id, call.from_user.id)
    if not ch:
        return
    new_val = 0 if ch.get("stars_enabled", 1) else 1
    db.toggle_channel_stars(chat_id, call.from_user.id, new_val)
    await call.answer("وضعیت پرداخت با Telegram Stars تغییر کرد.")
    await render_pay_hub(call.message, chat_id, edit=True)

async def render_plan_checkout(target: Message, user_id: int, plan_id: int, promo_code: str = "", edit: bool = True):
    plan = db.get_plan(plan_id)
    if not plan:
        return

    sell_toman = plan["price_toman"]
    promo_line = ""
    applied_code = ""
    if promo_code:
        pr = db.validate_promo_code(plan["chat_id"], user_id, promo_code)
        if pr:
            applied_code = pr["code"]
            sell_toman = max(1000, int(round(sell_toman * (1 - pr["discount_pct"] / 100.0))))
            promo_line = f"\n🎟 <b>کد تخفیف اعمال‌شده:</b> <code>{applied_code}</code> ({pr['discount_pct']}٪ تخفیف اضافه)"

    usd_rate = get_usd_toman_rate()
    star_rate = get_star_toman_rate()
    final_usdt = round(max(0.5, sell_toman / usd_rate), 2)
    final_stars = max(1, round(sell_toman / star_rate))

    buttons = []
    if plan.get("crypto_wallet"):
        buttons.append([InlineKeyboardButton(
            text=f"💎 پرداخت ارزی با تتر ({final_usdt:g} USDT TRC20)",
            callback_data=f"pay_crypto:{plan_id}:{applied_code}"
        )])

    if plan.get("stars_enabled", 1) and final_stars > 0:
        buttons.append([InlineKeyboardButton(
            text=f"⭐️ پرداخت سریع با استارز تلگرام ({final_stars} Stars)",
            callback_data=f"pay_stars:{plan_id}:{applied_code}"
        )])

    if plan.get("card_number"):
        buttons.append([InlineKeyboardButton(
            text=f"💳 کارت به کارت ({sell_toman:,} تومان)",
            callback_data=f"c2c:{plan_id}:{applied_code}"
        )])

    if not buttons:
        buttons.append([InlineKeyboardButton(
            text=f"💳 کارت به کارت ({sell_toman:,} تومان)",
            callback_data=f"c2c:{plan_id}:{applied_code}"
        )])

    if not applied_code:
        buttons.append([InlineKeyboardButton(text="🎟 کد تخفیف دارید؟ وارد کنید", callback_data=f"ask_promo:{plan_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت به لیست پلن‌ها", callback_data=f"renew_ch:{plan['chat_id']}")])

    orig_line = ""
    if plan.get("original_price_toman", 0) > sell_toman:
        orig_line = f"\n🏷 <b>قیمت اصلی:</b> <s>{plan['original_price_toman']:,} تومان</s>"

    sup_line = f"\n📞 پشتیبانی کانال: {html.escape(plan['support_handle'])}" if plan.get("support_handle") else ""
    footer = await get_viral_footer(plan["owner_id"], target.bot)

    text = (
        f"📦 <b>پلن انتخابی:</b> {html.escape(plan['name'])}\n"
        f"⏳ <b>مدت اعتبار:</b> {plan['duration_days']} روز{orig_line}{promo_line}\n"
        f"💰 <b>مبلغ قابل پرداخت:</b> <b>{sell_toman:,} تومان</b>\n"
        f"💎 <b>معادل کریپتو:</b> <b>{final_usdt:g} USDT</b>\n"
        f"⭐️ <b>معادل استارز:</b> <b>{final_stars} ستاره</b>{sup_line}\n\n"
        f"👇 روش پرداخت مورد نظر خود را انتخاب کنید:{footer}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    if edit:
        await safe_edit(target, text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)

@router.callback_query(F.data.startswith("buy:"))
async def cb_buy_plan(call: CallbackQuery, state: FSMContext):
    await state.clear()
    db.cancel_abandoned_initiations(call.from_user.id)

    plan_id = int(call.data.split(":")[1])
    plan = db.get_plan(plan_id)
    if not plan:
        await call.answer("این پلن وجود ندارد.", show_alert=True)
        return

    qs = db.get_admin_quota_status(plan["owner_id"])
    if qs["is_locked"]:
        await call.answer("ظرفیت پذیرش خودکار این کانال موقتاً تکمیل شده است؛ به مدیر کانال اطلاع داده شد.", show_alert=True)
        with suppress(Exception):
            kb_up = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🚀 ارتقای فوری پلن و باز کردن قفل فروش", callback_data="saas_hub")]
            ])
            await bot.send_message(
                plan["owner_id"],
                f"🚨 <b>هشدار توقف فروش در کانال «{html.escape(plan['channel_title'])}»!</b>\n\n"
                f"یک مشتری جدید قصد خرید پلن <b>«{html.escape(plan['name'])}»</b> را داشت، اما سقف <b>{qs['quota']} سفارش ماهانه</b> پلن فعلی شما پر شده است!\n"
                f"👇 همین الان پلن ربات خود را ارتقا دهید:",
                reply_markup=kb_up,
                parse_mode="HTML"
            )
        return

    await call.answer()
    await render_plan_checkout(call.message, call.from_user.id, plan_id, "", edit=True)

@router.callback_query(F.data.startswith("ask_promo:"))
async def cb_ask_promo(call: CallbackQuery, state: FSMContext):
    plan_id = int(call.data.split(":")[1])
    await call.answer()
    await state.set_state(BuyerFlow.waiting_promo)
    await state.update_data(promo_plan_id=plan_id)
    await safe_edit(
        call.message,
        "🎟 <b>اعمال کد تخفیف</b>\n\nلطفاً کد تخفیف خود را تایپ و ارسال کنید:",
        reply_markup=cancel_kb(f"buy:{plan_id}")
    )

@router.message(StateFilter(BuyerFlow.waiting_promo))
async def step_apply_promo(message: Message, state: FSMContext):
    if message.text and message.text.strip() in ALL_MENU_BUTTONS:
        await state.clear()
        return
    data = await state.get_data()
    plan_id = data["promo_plan_id"]
    plan = db.get_plan(plan_id)
    code = (message.text or "").strip().upper()
    pr = db.validate_promo_code(plan["chat_id"], message.from_user.id, code) if plan else None
    if not pr:
        await message.answer("❌ کد تخفیف نامعتبر است، ظرفیتش تمام شده یا قبلاً از آن استفاده کرده‌اید:", reply_markup=cancel_kb(f"buy:{plan_id}"))
        return
    await state.clear()
    await message.answer(f"✅ کد تخفیف <code>{pr['code']}</code> ({pr['discount_pct']}٪) با موفقیت اعمال شد!", parse_mode="HTML")
    await render_plan_checkout(message, message.from_user.id, plan_id, pr["code"], edit=False)

def _calc_final_amounts(plan: dict, user_id: int, promo_code: str) -> tuple[int, float, int, str]:
    sell_toman = plan["price_toman"]
    applied = ""
    if promo_code:
        pr = db.validate_promo_code(plan["chat_id"], user_id, promo_code)
        if pr:
            applied = pr["code"]
            sell_toman = max(1000, int(round(sell_toman * (1 - pr["discount_pct"] / 100.0))))
    final_usdt = round(max(0.5, sell_toman / get_usd_toman_rate()), 2)
    final_stars = max(1, round(sell_toman / get_star_toman_rate()))
    return sell_toman, final_usdt, final_stars, applied

@router.callback_query(F.data.startswith("pay_crypto:"))
async def cb_pay_crypto(call: CallbackQuery, state: FSMContext):
    parts = call.data.split(":")
    plan_id = int(parts[1])
    promo = parts[2] if len(parts) > 2 else ""
    plan = db.get_plan(plan_id)
    if not plan or not plan.get("crypto_wallet"):
        await call.answer("پرداخت کریپتو برای این کانال تنظیم نشده است.", show_alert=True)
        return
    await call.answer()
    sell_toman, final_usdt, _, applied = _calc_final_amounts(plan, call.from_user.id, promo)
    wallet_info = html.escape(plan["crypto_wallet"])
    auth = f"CRY-{uuid.uuid4().hex[:8].upper()}"
    db.create_payment(auth, call.from_user.id, plan["chat_id"], plan_id, sell_toman, "crypto", amount_usdt=final_usdt, promo_code=applied)
    await state.set_state(BuyerFlow.waiting_receipt)
    await state.update_data(auth=auth)
    await safe_edit(
        call.message,
        f"💎 <b>پرداخت ارزی (USDT شبکه TRC20)</b>\n\n"
        f"لطفاً دقیقاً مبلغ <b>{final_usdt:g} USDT</b> (خالص پس از کسر کارمزد برداشت صرافی) را به آدرس کیف پول زیر واریز کنید:\n\n"
        f"<code>{wallet_info}</code>\n\n"
        f"🔎 <b>پس از واریز، هش تراکنش (TXID / Hash) یا تصویر/PDF رسید را مستقیماً در همین چت بفرستید:</b>\n"
        f"(هش ۶۴ رقمی TRC20 به صورت خودکار روی بلاک‌چین بررسی و تایید می‌شود)\n"
        f"کد سفارش شما: <code>{auth}</code>",
        reply_markup=cancel_kb(f"buy:{plan_id}")
    )

@router.callback_query(F.data.startswith("pay_stars:"))
async def cb_pay_stars(call: CallbackQuery, state: FSMContext):
    await state.clear()
    parts = call.data.split(":")
    plan_id = int(parts[1])
    promo = parts[2] if len(parts) > 2 else ""
    plan = db.get_plan(plan_id)
    if not plan or not plan.get("stars_enabled", 1):
        await call.answer("پرداخت با استارز برای این کانال غیرفعال است.", show_alert=True)
        return
    _, _, final_stars, applied = _calc_final_amounts(plan, call.from_user.id, promo)
    auth = f"STR-{uuid.uuid4().hex[:10].upper()}"
    db.create_payment(auth, call.from_user.id, plan["chat_id"], plan_id, final_stars, "stars", promo_code=applied)
    await call.answer()
    inv_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⭐️ پرداخت {final_stars} ستاره", pay=True)],
        [InlineKeyboardButton(text="🔙 انصراف و بازگشت", callback_data="del_inv")]
    ])
    await call.bot.send_invoice(
        chat_id=call.from_user.id,
        title=f"اشتراک {plan['name']}",
        description=f"عضویت ویژه در کانال «{plan['channel_title']}» به مدت {plan['duration_days']} روز",
        payload=auth,
        currency="XTR",
        prices=[LabeledPrice(label=plan["name"], amount=final_stars)],
        reply_markup=inv_kb
    )

@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery):
    payload = query.invoice_payload
    now = int(time.time())
    if payload.startswith("PSTR-"):
        pp = db.get_platform_payment(payload)
        if not pp or pp["status"] == "paid" or (now - pp["created_at"]) > 900:
            await query.answer(ok=False, error_message="این فاکتور منقضی شده یا قبلاً پرداخت شده است.")
            return
        await query.answer(ok=True)
        return

    payment = db.get_payment(payload)
    if not payment or payment["status"] == "paid" or (now - payment["created_at"]) > 900:
        await query.answer(ok=False, error_message="این فاکتور منقضی شده (بیش از ۱۵ دقیقه) یا قبلاً پرداخت شده است.")
        return
    await query.answer(ok=True)

@router.message(F.successful_payment)
async def on_successful_stars_payment(message: Message):
    sp = message.successful_payment
    auth = sp.invoice_payload
    charge_id = sp.telegram_payment_charge_id

    if auth.startswith("PSTR-"):
        paid_pp = db.mark_platform_payment_paid(auth, charge_id)
        if paid_pp:
            await notify_platform_upgrade_success(paid_pp)
        return

    paid = db.mark_payment_paid(auth, charge_id)
    if paid:
        await issue_invite_link(paid["user_id"], paid["chat_id"], message.bot)

@router.callback_query(F.data.startswith("c2c:"))
async def cb_card_to_card(call: CallbackQuery, state: FSMContext):
    parts = call.data.split(":")
    plan_id = int(parts[1])
    promo = parts[2] if len(parts) > 2 else ""
    plan = db.get_plan(plan_id)
    if not plan:
        return
    await call.answer()
    sell_toman, _, _, applied = _calc_final_amounts(plan, call.from_user.id, promo)
    card_info = html.escape(plan["card_number"] or "شماره کارتی توسط ادمین ثبت نشده است")
    auth = f"C2C-{uuid.uuid4().hex[:8].upper()}"
    db.create_payment(auth, call.from_user.id, plan["chat_id"], plan_id, sell_toman, "card", promo_code=applied)
    await state.set_state(BuyerFlow.waiting_receipt)
    await state.update_data(auth=auth)
    await safe_edit(
        call.message,
        f"💳 لطفاً مبلغ <b>{sell_toman:,} تومان</b> را به شماره کارت زیر واریز کنید:\n\n"
        f"<code>{card_info}</code>\n\n"
        f"📸 <b>همین الان عکس یا فایل PDF رسید بانکی را مستقیماً در همین چت بفرستید:</b>\n"
        f"کد سفارش شما: <code>{auth}</code>",
        reply_markup=cancel_kb(f"buy:{plan_id}")
    )

@router.message(StateFilter(BuyerFlow.waiting_receipt))
@router.message(StateFilter(None), F.photo | F.document)
@router.message(StateFilter(None), F.reply_to_message)
async def handle_receipt_submission(message: Message, state: FSMContext):
    if message.text and message.text.strip() in ALL_MENU_BUTTONS:
        await state.clear()
        db.cancel_abandoned_initiations(message.from_user.id)
        return

    data = await state.get_data()
    auth = data.get("auth")

    if not auth and message.reply_to_message and message.reply_to_message.text:
        orig = message.reply_to_message.text
        if "کد سفارش شما:" in orig:
            auth = orig.split("کد سفارش شما:")[-1].strip()

    if not auth:
        pending = db.get_latest_pending_manual(message.from_user.id)
        if pending:
            auth = pending["authority"]

    if not auth:
        return

    payment = db.get_payment(auth)
    if not payment or payment["status"] in ("paid", "submitted"):
        await state.clear()
        await message.answer("این سفارش قبلاً ثبت و ارسال شده است.")
        return

    plan = db.get_plan(payment["plan_id"])
    raw_txt = (message.text or message.caption or "").strip()
    photo_id = message.photo[-1].file_id if message.photo else ""
    doc_id = message.document.file_id if message.document else ""

    if payment["method"] == "crypto" and len(raw_txt) == 64 and " " not in raw_txt:
        expected_usdt = float(payment.get("amount_usdt") or plan.get("price_usdt", 0))
        ok_chain, chain_msg = await gateways.verify_trc20_txid(
            raw_txt, plan.get("crypto_wallet", ""), expected_usdt, message.from_user.id
        )
        if ok_chain:
            await state.clear()
            paid = db.mark_payment_paid(auth, raw_txt.lower())
            if paid:
                await issue_invite_link(paid["user_id"], paid["chat_id"], message.bot)
                with suppress(Exception):
                    await message.bot.send_message(
                        plan["owner_id"],
                        f"✅ <b>واریز کریپتو به صورت خودکار تایید شد</b>\n"
                        f"کانال: «{html.escape(plan['channel_title'])}»\n"
                        f"خریدار: <code>{payment['user_id']}</code> | {chain_msg}\n"
                        f"TXID: <code>{html.escape(raw_txt)}</code>",
                        parse_mode="HTML"
                    )
            return
        elif "قبلاً در سیستم ثبت" in chain_msg:
            await message.answer(chain_msg)
            return

    await state.clear()
    db.mark_payment_submitted(auth, raw_txt, photo_id, doc_id)

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ تایید و ارسال لینک دعوت", callback_data=f"apr:{auth}"),
        InlineKeyboardButton(text="❌ رد رسید", callback_data=f"rej:{auth}")
    ]])
    user_label = f"@{message.from_user.username}" if message.from_user.username else f"<code>{payment['user_id']}</code>"
    method_badge = "💎 کریپتو (USDT)" if payment["method"] == "crypto" else "💳 کارت‌به‌کارت"
    amount_display = f"{payment.get('amount_usdt', 0):g} USDT" if payment["method"] == "crypto" else f"{payment['amount_toman']:,} تومان"
    promo_note = f"\n🎟 کد تخفیف: <code>{payment['promo_code']}</code>" if payment.get("promo_code") else ""

    caption_text = (
        f"🔔 <b>رسید {method_badge} جدید</b>\n"
        f"📢 کانال: «{html.escape(plan['channel_title'])}»\n"
        f"👤 خریدار: {user_label}\n"
        f"📦 پلن: {html.escape(plan['name'])} ({amount_display}){promo_note}\n"
        f"🔖 کد سفارش: <code>{auth}</code>\n"
        f"💬 پیام / هش کاربر: <code>{html.escape(raw_txt or '[فایل رسید]')}</code>"
    )
    if photo_id:
        await message.bot.send_photo(plan["owner_id"], photo_id, caption=caption_text, reply_markup=kb, parse_mode="HTML")
    elif doc_id:
        await message.bot.send_document(plan["owner_id"], doc_id, caption=caption_text, reply_markup=kb, parse_mode="HTML")
    else:
        await message.bot.send_message(plan["owner_id"], caption_text, reply_markup=kb, parse_mode="HTML")

    await message.answer("✅ رسید شما دریافت و برای مدیر کانال ارسال شد. به محض تایید، لینک ورود اختصاصی برایتان ارسال می‌شود.")

@router.callback_query(F.data.startswith("apr:"))
async def cb_approve_manual(call: CallbackQuery):
    auth = call.data.split(":")[1]
    paid = db.mark_payment_paid(auth, "MANUAL-APPROVED", expected_owner_id=call.from_user.id)
    if not paid:
        await call.answer("عدم دسترسی یا خطا در تایید سفارش.", show_alert=True)
        return
    await call.answer("تایید شد.")
    await issue_invite_link(paid["user_id"], paid["chat_id"], call.bot)
    with suppress(TelegramBadRequest):
        if call.message.photo or call.message.document:
            await call.message.edit_caption(caption=f"✅ سفارش {auth} تایید شد و لینک یک‌بارمصرف برای خریدار ارسال گردید.")
        else:
            await call.message.edit_text(f"✅ سفارش {auth} تایید شد و لینک یک‌بارمصرف برای خریدار ارسال گردید.")

@router.callback_query(F.data.startswith("rej:"))
async def cb_reject_manual(call: CallbackQuery):
    auth = call.data.split(":")[1]
    payment = db.mark_payment_rejected(auth, expected_owner_id=call.from_user.id)
    if not payment:
        await call.answer("عدم دسترسی.", show_alert=True)
        return
    await call.answer("رد شد.")
    with suppress(Exception):
        await call.bot.send_message(payment["user_id"], f"❌ رسید سفارش <code>{auth}</code> توسط ادمین تایید نشد.", parse_mode="HTML")
    with suppress(TelegramBadRequest):
        if call.message.photo or call.message.document:
            await call.message.edit_caption(caption=f"❌ سفارش {auth} رد شد.")
        else:
            await call.message.edit_text(f"❌ سفارش {auth} رد شد.")

async def check_and_nudge_admin_quota(owner_id: int, current_bot: Bot):
    qs = db.get_admin_quota_status(owner_id)
    if qs["quota"] > 100000:
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 ارتقای پلن ربات (بدون توقف فروش)", callback_data="saas_hub")]
    ])
    if qs["remaining"] == 2:
        with suppress(Exception):
            await current_bot.send_message(
                owner_id,
                f"🎉 <b>تبریک! شما تا این لحظه {qs['used']} فروش موفق در این ماه ثبت کردید.</b>\n\n"
                f"⚠️ تنها <b>۲ سفارش دیگر</b> از ظرفیت پلن فعلی شما ({qs['tier_title']}) باقی مانده است.\n"
                f"برای اینکه فروش خودکار کانال شما قفل نشود، همین الان پلن خود را ارتقا دهید:",
                reply_markup=kb,
                parse_mode="HTML"
            )
    elif qs["remaining"] == 0:
        with suppress(Exception):
            await current_bot.send_message(
                owner_id,
                f"🚨 <b>سقف سفارش‌های ماهانه پلن شما ({qs['quota']} سفارش) تکمیل شد!</b>\n\n"
                f"از این لحظه خریداران جدید نمی‌توانند سفارش ثبت کنند تا زمانی که پلن خود را ارتقا دهید:",
                reply_markup=kb,
                parse_mode="HTML"
            )

async def issue_invite_link(user_id: int, chat_id: int, current_bot: Bot, count_quota: bool = True):
    ch = db.get_channel(chat_id)
    owner_id = ch["owner_id"] if ch else 0
    footer = await get_viral_footer(owner_id, current_bot) if owner_id else ""
    try:
        link = await current_bot.create_chat_invite_link(
            chat_id=chat_id,
            creates_join_request=True,
            expire_date=int(time.time()) + 600
        )
        grp_part = ""
        if ch and ch.get("linked_group_id"):
            with suppress(Exception):
                g_link = await current_bot.create_chat_invite_link(
                    chat_id=ch["linked_group_id"],
                    creates_join_request=True,
                    expire_date=int(time.time()) + 600
                )
                grp_part = f"\n\n👥 <b>لینک ورود به گروه گفتگوی VIP ({html.escape(ch['linked_group_title'])}):</b>\n{g_link.invite_link}"

        await current_bot.send_message(
            user_id,
            f"🎉 <b>لینک ورود اختصاصی شما آماده است!</b>\n\n"
            f"🔗 <b>لینک کانال VIP</b> (معتبر به مدت ۱۰ دقیقه و فقط برای اکانت شما):\n"
            f"{link.invite_link}{grp_part}{footer}",
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    except Exception as e:
        await current_bot.send_message(
            user_id,
            f"تراکنش ثبت شد اما ربات نتوانست لینک بسازد (دسترسی ادمین بررسی شود). خطا: {html.escape(str(e))}",
            parse_mode="HTML"
        )
    if owner_id and count_quota:
        await check_and_nudge_admin_quota(owner_id, current_bot)

@router.chat_join_request()
async def verify_join_request(update: ChatJoinRequest):
    if db.is_subscription_active(update.from_user.id, update.chat.id):
        with suppress(Exception):
            await update.bot.unban_chat_member(chat_id=update.chat.id, user_id=update.from_user.id, only_if_banned=True)
        await update.approve()
        await update.bot.send_message(update.from_user.id, f"✅ عضویت شما در «{html.escape(update.chat.title or 'VIP')}» تایید شد.", parse_mode="HTML")
    else:
        await update.decline()
        await update.bot.send_message(
            update.from_user.id,
            "⛔ شما اشتراک فعالی برای این کانال ندارید. لینک‌های دعوت اختصاصی بوده و قابل انتقال به دیگران نیستند."
        )

async def expiration_worker():
    last_backup_ts = 0
    while True:
        try:
            now = int(time.time())
            admin_id = get_platform_admin_id()
            if admin_id > 0 and (now - last_backup_ts) >= 86400:
                b_path = f"backup_daily_{now}.db"
                try:
                    db.backup_database_to_file(b_path)
                    await bot.send_document(admin_id, FSInputFile(b_path), caption="💾 بکاپ خودکار روزانه دیتابیس VIP SaaS")
                    last_backup_ts = now
                except Exception:
                    pass
                finally:
                    if os.path.exists(b_path):
                        os.remove(b_path)

            for rem in db.get_due_reminders():
                uid, cid = rem["user_id"], rem["chat_id"]
                footer = await get_viral_footer(rem["owner_id"], bot)
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 تمدید آنی اشتراک (بدون قطعی)", callback_data=f"renew_ch:{cid}")]
                ])
                try:
                    await bot.send_message(
                        uid,
                        f"⏳ <b>یادآوری پایان اشتراک</b>\n\n"
                        f"کمتر از ۲۴ ساعت به پایان اشتراک شما در کانال «<b>{html.escape(rem['channel_title'])}</b>» باقی مانده است.\n"
                        f"جهت جلوگیری از حذف خودکار، از دکمه زیر اشتراک خود را تمدید کنید:{footer}",
                        reply_markup=kb,
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )
                except Exception:
                    pass
                db.mark_reminded(uid, cid)
                await asyncio.sleep(0.05)

            for sub in db.get_due_expirations():
                uid, cid = sub["user_id"], sub["chat_id"]
                gid = sub.get("linked_group_id") or 0
                footer = await get_viral_footer(sub["owner_id"], bot)
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 خرید مجدد و بازگشت به کانال", callback_data=f"renew_ch:{cid}")]
                ])
                try:
                    await bot.ban_chat_member(chat_id=cid, user_id=uid)
                    await bot.unban_chat_member(chat_id=cid, user_id=uid, only_if_banned=True)
                    if gid:
                        with suppress(Exception):
                            await bot.ban_chat_member(chat_id=gid, user_id=uid)
                            await bot.unban_chat_member(chat_id=gid, user_id=uid, only_if_banned=True)
                    await bot.send_message(
                        uid,
                        f"⛔ مهلت اشتراک VIP شما به پایان رسید و به صورت خودکار از کانال خارج شدید.\n"
                        f"هر زمان مایل بودید می‌توانید با دکمه زیر مجدداً اشتراک تهیه کنید:{footer}",
                        reply_markup=kb,
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )
                except Exception:
                    pass
                db.mark_subscription_expired(uid, cid)
                await asyncio.sleep(0.05)
        except Exception:
            pass
        await asyncio.sleep(60)

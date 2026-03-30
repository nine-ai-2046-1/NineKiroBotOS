import os
import re
import sys
import asyncio
import subprocess
import logging
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)

load_dotenv()

BOT_NAME = os.environ["BOT_NAME"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
ALLOWED_USER_ID = int(os.environ["ALLOWED_USER_ID"])
ALLOWED_GROUP_IDS = set(
    int(x.strip()) for x in os.environ.get("ALLOWED_GROUP_IDS", "").split(",") if x.strip()
)
ALLOWED_CHANNEL_IDS = set(
    int(x.strip()) for x in os.environ.get("ALLOWED_CHANNEL_IDS", "").split(",") if x.strip()
)
CC_CHANNEL_IDS = [
    int(x.strip()) for x in os.environ.get("CC_CHANNEL_IDS", "").split(",") if x.strip()
]
HANDLE_IMAGE = os.environ.get("HANDLE_IMAGE", "true").strip().lower() == "true"
HANDLE_FILE = os.environ.get("HANDLE_FILE", "true").strip().lower() == "true"
AGENT_WORK_DIR = os.environ.get("AGENT_WORK_DIR", "").strip() or None

BASE_DIR = Path(__file__).parent / "sessions"
BASE_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Suppress noisy HTTP request logs from telegram/httpx
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

# ── Security ──────────────────────────────────────────────────────────────────

def is_authorized(update: Update) -> bool:
    """Only allow the whitelisted user in DM, or whitelisted groups/channels."""
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if chat.type == "private":
        if user is None or user.id != ALLOWED_USER_ID:
            return False
        return True
    if chat.type in ("group", "supergroup"):
        if user is None or user.id != ALLOWED_USER_ID:
            return False
        return chat.id in ALLOWED_GROUP_IDS
    if chat.type == "channel":
        # Channel posts: allow if bot is mentioned
        return chat.id in ALLOWED_CHANNEL_IDS

    return False

MAX_PROMPT_LEN = 4000  # chars

def sanitize(text: str) -> str:
    """Strip control characters and limit length to prevent injection."""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text).strip()
    return text[:MAX_PROMPT_LEN]

def safe_key(value: str) -> str:
    """Allow only alphanumeric, dash, underscore to prevent path traversal."""
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", str(value))

def strip_ansi(text: str) -> str:
    """Remove ANSI terminal escape codes and kiro-cli prompt prefix from output."""
    text = re.sub(r"\x1b\[[0-9;]*[mGKHF]", "", text)
    # Remove leading "> " prompt that kiro-cli prepends to responses
    text = re.sub(r"^> ", "", text, flags=re.MULTILINE)
    return text.strip()

# ── Session directory ─────────────────────────────────────────────────────────

AGENT_SRC = Path(__file__).parent / ".kiro"
AGENT_NAME = "kiro-tg"  # overridden by --agent param at startup

def session_dir(update: Update) -> Path:
    # If AGENT_WORK_DIR is set, all sessions share that directory
    if AGENT_WORK_DIR:
        d = Path(AGENT_WORK_DIR)
        d.mkdir(parents=True, exist_ok=True)
        link = d / ".kiro"
        if not link.exists():
            link.symlink_to(AGENT_SRC, target_is_directory=True)
        return d
    chat = update.effective_chat
    msg = update.effective_message
    if chat.type == "private":
        key = f"user_{safe_key(chat.id)}"
    elif chat.type in ("group", "supergroup"):
        thread_id = msg.message_thread_id if msg and msg.message_thread_id else "main"
        key = f"group_{safe_key(chat.id)}_topic_{safe_key(thread_id)}"
    else:
        key = f"channel_{safe_key(chat.id)}"
    d = BASE_DIR / key
    # Guard against path traversal
    d = d.resolve()
    assert str(d).startswith(str(BASE_DIR.resolve())), "Path traversal detected"
    d.mkdir(exist_ok=True)
    link = d / ".kiro"
    if not link.exists():
        link.symlink_to(AGENT_SRC, target_is_directory=True)
    return d

# ── Kiro CLI call ─────────────────────────────────────────────────────────────

AGENT_DIR = Path(__file__).parent  # .kiro/agents/ lives here

def extract_final_response(output: str) -> tuple[str, str]:
    """Returns (progress, final_reply)."""
    if "===FINAL===" in output and "===END===" in output:
        parts = output.split("===FINAL===", 1)
        progress = parts[0].strip()
        final = parts[1].split("===END===", 1)[0].strip()
        return progress, final

    # Fallback: take last paragraph (after last blank line) as final reply
    # Skip lines that look like tool logs
    tool_line = re.compile(
        r'(using tool:|I will run|I\'ll create|Creating:|Completed in|Purpose:|^\+\s+\d+:)'
    )
    lines = output.strip().splitlines()
    # Find last block of non-tool lines
    final_lines = []
    for line in reversed(lines):
        if not line.strip():
            if final_lines:
                break
            continue
        if tool_line.search(line):
            if final_lines:
                break
            continue
        final_lines.append(line)

    if final_lines:
        final = "\n".join(reversed(final_lines)).strip()
        progress = output.strip()
        return progress, final

    return output.strip(), ""


async def ask_kiro(prompt: str, cwd: Path, resume: bool, thinking_msg=None) -> tuple:
    cmd = [
        "kiro-cli", "chat",
        "--no-interactive",
        "--trust-all-tools",
        "--agent", AGENT_NAME,
    ]
    if resume:
        cmd.append("-r")
    cmd.append(prompt)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        output_lines = []
        last_edit = 0

        async def read_stream():
            nonlocal last_edit
            import time
            buffer = b""
            while True:
                chunk = await proc.stdout.read(256)
                if not chunk:
                    break
                buffer += chunk
                # Process complete lines
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    text = strip_ansi(line.decode("utf-8", errors="replace")).strip()
                    if text:
                        output_lines.append(text)
                        # Update thinking message with last 20 lines, throttle to 2s
                        now = time.time()
                        if thinking_msg and (now - last_edit) >= 2:
                            last_edit = now
                            await update_thinking(thinking_msg, "\n".join(output_lines[-20:]))
            # flush remaining buffer
            if buffer:
                text = strip_ansi(buffer.decode("utf-8", errors="replace")).strip()
                if text:
                    output_lines.append(text)

        await asyncio.wait_for(
            asyncio.gather(read_stream(), proc.wait()),
            timeout=10800
        )

        return extract_final_response("\n".join(output_lines))

    except asyncio.TimeoutError:
        return "\n".join(output_lines), f"⚠️ {BOT_NAME} 超時（>3小時），請再試。"
    except Exception as e:
        log.error("kiro error: %s", e)
        return "", f"⚠️ 呼叫 {BOT_NAME} 出錯：{e}"

# ── Helpers ───────────────────────────────────────────────────────────────────

async def delete_latest_session(cwd: Path) -> bool:
    """Delete the most recent kiro-cli session for this cwd."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "kiro-cli", "chat", "--list-sessions",
            cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        output = strip_ansi(stdout.decode())
        match = re.search(r"Chat SessionId:\s+([a-f0-9\-]{36})", output)
        if match:
            proc2 = await asyncio.create_subprocess_exec(
                "kiro-cli", "chat", "--delete-session", match.group(1),
                cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            await proc2.communicate()
            return True
    except Exception as e:
        log.error("delete_session error: %s", e)
    return False

# ── Command handlers ──────────────────────────────────────────────────────────

async def update_thinking(thinking_msg, progress: str):
    """Update thinking message with progress, fallback to plain text if Markdown fails."""
    preview = progress[-3000:]
    for text in [f"```\n{preview}\n```", preview]:
        try:
            kwargs = {"parse_mode": "Markdown"} if text.startswith("```") else {}
            await thinking_msg.edit_text(f"📋 進行中...\n{text}", **kwargs)
            return
        except Exception:
            continue

async def send_long(update: Update, text: str):
    """Telegram max message length is 4096; split if needed."""
    for i in range(0, len(text), 4096):
        await update.effective_message.reply_text(text[i:i+4096])

# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    await update.message.reply_text(
        f"👋 {BOT_NAME} Bot 已就緒！\n\n"
        "指令：\n"
        "  /new   — 開新對話（清除上下文）\n"
        "  /info  — 顯示目前 Session 資料\n"
        "  /help  — 顯示此說明\n\n"
        f"直接發訊息即可同 {BOT_NAME} 傾偈。\n"
        "程式碼檔案會建立喺伺服器嘅 Session 資料夾。"
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    await cmd_start(update, ctx)

async def cmd_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    cwd = session_dir(update)
    deleted = await delete_latest_session(cwd)
    if deleted:
        await update.message.reply_text("🗑️ Session 已清除，下一條訊息將開始新對話。")
    else:
        await update.message.reply_text("ℹ️ 冇搵到現有 session，下一條訊息會自動開新對話。")

async def cmd_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    cwd = session_dir(update)
    chat = update.effective_chat
    await update.message.reply_text(
        f"📁 Session 資料夾：`sessions/{cwd.name}`\n"
        f"💬 對話類型：{chat.type}\n"
        f"🔑 Chat ID：`{chat.id}`",
        parse_mode="Markdown"
    )

async def cmd_cc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Send a message to all CC_CHANNEL_IDS. Usage: /cc <message>"""
    if not is_authorized(update):
        return
    if not ctx.args:
        await update.message.reply_text("用法：`/cc <訊息內容>`", parse_mode="Markdown")
        return
    if not CC_CHANNEL_IDS:
        await update.message.reply_text("⚠️ 未設定 CC_CHANNEL_IDS，請喺 .env 填入。")
        return

    text = sanitize(" ".join(ctx.args))
    if not text:
        await update.message.reply_text("⚠️ 訊息內容唔可以為空。")
        return

    success, failed = 0, 0
    for cid in CC_CHANNEL_IDS:
        try:
            await ctx.bot.send_message(chat_id=cid, text=text)
            success += 1
        except Exception as e:
            log.error("cc send failed to %s: %s", cid, e)
            failed += 1

    await update.message.reply_text(
        f"✅ 已發送到 {success} 個 channel" +
        (f"，{failed} 個失敗" if failed else "")
    )

async def on_image(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    msg = update.effective_message
    if not msg or not msg.photo:
        return

    cwd = session_dir(update)
    photo = msg.photo[-1]  # highest resolution
    tg_file = await ctx.bot.get_file(photo.file_id)
    img_path = cwd / f"photo_{photo.file_id}.jpg"
    await tg_file.download_to_drive(img_path)

    caption = sanitize(msg.caption or "")
    prompt = f"幫我分析呢張圖片：{img_path}" + (f"\n{caption}" if caption else "")

    user = update.effective_user
    chat = update.effective_chat
    sender_name = f"@{user.username}" if user and user.username else str(user.id if user else "unknown")
    log.info("[%s %s] %s sent image: %s", chat.type, chat.id, sender_name, img_path)

    thinking = await msg.reply_text("⏳ 諗緊...")
    progress, final = await ask_kiro(prompt, cwd, True, thinking_msg=thinking)

    if progress:
        await update_thinking(thinking, progress)

    # Send final reply as new message
    reply = final if final else progress or "(冇回應)"
    log.info("[%s %s] bot → %s: %s", chat.type, chat.id, sender_name, reply[:100])
    await send_long(update, reply)


async def on_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    msg = update.effective_message
    if not msg or not msg.document:
        return

    cwd = session_dir(update)
    doc = msg.document
    tg_file = await ctx.bot.get_file(doc.file_id)
    file_path = cwd / doc.file_name
    await tg_file.download_to_drive(file_path)

    caption = sanitize(msg.caption or "")
    prompt = f"我傳咗一個檔案俾你：{file_path}" + (f"\n{caption}" if caption else "\n請分析或處理呢個檔案。")

    user = update.effective_user
    chat = update.effective_chat
    sender_name = f"@{user.username}" if user and user.username else str(user.id if user else "unknown")
    log.info("[%s %s] %s sent file: %s", chat.type, chat.id, sender_name, file_path)

    thinking = await msg.reply_text("⏳ 諗緊...")
    progress, final = await ask_kiro(prompt, cwd, True, thinking_msg=thinking)
    if progress:
        await update_thinking(thinking, progress)
    reply = final if final else progress or "(冇回應)"
    log.info("[%s %s] bot → %s: %s", chat.type, chat.id, sender_name, reply[:100])
    await send_long(update, reply)


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message

    if not msg or not msg.text:
        return

    # ── Channel post handling ─────────────────────────────────────────────────
    if chat and chat.type == "channel" and chat.id in ALLOWED_CHANNEL_IDS:
        bot_username = ctx.bot.username
        mentioned = bot_username and f"@{bot_username}" in msg.text
        sender = (
            msg.sender_chat.username or msg.sender_chat.title
            if msg.sender_chat else
            (msg.from_user.username or str(msg.from_user.id) if msg.from_user else "unknown")
        )
        if not mentioned:
            log.info("[channel %s] @%s: %s", chat.id, sender, msg.text[:100])
            return
        # Bot is mentioned — log and respond
        log.info("[channel %s] @%s mentioned bot: %s", chat.id, sender, msg.text[:100])
        prompt = sanitize(msg.text.replace(f"@{bot_username}", "").strip())
        if not prompt:
            return
        cwd = session_dir(update)
        thinking = await ctx.bot.send_message(chat_id=chat.id, text="⏳ 諗緊...")
        progress, final = await ask_kiro(prompt, cwd, True, thinking_msg=thinking)
        if progress:
            await update_thinking(thinking, progress)
        reply = final if final else progress or "(冇回應)"
        for i in range(0, len(reply), 4096):
            chunk = reply[i:i+4096]
            await ctx.bot.send_message(chat_id=chat.id, text=chunk)
            log.info("[channel %s] bot → @%s: %s", chat.id, sender, chunk[:100])
        return

    # ── Normal DM / Group handling ────────────────────────────────────────────
    if not is_authorized(update):
        if update.effective_user is not None:
            log.warning("Blocked unauthorized access from user=%s chat=%s",
                        update.effective_user.id,
                        chat and chat.id)
        return

    if not msg.text:
        return

    prompt = sanitize(msg.text)
    if not prompt:
        return

    cwd = session_dir(update)
    user = update.effective_user
    sender_name = (
        f"@{user.username}" if user and user.username else
        user.first_name if user and user.first_name else
        str(user.id) if user else "unknown"
    )
    log.info("[%s %s] %s: %s", chat.type, chat.id, sender_name, prompt[:100])

    thinking = await msg.reply_text("⏳ 諗緊...")
    progress, final = await ask_kiro(prompt, cwd, True, thinking_msg=thinking)

    if progress:
        await update_thinking(thinking, progress)

    reply = final if final else progress or "(冇回應)"
    log.info("[%s %s] bot → %s: %s", chat.type, chat.id, sender_name, reply[:100])
    await send_long(update, reply)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Kiro Telegram Bot")
    parser.add_argument("--agent", default="kiro-tg", help="kiro-cli agent name (default: kiro-tg)")
    args = parser.parse_args()

    # Validate agent name: alphanumeric + dash/underscore only
    if not re.fullmatch(r"[a-zA-Z0-9_\-]+", args.agent):
        raise ValueError(f"Invalid agent name: {args.agent!r}")

    global AGENT_NAME
    AGENT_NAME = args.agent
    log.info("Using agent: %s", AGENT_NAME)
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("info", cmd_info))
    app.add_handler(CommandHandler("cc", cmd_cc))

    # Only handle text messages (no forwarded, no media) to reduce injection surface
    app.add_handler(MessageHandler(
        (filters.TEXT & ~filters.COMMAND & ~filters.FORWARDED) |
        filters.ChatType.CHANNEL,
        on_message
    ))

    if HANDLE_IMAGE:
        app.add_handler(MessageHandler(filters.PHOTO & ~filters.FORWARDED, on_image))

    if HANDLE_FILE:
        app.add_handler(MessageHandler(filters.Document.ALL & ~filters.FORWARDED, on_file))

    # Health check every hour
    async def health_check(ctx):
        log.info("✅ Bot alive | agent=%s", AGENT_NAME)

    app.job_queue.run_repeating(health_check, interval=3600, first=10)

    log.info("Bot started.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    import time
    auto_restart = os.environ.get("AUTO_RESTART", "true").strip().lower() != "false"

    while True:
        try:
            main()
        except (KeyboardInterrupt, SystemExit):
            log.info("Bot stopped by user.")
            break
        except Exception as e:
            if not auto_restart:
                log.error("Bot crashed: %s", e)
                break
            log.error("Bot crashed: %s — restarting in 5s...", e)
            time.sleep(5)

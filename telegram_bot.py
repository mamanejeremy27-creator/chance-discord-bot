"""
ChanceBot on Telegram (@ChanceFunOfficialBot): runs Chance 101 in private chats.

    TELEGRAM_BOT_TOKEN=... python telegram_bot.py

It runs on its own, separately from the Discord bot (bot.py), and uses the same slides.
The screens are built in telegram_course.py. In a group the bot never shows the course;
it answers with a button that opens it in a private chat.

Set TELEGRAM_SYSTEM_CERTS=1 to trust the system's certificates (needs the truststore
package), e.g. on a PC whose antivirus inspects HTTPS.
"""

import logging
import os

from dotenv import load_dotenv
from telegram import (BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto,
                      Message, Update)
from telegram.constants import ChatType, ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

import telegram_course as course

log = logging.getLogger("chancebot")

DESCRIPTION = ("ChanceBot is the official CHANCE.fun bot. Start Chance 101 to learn how Chance works in "
               "13 short episodes, one slide at a time, each with a one-question quiz.\n\n"
               "The only official ChanceBot is @{username}. Chance never DMs first.\n"
               "18+ · Only where permitted")
SHORT_DESCRIPTION = "The official CHANCE.fun bot. Learn how Chance works in 13 short episodes. 18+, only where permitted."
COMMANDS = [BotCommand("start", "Open Chance 101"),
            BotCommand("chance101", "Open Chance 101"),
            BotCommand("episodes", "Pick an episode")]

# Telegram's id for each slide once it has been uploaded, so later screens don't upload it again
FILE_IDS: dict = {}


class RedactToken(logging.Filter):
    """Keeps the bot token out of every log line and traceback."""

    def __init__(self, token: str):
        super().__init__()
        self.token = token

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if self.token in message:
            record.msg, record.args = message.replace(self.token, "<token>"), ()
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info).replace(self.token, "<token>")
        return True


def _markup(rows: list) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, url=value) if value.startswith("https://")
         else InlineKeyboardButton(label, callback_data=value) for label, value in row]
        for row in rows])


def _photo(name: str):
    # The file's bytes, not its path: InputMediaPhoto turns a path into a file:// link,
    # which only a self-hosted Bot API server accepts
    return FILE_IDS.get(name) or (course.ASSET_DIR / name).read_bytes()


def _remember(name: str, message) -> None:
    if isinstance(message, Message) and message.photo:
        FILE_IDS[name] = message.photo[-1].file_id


async def _send(chat_id: int, screen: course.Screen, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await context.bot.send_photo(chat_id, photo=_photo(screen.photo), caption=screen.caption,
                                           parse_mode=ParseMode.HTML, reply_markup=_markup(screen.buttons))
    _remember(screen.photo, message)


async def _edit(update: Update, screen: course.Screen, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show a screen in place of the one whose button was pressed."""
    query = update.callback_query
    for attempt in range(2):
        media = InputMediaPhoto(_photo(screen.photo), caption=screen.caption, parse_mode=ParseMode.HTML)
        try:
            _remember(screen.photo, await query.edit_message_media(media=media, reply_markup=_markup(screen.buttons)))
            return
        except BadRequest as error:
            if "not modified" in str(error).lower():
                return
            if attempt == 0 and screen.photo in FILE_IDS:
                FILE_IDS.pop(screen.photo)  # a stale id: upload the file instead
                continue
            log.warning("Couldn't edit, sending a new message instead: %s", error)
            break
    await _send(update.effective_chat.id, screen, context)


async def _open_in_private(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """In a group: one short reply with a button, so the course never fills the group."""
    link = f"https://t.me/{context.bot.username}?start=go"
    await update.effective_message.reply_text(
        "Chance 101 runs in a private chat with me, so it doesn't fill up the group.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Open Chance 101", url=link)]]))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start (with an optional deep-link payload) and /chance101."""
    if update.effective_chat.type != ChatType.PRIVATE:
        await _open_in_private(update, context)
        return
    payload = context.args[0] if context.args else ""
    await _send(update.effective_chat.id, course.from_start(payload), context)


async def episodes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != ChatType.PRIVATE:
        await _open_in_private(update, context)
        return
    await _send(update.effective_chat.id, course.episodes(), context)


async def other_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Anything typed in the private chat: show the start screen again."""
    await _send(update.effective_chat.id, course.welcome(), context)


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    screen = course.from_data(query.data)
    if screen is not None and update.effective_chat and update.effective_chat.type == ChatType.PRIVATE:
        await _edit(update, screen, context)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Update failed: %s", context.error, exc_info=context.error)


async def post_init(app: Application) -> None:
    me = await app.bot.get_me()
    try:
        await app.bot.set_my_commands(COMMANDS)
        await app.bot.set_my_description(DESCRIPTION.format(username=me.username))
        await app.bot.set_my_short_description(SHORT_DESCRIPTION)
    except TelegramError as error:
        log.warning("Couldn't update the bot's commands or description: %s", error)
    missing = course.missing_files()
    if missing:
        log.error("Chance 101 files missing from %s: %s", course.ASSET_DIR, ", ".join(missing[:5]))
    log.info("ChanceBot ready as @%s: %d slides, %d quizzes", me.username,
             course.TOTAL_SLIDES - len(missing), len(course.QUIZZES))


def build_app(token: str) -> Application:
    app = Application.builder().token(token).post_init(post_init).build()
    app.add_handler(CommandHandler(["start", "chance101"], start))
    app.add_handler(CommandHandler("episodes", episodes))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, other_text))
    app.add_error_handler(on_error)
    return app


def main() -> None:
    load_dotenv()
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)  # they log request URLs, which hold the token
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")
    for handler in logging.getLogger().handlers:
        handler.addFilter(RedactToken(token))
    if os.getenv("TELEGRAM_SYSTEM_CERTS") == "1":
        import truststore
        truststore.inject_into_ssl()
    build_app(token).run_polling(allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY])


if __name__ == "__main__":
    main()

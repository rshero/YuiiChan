import re, ast
from io import BytesIO
from typing import Optional

import tg_bot.modules.sql.notes_sql as sql
from tg_bot import log, dispatcher, SUDO_USERS
from tg_bot.modules.disable import DisableAbleCommandHandler
from tg_bot.modules.helper_funcs.chat_status import user_admin, connection_status
from tg_bot.modules.helper_funcs.misc import build_keyboard, revert_buttons
from tg_bot.modules.helper_funcs.msg_types import get_note_type
from tg_bot.modules.helper_funcs.string_handling import escape_invalid_curly_brackets
from telegram import (
    MAX_MESSAGE_LENGTH,
    InlineKeyboardMarkup,
    Message,
    ParseMode,
    Update,
    InlineKeyboardButton,
)
from telegram.error import BadRequest
from telegram.utils.helpers import escape_markdown, mention_markdown
from telegram.ext import (
    CallbackContext,
    CommandHandler,
    CallbackQueryHandler,
    Filters,
    MessageHandler,
)
from telegram.ext.dispatcher import run_async
from tg_bot.modules.helper_funcs.alternate import send_message

JOIN_LOGGER = None
FILE_MATCHER = re.compile(r"^###file_id(!photo)?###:(.*?)(?:\s|$)")
STICKER_MATCHER = re.compile(r"^###sticker(!photo)?###:")
BUTTON_MATCHER = re.compile(r"^###button(!photo)?###:(.*?)(?:\s|$)")
MYFILE_MATCHER = re.compile(r"^###file(!photo)?###:")
MYPHOTO_MATCHER = re.compile(r"^###photo(!photo)?###:")
MYAUDIO_MATCHER = re.compile(r"^###audio(!photo)?###:")
MYVOICE_MATCHER = re.compile(r"^###voice(!photo)?###:")
MYVIDEO_MATCHER = re.compile(r"^###video(!photo)?###:")
MYVIDEONOTE_MATCHER = re.compile(r"^###video_note(!photo)?###:")

ENUM_FUNC_MAP = {
    sql.Types.TEXT.value: dispatcher.bot.send_message,
    sql.Types.BUTTON_TEXT.value: dispatcher.bot.send_message,
    sql.Types.STICKER.value: dispatcher.bot.send_sticker,
    sql.Types.DOCUMENT.value: dispatcher.bot.send_document,
    sql.Types.PHOTO.value: dispatcher.bot.send_photo,
    sql.Types.AUDIO.value: dispatcher.bot.send_audio,
    sql.Types.VOICE.value: dispatcher.bot.send_voice,
    sql.Types.VIDEO.value: dispatcher.bot.send_video,
}

def private_get(context, update, notename, chat_id, u, m, show_none=False, no_format=False):
    bot = context.bot
    note = sql.get_note(chat_id, notename)
    message = m
    if note:
        reply_id = m.message_id

        if note.is_reply:
            if JOIN_LOGGER:
                try:
                    bot.forward_message(
                        chat_id=chat_id, from_chat_id=JOIN_LOGGER, message_id=note.value
                    )
                except BadRequest as excp:
                    if excp.message == "Message to forward not found":
                        message.reply_text(
                            "This message seems to have been lost - I'll remove it "
                            "from your notes list."
                        )
                        sql.rm_note(chat_id, notename)
                    else:
                        raise
            else:
                try:
                    bot.forward_message(
                        chat_id=chat_id, from_chat_id=chat_id, message_id=note.value
                    )
                except BadRequest as excp:
                    if excp.message == "Message to forward not found":
                        message.reply_text(
                            "Looks like the original sender of this note has deleted "
                            "their message - sorry! Get your bot admin to start using a "
                            "message dump to avoid this. I'll remove this note from "
                            "your saved notes."
                        )
                        sql.rm_note(chat_id, notename)
                    else:
                        raise
        else:
            VALID_NOTE_FORMATTERS = [
                "first",
                "last",
                "fullname",
                "username",
                "id",
                "chatname",
                "mention",
            ]
            valid_format = escape_invalid_curly_brackets(
                note.value, VALID_NOTE_FORMATTERS
            )
            if valid_format:
                text = valid_format.format(
                    first=escape_markdown(message.from_user.first_name),
                    last=escape_markdown(
                        message.from_user.last_name or message.from_user.first_name
                    ),
                    fullname=escape_markdown(
                        " ".join(
                            [message.from_user.first_name, message.from_user.last_name]
                            if message.from_user.last_name
                            else [message.from_user.first_name]
                        )
                    ),
                    username="@" + message.from_user.username
                    if message.from_user.username
                    else mention_markdown(
                        message.from_user.id, message.from_user.first_name
                    ),
                    mention=mention_markdown(
                        message.from_user.id, message.from_user.first_name
                    ),
                    chatname=escape_markdown(
                        message.chat.title
                        if message.chat.type != "private"
                        else message.from_user.first_name
                    ),
                    id=message.from_user.id,
                )
            else:
                text = ""

            keyb = []
            parseMode = ParseMode.MARKDOWN
            buttons = sql.get_buttons(chat_id, notename)
            if no_format:
                parseMode = None
                text += revert_buttons(buttons)
            else:
                keyb = build_keyboard(buttons)

            keyboard = InlineKeyboardMarkup(keyb)

            try:
                if note.msgtype in (sql.Types.BUTTON_TEXT, sql.Types.TEXT):
                    bot.send_message(
                        u,
                        text,
                        reply_to_message_id=reply_id,
                        parse_mode=parseMode,
                        reply_markup=keyboard,
                    )
                else:
                    if ENUM_FUNC_MAP[note.msgtype] == dispatcher.bot.send_sticker:
                        ENUM_FUNC_MAP[note.msgtype](
                            chat_id,
                            note.file,
                            reply_to_message_id=reply_id,
                            reply_markup=keyboard,
                        )
                    else:
                        ENUM_FUNC_MAP[note.msgtype](
                            chat_id,
                            note.file,
                            caption=text,
                            reply_to_message_id=reply_id,
                            parse_mode=parseMode,
                            reply_markup=keyboard,
                        )

            except BadRequest as excp:
                if excp.message == "Entity_mention_user_invalid":
                    message.reply_text(
                        "Looks like you tried to mention someone I've never seen before. If you really "
                        "want to mention them, forward one of their messages to me, and I'll be able "
                        "to tag them!"
                    )
                elif FILE_MATCHER.match(note.value):
                    message.reply_text(
                        "This note was an incorrectly imported file from another bot - I can't use "
                        "it. If you really need it, you'll have to save it again. In "
                        "the meantime, I'll remove it from your notes list."
                    )
                    sql.rm_note(chat_id, notename)
                else:
                    message.reply_text(
                        "This note could not be sent, as it is incorrectly formatted. Ask in "
                        f"@yuiichansupport if you can't figure out why!"
                    )
                    log.exception(
                        "Could not parse message #%s in chat %s", notename, str(chat_id)
                    )
                    log.warning("Message was: %s", str(note.value))
        return
    elif show_none:
        message.reply_text("This note doesn't exist")
# Do not async
@connection_status
def get(update, context, notename, show_none=True, no_format=False):
    bot = context.bot
    chat_id = update.effective_chat.id
    note = sql.get_note(chat_id, notename)
    message = update.effective_message  # type: Optional[Message]

    if note:
        # If we're replying to a message, reply to that message (unless it's an error)
        if message.reply_to_message:
            reply_id = message.reply_to_message.message_id
        else:
            reply_id = message.message_id

        if note.is_reply:
            if JOIN_LOGGER:
                try:
                    bot.forward_message(
                        chat_id=chat_id, from_chat_id=JOIN_LOGGER, message_id=note.value
                    )
                except BadRequest as excp:
                    if excp.message == "Message to forward not found":
                        message.reply_text(
                            "This message seems to have been lost - I'll remove it "
                            "from your notes list."
                        )
                        sql.rm_note(chat_id, notename)
                    else:
                        raise
            else:
                try:
                    bot.forward_message(
                        chat_id=chat_id, from_chat_id=chat_id, message_id=note.value
                    )
                except BadRequest as excp:
                    if excp.message == "Message to forward not found":
                        message.reply_text(
                            "Looks like the original sender of this note has deleted "
                            "their message - sorry! Get your bot admin to start using a "
                            "message dump to avoid this. I'll remove this note from "
                            "your saved notes."
                        )
                        sql.rm_note(chat_id, notename)
                    else:
                        raise
        else:
            VALID_NOTE_FORMATTERS = [
                "first",
                "last",
                "fullname",
                "username",
                "id",
                "chatname",
                "mention",
            ]
            valid_format = escape_invalid_curly_brackets(
                note.value, VALID_NOTE_FORMATTERS
            )
            if valid_format:
                text = valid_format.format(
                    first=escape_markdown(message.from_user.first_name),
                    last=escape_markdown(
                        message.from_user.last_name or message.from_user.first_name
                    ),
                    fullname=escape_markdown(
                        " ".join(
                            [message.from_user.first_name, message.from_user.last_name]
                            if message.from_user.last_name
                            else [message.from_user.first_name]
                        )
                    ),
                    username="@" + message.from_user.username
                    if message.from_user.username
                    else mention_markdown(
                        message.from_user.id, message.from_user.first_name
                    ),
                    mention=mention_markdown(
                        message.from_user.id, message.from_user.first_name
                    ),
                    chatname=escape_markdown(
                        message.chat.title
                        if message.chat.type != "private"
                        else message.from_user.first_name
                    ),
                    id=message.from_user.id,
                )
            else:
                text = ""

            keyb = []
            parseMode = ParseMode.MARKDOWN
            buttons = sql.get_buttons(chat_id, notename)
            if no_format:
                parseMode = None
                text += revert_buttons(buttons)
            else:
                keyb = build_keyboard(buttons)

            keyboard = InlineKeyboardMarkup(keyb)

            try:
                if note.msgtype in (sql.Types.BUTTON_TEXT, sql.Types.TEXT):
                    bot.send_message(
                        chat_id,
                        text,
                        reply_to_message_id=reply_id,
                        parse_mode=parseMode,
                        reply_markup=keyboard,
                    )
                else:
                    if ENUM_FUNC_MAP[note.msgtype] == dispatcher.bot.send_sticker:
                        ENUM_FUNC_MAP[note.msgtype](
                            chat_id,
                            note.file,
                            reply_to_message_id=reply_id,
                            reply_markup=keyboard,
                        )
                    else:
                        ENUM_FUNC_MAP[note.msgtype](
                            chat_id,
                            note.file,
                            caption=text,
                            reply_to_message_id=reply_id,
                            parse_mode=parseMode,
                            reply_markup=keyboard,
                        )

            except BadRequest as excp:
                if excp.message == "Entity_mention_user_invalid":
                    message.reply_text(
                        "Looks like you tried to mention someone I've never seen before. If you really "
                        "want to mention them, forward one of their messages to me, and I'll be able "
                        "to tag them!"
                    )
                elif FILE_MATCHER.match(note.value):
                    message.reply_text(
                        "This note was an incorrectly imported file from another bot - I can't use "
                        "it. If you really need it, you'll have to save it again. In "
                        "the meantime, I'll remove it from your notes list."
                    )
                    sql.rm_note(chat_id, notename)
                else:
                    message.reply_text(
                        "This note could not be sent, as it is incorrectly formatted. Ask in "
                        f"@yuiichansupport if you can't figure out why!"
                    )
                    log.exception(
                        "Could not parse message #%s in chat %s", notename, str(chat_id)
                    )
                    log.warning("Message was: %s", str(note.value))
        return
    elif show_none:
        message.reply_text("This note doesn't exist")


@connection_status
def cmd_get(update: Update, context: CallbackContext):
    bot, args = context.bot, context.args
    chat = update.effective_chat
    note = sql.get_note(chat.id, args[0])
    if not note:
        send_message(
            update.effective_message, "This note does not exist",
        )
        return
    is_private, is_delete = sql.get_private_note(chat.id)
    if len(args) >= 2 and args[1].lower() == "noformat":
        if is_private:
            buttons = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            text="Click me!",
                            url=f"http://t.me/YuiiChanBot?start=notes_{chat.id}={args[0]}",
                        )
                    ]
                ]
            )
            update.effective_message.reply_text(
                f"Tap here to view '{args[0]}' in your private chat.",
                parse_mode=None,
                disable_web_page_preview=True,
                reply_markup=buttons,
            )
        else:
            get(update, context, args[0], no_format=True)
    elif len(args) >= 1:
        if is_private:
            buttons = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            text="Click me!",
                            url=f"http://t.me/YuiiChanBot?start=notes_{chat.id}={args[0]}",
                        )
                    ]
                ]
            )
            update.effective_message.reply_text(
                f"Tap here to view '{args[0]}' in your private chat.",
                parse_mode=None,
                disable_web_page_preview=True,
                reply_markup=buttons,
            )
        else:
            get(update, context, args[0])
    else:
        send_message(update.effective_message, "Get what?")


@connection_status
def hash_get(update: Update, context: CallbackContext):
    message = update.effective_message.text
    chat = update.effective_chat
    is_private, is_delete = sql.get_private_note(chat.id)
    fst_word = message.split()[0]
    no_hash = fst_word[1:]
    note = sql.get_note(chat.id, no_hash)
    if not note:
        return
    if is_private:
        buttons = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        text="Click me!",
                        url=f"http://t.me/YuiiChanBot?start=notes_{chat.id}={no_hash}",
                    )
                ]
            ]
        )
        update.effective_message.reply_text(
            f"Tap here to view '{no_hash}' in your private chat.",
            parse_mode=None,
            disable_web_page_preview=True,
            reply_markup=buttons,
        )
    else:
        get(update, context, no_hash, show_none=False)


@connection_status
def slash_get(update: Update, context: CallbackContext):
    message, chat_id = update.effective_message.text, update.effective_chat.id
    no_slash = message[1:]
    note_list = sql.get_all_chat_notes(chat_id)

    try:
        noteid = note_list[int(no_slash) - 1]
        note_name = str(noteid).strip(">").split()[1]
        get(update, context, note_name, show_none=False)
    except IndexError:
        update.effective_message.reply_text("Wrong Note ID 😾")


@user_admin
@connection_status
def save(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    msg = update.effective_message  # type: Optional[Message]

    note_name, text, data_type, content, buttons = get_note_type(msg)
    note_name = note_name.lower()
    if data_type is None:
        msg.reply_text("Dude, there's no note")
        return

    sql.add_note_to_db(
        chat_id, note_name, text, data_type, buttons=buttons, file=content
    )

    msg.reply_text(
        f"Yas! Added `{note_name}`.\nGet it with /get `{note_name}`, or `#{note_name}`",
        parse_mode=ParseMode.MARKDOWN,
    )

    if msg.reply_to_message and msg.reply_to_message.from_user.is_bot:
        if text:
            msg.reply_text(
                "Seems like you're trying to save a message from a bot. Unfortunately, "
                "bots can't forward bot messages, so I can't save the exact message. "
                "\nI'll save all the text I can, but if you want more, you'll have to "
                "forward the message yourself, and then save it."
            )
        else:
            msg.reply_text(
                "Bots are kinda handicapped by telegram, making it hard for bots to "
                "interact with other bots, so I can't save this message "
                "like I usually would - do you mind forwarding it and "
                "then saving that new message? Thanks!"
            )
        return


@user_admin
@connection_status
def clear(update: Update, context: CallbackContext):
    args = context.args
    chat_id = update.effective_chat.id
    if len(args) >= 1:
        notename = args[0].lower()

        if sql.rm_note(chat_id, notename):
            update.effective_message.reply_text("Successfully removed note.")
        else:
            update.effective_message.reply_text("That's not a note in my database!")


def clearall(update: Update, context: CallbackContext):
    chat = update.effective_chat
    user = update.effective_user
    member = chat.get_member(user.id)
    if member.status != "creator" and user.id not in SUDO_USERS:
        update.effective_message.reply_text(
            "Only the chat owner can clear all notes at once."
        )
    else:
        buttons = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        text="Delete all notes", callback_data="notes_rmall"
                    )
                ],
                [InlineKeyboardButton(text="Cancel", callback_data="notes_cancel")],
            ]
        )
        update.effective_message.reply_text(
            f"Are you sure you would like to clear ALL notes in {chat.title}? This action cannot be undone.",
            reply_markup=buttons,
            parse_mode=ParseMode.MARKDOWN,
        )


def clearall_btn(update: Update, context: CallbackContext):
    query = update.callback_query
    chat = update.effective_chat
    message = update.effective_message
    member = chat.get_member(query.from_user.id)
    if query.data == "notes_rmall":
        if member.status == "creator" or query.from_user.id in SUDO_USERS:
            note_list = sql.get_all_chat_notes(chat.id)
            try:
                for notename in note_list:
                    note = notename.name.lower()
                    sql.rm_note(chat.id, note)
                message.edit_text("Deleted all notes.")
            except BadRequest:
                return

        if member.status == "administrator":
            query.answer("Only owner of the chat can do this.")

        if member.status == "member":
            query.answer("You need to be admin to do this.")
    elif query.data == "notes_cancel":
        if member.status == "creator" or query.from_user.id in SUDO_USERS:
            message.edit_text("Clearing of all notes has been cancelled.")
            return
        if member.status == "administrator":
            query.answer("Only owner of the chat can do this.")
        if member.status == "member":
            query.answer("You need to be admin to do this.")
            
@connection_status
@user_admin
def private_note(update: Update, context: CallbackContext):
	args = context.args
	chat = update.effective_chat  # type: Optional[Chat]
	user = update.effective_user  # type: Optional[User]
	conn = 1
	if conn == 2:
		chat_id = conn
		chat_name = dispatcher.context.bot.getChat(conn).title
	else:
		chat_id = update.effective_chat.id
		if chat.type == "private":
			chat_name = chat.title
		else:
			chat_name = chat.title

	if len(args) >= 1:
		if args[0] in ("yes", "on", "ya"):
			if len(args) >= 2:
				if args[1] == "del":
					sql.private_note(str(chat_id), True, True)
					send_message(update.effective_message, "Private Note is * activated *, when a user retrieves a note, the note message will be sent to the PM.", parse_mode="markdown")
				else:
					sql.private_note(str(chat_id), True, False)
					send_message(update.effective_message, "Private Note is * activated *, when a user retrieves a note, a note message will be sent to the PM.", parse_mode="markdown")
			else:
				sql.private_note(str(chat_id), True, False)
				send_message(update.effective_message, "Private Note is * activated *, when a user retrieves a note, a note message will be sent to the PM.", parse_mode="markdown")
		elif args[0] in ("no", "off"):
			sql.private_note(str(chat_id), False, False)
			send_message(update.effective_message, "Private Note is * deactivated *, note messages will be sent to the group.", parse_mode="markdown")
		else:
			send_message(update.effective_message, "I understand only 'yes', or 'no'.")
	else:
		is_private, is_delete = sql.get_private_note(chat_id)
		print(is_private, is_delete)
		send_message(update.effective_message, "Private Note Settings at {}: *{}*{}".format(chat_name, "Enabled" if is_private else "Disabled", " - *Hash will be deleted*" if is_delete else ""), parse_mode="markdown")


@connection_status
def list_notes(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    note_list = sql.get_all_chat_notes(chat_id)
    is_private, is_delete = sql.get_private_note(chat_id)
    notes = len(note_list) + 1
    msg = "Get note by `/notenumber` or `#notename` \n\n  *ID*    *Note* \n"
    for note_id, note in zip(range(1, notes), note_list):
        if note_id < 10:
            note_name = f"`{note_id:2}.`  `#{(note.name.lower())}`\n"
        else:
            note_name = f"`{note_id}.`  `#{(note.name.lower())}`\n"
        if len(msg) + len(note_name) > MAX_MESSAGE_LENGTH:
            update.effective_message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
            msg = ""
        msg += note_name

    if not note_list:
        update.effective_message.reply_text("No notes in this chat!")
    elif is_private:
        buttons = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        text="Click me!",
                        url=f"http://t.me/YuiiChanBot?start=allnotes_{chat_id}",
                    )
                ]
            ]
        )
        update.effective_message.reply_text(
            f"Tap here to view all notes in this chat.",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
            reply_markup=buttons,
        )
        return
    elif len(msg) != 0:
        update.effective_message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

def lst_notes(bot, update, chat_id):
    chat = bot.getChat(chat_id)
    msg = f"*List of Notes in {chat.title}:*\n"
    note_list = sql.get_all_chat_notes(chat_id)
    for note in note_list:
        note_name = f"• [{note.name}](http://t.me/YuiiChanBot?start=notes_{chat_id}={note.name})\n"
        if len(msg) + len(note_name) > MAX_MESSAGE_LENGTH:
            update.effective_message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
            msg = ""
        msg += note_name
    if not note_list:
        update.effective_message.reply_text(
            "No notes in *{}*!".format(chat.title), parse_mode=ParseMode.MARKDOWN
        )
    elif len(msg) != 0:
        msg += "\nYou can retrieve these notes by tapping on the notename."
        update.effective_message.reply_text(
            msg.format(chat.title), parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True
        )
        
def __import_data__(chat_id, data):
    failures = []
    for notename, notedata in data.get("extra", {}).items():
        match = FILE_MATCHER.match(notedata)
        matchsticker = STICKER_MATCHER.match(notedata)
        matchbtn = BUTTON_MATCHER.match(notedata)
        matchfile = MYFILE_MATCHER.match(notedata)
        matchphoto = MYPHOTO_MATCHER.match(notedata)
        matchaudio = MYAUDIO_MATCHER.match(notedata)
        matchvoice = MYVOICE_MATCHER.match(notedata)
        matchvideo = MYVIDEO_MATCHER.match(notedata)
        matchvn = MYVIDEONOTE_MATCHER.match(notedata)

        if match:
            failures.append(notename)
            notedata = notedata[match.end() :].strip()
            if notedata:
                sql.add_note_to_db(chat_id, notename[1:], notedata, sql.Types.TEXT)
        elif matchsticker:
            content = notedata[matchsticker.end() :].strip()
            if content:
                sql.add_note_to_db(
                    chat_id, notename[1:], notedata, sql.Types.STICKER, file=content
                )
        elif matchbtn:
            parse = notedata[matchbtn.end() :].strip()
            notedata = parse.split("<###button###>")[0]
            buttons = parse.split("<###button###>")[1]
            buttons = ast.literal_eval(buttons)
            if buttons:
                sql.add_note_to_db(
                    chat_id,
                    notename[1:],
                    notedata,
                    sql.Types.BUTTON_TEXT,
                    buttons=buttons,
                )
        elif matchfile:
            file = notedata[matchfile.end() :].strip()
            file = file.split("<###TYPESPLIT###>")
            notedata = file[1]
            content = file[0]
            if content:
                sql.add_note_to_db(
                    chat_id, notename[1:], notedata, sql.Types.DOCUMENT, file=content
                )
        elif matchphoto:
            photo = notedata[matchphoto.end() :].strip()
            photo = photo.split("<###TYPESPLIT###>")
            notedata = photo[1]
            content = photo[0]
            if content:
                sql.add_note_to_db(
                    chat_id, notename[1:], notedata, sql.Types.PHOTO, file=content
                )
        elif matchaudio:
            audio = notedata[matchaudio.end() :].strip()
            audio = audio.split("<###TYPESPLIT###>")
            notedata = audio[1]
            content = audio[0]
            if content:
                sql.add_note_to_db(
                    chat_id, notename[1:], notedata, sql.Types.AUDIO, file=content
                )
        elif matchvoice:
            voice = notedata[matchvoice.end() :].strip()
            voice = voice.split("<###TYPESPLIT###>")
            notedata = voice[1]
            content = voice[0]
            if content:
                sql.add_note_to_db(
                    chat_id, notename[1:], notedata, sql.Types.VOICE, file=content
                )
        elif matchvideo:
            video = notedata[matchvideo.end() :].strip()
            video = video.split("<###TYPESPLIT###>")
            notedata = video[1]
            content = video[0]
            if content:
                sql.add_note_to_db(
                    chat_id, notename[1:], notedata, sql.Types.VIDEO, file=content
                )
        elif matchvn:
            video_note = notedata[matchvn.end() :].strip()
            video_note = video_note.split("<###TYPESPLIT###>")
            notedata = video_note[1]
            content = video_note[0]
            if content:
                sql.add_note_to_db(
                    chat_id, notename[1:], notedata, sql.Types.VIDEO_NOTE, file=content
                )
        else:
            sql.add_note_to_db(chat_id, notename[1:], notedata, sql.Types.TEXT)

    if failures:
        with BytesIO(str.encode("\n".join(failures))) as output:
            output.name = "failed_imports.txt"
            dispatcher.bot.send_document(
                chat_id,
                document=output,
                filename="failed_imports.txt",
                caption="These files/photos failed to import due to originating "
                "from another bot. This is a telegram API restriction, and can't "
                "be avoided. Sorry for the inconvenience!",
            )


def __stats__():
    return f"• {sql.num_notes()} notes, across {sql.num_chats()} chats."


def __migrate__(old_chat_id, new_chat_id):
    sql.migrate_chat(old_chat_id, new_chat_id)


def __chat_settings__(chat_id, user_id):
    notes = sql.get_all_chat_notes(chat_id)
    return f"There are `{len(notes)}` notes in this chat."

from tg_bot.modules.language import gs

def get_help(chat):
    return gs(chat, "notes_help")


__mod_name__ = "Notes"

GET_HANDLER = CommandHandler("get", cmd_get, run_async=True)
HASH_GET_HANDLER = MessageHandler(Filters.regex(r"^#[^\s]+"), hash_get, run_async=True)
SLASH_GET_HANDLER = MessageHandler(Filters.regex(r"^/\d+$"), slash_get, run_async=True)
SAVE_HANDLER = CommandHandler("save", save, run_async=True)
PMNOTE_HANDLER = CommandHandler("privatenote", private_note, run_async=True)
DELETE_HANDLER = CommandHandler("clear", clear, run_async=True)

LIST_HANDLER = DisableAbleCommandHandler(
    ["notes", "saved"], list_notes, admin_ok=True, run_async=True
)

CLEARALL = DisableAbleCommandHandler("removeallnotes", clearall)
CLEARALL_BTN = CallbackQueryHandler(clearall_btn, pattern=r"notes_.*")

dispatcher.add_handler(GET_HANDLER)
dispatcher.add_handler(SAVE_HANDLER)
dispatcher.add_handler(LIST_HANDLER)
dispatcher.add_handler(DELETE_HANDLER)
dispatcher.add_handler(HASH_GET_HANDLER)
dispatcher.add_handler(SLASH_GET_HANDLER)
dispatcher.add_handler(PMNOTE_HANDLER)
dispatcher.add_handler(CLEARALL)
dispatcher.add_handler(CLEARALL_BTN)

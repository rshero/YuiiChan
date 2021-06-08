import tg_bot.modules.sql.welcome_sql as sql
from telethon import events
from tg_bot import telethn

@telethn.on(events.ChatAction)
async def delete_service(event):
  clean = sql.clean_service(event.chat_id)
  if not clean:
    return
  await event.delete()

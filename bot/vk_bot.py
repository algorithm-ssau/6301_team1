import os
import json
import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType

from google_services import get_credentials, CalendarAPI, DriveAPI
from db import Database
from bot_controller import BotController
from dotenv import load_dotenv
load_dotenv()

VK_TOKEN = os.environ['VK_GROUP_TOKEN']
VK_GROUP_ID = int(os.environ['VK_GROUP_ID'])


def main():
    print('Авторизуемся в Google...')
    creds = get_credentials()
    drive = DriveAPI(creds)
    calendar = CalendarAPI(creds)

    db = Database()
    session = vk_api.VkApi(token=VK_TOKEN)
    longpoll = VkBotLongPoll(session, VK_GROUP_ID)
    ctrl = BotController(session, db, calendar, drive)

    print('Бот запущен.')
    for event in longpoll.listen():
        if event.type != VkBotEventType.MESSAGE_NEW:
            continue
        msg = event.obj.message
        peer_id = msg['peer_id']
        text = (msg.get('text') or '').strip()
        payload = None
        if msg.get('payload'):
            try:
                payload = json.loads(msg['payload'])
            except json.JSONDecodeError:
                pass
        ctrl.handle(peer_id, text, payload)


if __name__ == '__main__':
    main()

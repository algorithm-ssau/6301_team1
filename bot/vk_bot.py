from dotenv import load_dotenv
load_dotenv()

import os, json, threading
import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType

from db import Database
from bot_controller import BotController
import auth_server

VK_TOKEN = os.environ['VK_GROUP_TOKEN']
VK_GROUP_ID = int(os.environ['VK_GROUP_ID'])

def main():
    threading.Thread(target=auth_server.run, daemon=True).start()
    print('OAuth server: ', os.environ['OAUTH_REDIRECT_URI'])

    db = Database()
    session = vk_api.VkApi(token=VK_TOKEN)
    longpoll = VkBotLongPoll(session, VK_GROUP_ID)
    ctrl = BotController(session, db)

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

        docs = []
        for att in msg.get('attachments', []):
            if att.get('type') == 'doc' and att.get('doc'):
                docs.append(att['doc'])

        ctrl.handle(peer_id, text, payload, docs=docs)

if __name__ == '__main__':
    main()

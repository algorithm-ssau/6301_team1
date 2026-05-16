import os.path
import datetime as dt

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/calendar']
CREDENTIALS_FILE = 'credentials_d.json'
TOKEN_FILE = 'token.json'
REDIRECT_URI = 'http://localhost:8080/'   # должен совпадать с тем, что в Google Cloud


def get_credentials():
    creds = None
    # 1. Пробуем взять сохранённый токен
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    # 2. Если нет/истёк — обновляем или логинимся заново
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # Для Web-клиента используем InstalledAppFlow аналог через Flow
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, SCOPES
            )
            # run_local_server сам поднимет http://localhost:8080/
            creds = flow.run_local_server(
                port=8080,
                prompt='consent',
                authorization_prompt_message='Открываю браузер для авторизации...',
                success_message='Успех! Можно закрыть вкладку.',
                open_browser=True,
            )
        # 3. Сохраняем токен на будущее
        with open(TOKEN_FILE, 'w') as f:
            f.write(creds.to_json())
    return creds


def add_event(summary: str, start: dt.datetime, end: dt.datetime, description: str = ''):
    creds = get_credentials()
    service = build('calendar', 'v3', credentials=creds)

    event = {
        'summary': summary,
        'description': description,
        'start': {'dateTime': start.isoformat(), 'timeZone': 'Europe/Samara'},
        'end':   {'dateTime': end.isoformat(),   'timeZone': 'Europe/Samara'},
    }
    result = service.events().insert(calendarId='primary', body=event).execute()
    print('Готово! ID события:', result.get('id'))
    print('Ссылка:', result.get('htmlLink'))
    return result


# if __name__ == '__main__':
#     start = dt.datetime(2026, 5, 20, 18, 0, 0)
#     end   = dt.datetime(2026, 5, 20, 19, 0, 0)
#     add_event('Тест Web Creds', start, end, 'сообщение из бота')

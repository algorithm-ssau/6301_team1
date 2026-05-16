import os
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# 1. Загружаем файл десктопных учетных данных по точному пути
flow = InstalledAppFlow.from_client_secrets_file(
    'credentials.json',
    scopes=['https://googleapis.com']
)

# 2. Метод через ссылку в терминале
creds = flow.run_console()

# 3. Подключение к календарю и запись
service = build('calendar', 'v3', credentials=creds)

event = {
    'summary': 'Тест Desktop Creds',
    'start': {'dateTime': '2026-05-20T18:00:00+03:00'},
    'end': {'dateTime': '2026-05-20T19:00:00+03:00'}
}

result = service.events().insert(calendarId='primary', body=event).execute()
print("Готово! ID события:", result.get('id'))

"""Explicit Telegram checks; credentials are read only from server settings."""
import sys

from .telegram_api import DeliveryError, check_connection, site_origin
from .config import validate_notification_transport

_ERROR_MESSAGES = {
    'NOTIFICATION_TRANSPORT_INVALID': 'Укажите NOTIFICATION_TRANSPORT=direct или relay.',
    'TELEGRAM_NOT_CONFIGURED': 'Заполните серверные TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID.',
    'TELEGRAM_INVALID_SITE_URL': 'Укажите корректный PUBLIC_SITE_URL без пути, пароля, query и fragment.',
    'TELEGRAM_AUTH_ERROR': 'Telegram отклонил токен бота. Проверьте серверную настройку.',
    'TELEGRAM_FORBIDDEN': 'Доступ запрещён. Проверьте доступ бота к каналу и его права.',
    'TELEGRAM_REJECTED': 'Telegram отклонил запрос. Проверьте канал и серверные настройки.',
    'TELEGRAM_INVALID_BOT': 'Telegram не подтвердил ожидаемую учётную запись бота.',
    'TELEGRAM_NOT_CHANNEL': 'Указанный чат не является каналом Telegram.',
    'TELEGRAM_NO_POST_PERMISSION': 'Добавьте бота администратором канала с правом публикации сообщений.',
    'TELEGRAM_NETWORK_ERROR': 'Не удалось получить ответ Telegram. Проверьте исходящий HTTPS-доступ.',
    'TELEGRAM_UNAVAILABLE': 'Telegram временно недоступен.',
    'TELEGRAM_INVALID_RESPONSE': 'Получен некорректный ответ Telegram.',
    'TELEGRAM_RATE_LIMIT': 'Telegram ограничил частоту запросов.',
}


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if arguments not in (['check'], ['test']):
        print('Использование: python -m app.telegram_cli check|test', file=sys.stderr)
        return 2
    try:
        if validate_notification_transport() == 'relay':
            from .notification_relay_client import RelayClient
            client = RelayClient()
            try:
                client.check()
            finally:
                client.close()
            if arguments == ['check']:
                print('Проверка relay пройдена: TLS, авторизация и ledger доступны; доставка Telegram проверяется отдельной командой test.')
            else:
                from .notification_worker import send_test_notification
                from .notification_protocol import notifications_pb2 as pb
                receipt = send_test_notification()
                if receipt.state == pb.TELEGRAM_ACCEPTED and receipt.HasField('telegram_message_id') and receipt.telegram_message_id > 0:
                    print(f'Тестовое сообщение принято Telegram: сообщение {receipt.telegram_message_id}.')
                else:
                    print(f'Тест принят relay: {receipt.notification_id}; доставка Telegram пока не подтверждена. Повтор команды создаёт новое техническое сообщение.')
            return 0
        info = check_connection()
        if arguments == ['check']:
            print(f'Проверка пройдена: бот {info["bot_id"]}, канал {info["chat_id"]}; публикация разрешена.')
        else:
            from .notification_worker import send_test_notification

            message_id = send_test_notification()
            print(f'Тестовое сообщение принято Telegram: канал {info["chat_id"]}, сообщение {message_id}.')
        if not site_origin():
            print('PUBLIC_SITE_URL не задан: доставка проверяется без ссылки на CRM. Для автоматических уведомлений настройте адрес сайта.')
        return 0
    except DeliveryError as error:
        print(f'{error.code}: {_ERROR_MESSAGES.get(error.code, "Проверка Telegram завершилась ошибкой.")}', file=sys.stderr)
        if error.code == 'TELEGRAM_RATE_LIMIT':
            print(f'Повторите не раньше чем через {error.retry_after} сек.; автоматического повтора нет.', file=sys.stderr)
        if error.uncertain:
            print('Исход отправки неизвестен. Проверьте канал: повтор команды может создать дубль.', file=sys.stderr)
        return 1
    except Exception:
        # Database and other unexpected exception text can include credentials.
        print('TELEGRAM_CHECK_ERROR: Проверка не завершена; проверьте БД и серверные настройки.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

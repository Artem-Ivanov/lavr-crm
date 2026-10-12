import argparse
from getpass import getpass
import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .auth import Staff, hash_password
from .common import new_id
from .db import SessionLocal


def create_manager(db, login, name, password):
    login, name = login.strip(), name.strip()
    if not 1 <= len(login) <= 120 or re.search(r'[\x00-\x1f\x7f]', login):
        raise ValueError('Логин должен содержать от 1 до 120 символов.')
    if not 1 <= len(name) <= 240 or '\x00' in name:
        raise ValueError('Имя должно содержать от 1 до 240 символов.')
    if not 16 <= len(password) <= 256 or '\x00' in password:
        raise ValueError('Пароль должен содержать от 16 до 256 символов.')
    if db.scalar(select(Staff.id).where(Staff.login == login)) is not None:
        raise ValueError('Логин уже существует.')
    manager = Staff(id=new_id(), login=login, name=name, role='manager', password_hash=hash_password(password), is_active=True)
    db.add(manager)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ValueError('Логин уже существует.') from None
    return manager.id


def main():
    parser = argparse.ArgumentParser(description='Создать менеджера CRM без изменения существующих пользователей.')
    parser.add_argument('login')
    parser.add_argument('--name', required=True)
    arguments = parser.parse_args()
    password = getpass('Пароль менеджера: ')
    if password != getpass('Повторите пароль: '):
        raise SystemExit('Пароли не совпадают.')
    try:
        with SessionLocal() as db:
            create_manager(db, arguments.login, arguments.name, password)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    except SQLAlchemyError:
        raise SystemExit('Не удалось создать менеджера. Проверьте PostgreSQL.') from None
    print('Менеджер создан.')


if __name__ == '__main__':
    main()

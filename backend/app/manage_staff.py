import argparse
from getpass import getpass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .auth import User, hash_password, normalize_email
from .common import new_id, utcnow
from .db import SessionLocal
from .crypto import lookup_digest


def create_manager(db, login, name, password):
    email, name = normalize_email(login), name.strip()
    if not 1 <= len(name) <= 240 or '\x00' in name:
        raise ValueError('Имя должно содержать от 1 до 240 символов.')
    if not 16 <= len(password) <= 256 or '\x00' in password:
        raise ValueError('Пароль должен содержать от 16 до 256 символов.')
    if db.scalar(select(User.id).where(User.email_lookup == lookup_digest('email', email))) is not None:
        raise ValueError('Email уже существует.')
    manager = User(id=new_id(), name=name, role='operator', password_hash=hash_password(password),
                   is_active=True, email_verified_at=utcnow())
    manager.profile = {'email': email}
    db.add(manager)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ValueError('Email уже существует.') from None
    return manager.id


def main():
    parser = argparse.ArgumentParser(description='Создать менеджера CRM без изменения существующих пользователей.')
    parser.add_argument('email', help='Подтверждённый владельцем email менеджера.')
    parser.add_argument('--name', required=True)
    arguments = parser.parse_args()
    password = getpass('Пароль менеджера: ')
    if password != getpass('Повторите пароль: '):
        raise SystemExit('Пароли не совпадают.')
    try:
        with SessionLocal() as db:
            create_manager(db, arguments.email, arguments.name, password)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    except SQLAlchemyError:
        raise SystemExit('Не удалось создать менеджера. Проверьте PostgreSQL.') from None
    print('Менеджер создан.')


if __name__ == '__main__':
    main()

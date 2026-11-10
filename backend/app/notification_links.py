"""Trusted notification destinations, kept compatible with navigation-v1.json."""
from urllib.parse import urlencode

from .telegram_api import DeliveryError, site_origin

_PARAMETERS = {
    'home': ('section', 'publication'), 'catalog': ('category', 'product'), 'cart': (),
    'crm': ('order',), 'crm-feedback': ('case',), 'account': (),
    'account-orders': ('order',), 'account-feedback': ('case',), 'admin-users': ('user',),
    'crm-catalog': (), 'crm-content': ('tab', 'publication'),
}


def site_link(page: str, parameters: dict[str, str] | None = None) -> str:
    origin = site_origin()
    if not origin:
        raise DeliveryError('TELEGRAM_NOT_CONFIGURED', retry_after=900)
    parameters = {} if parameters is None else parameters
    if not isinstance(page, str) or page not in _PARAMETERS or not isinstance(parameters, dict):
        raise DeliveryError('NOTIFICATION_INVALID_LINK')
    keys = _PARAMETERS[page]
    if any(key not in keys for key in parameters):
        raise DeliveryError('NOTIFICATION_INVALID_LINK')
    for value in parameters.values():
        if (not isinstance(value, str) or not 1 <= len(value) <= 128 or value in ('.', '..')
                or any(ord(char) < 32 or 127 <= ord(char) <= 159 or 0xD800 <= ord(char) <= 0xDFFF for char in value)):
            raise DeliveryError('NOTIFICATION_INVALID_LINK')
    if page == 'home' and (('section' in parameters and parameters['section'] not in ('offers', 'announcements', 'hot'))
                           or ('section' in parameters and 'publication' in parameters)):
        raise DeliveryError('NOTIFICATION_INVALID_LINK')
    if page == 'crm-content' and (('tab' in parameters and parameters['tab'] not in ('site', 'publications', 'media'))
                                  or ('publication' in parameters and parameters.get('tab') != 'publications')):
        raise DeliveryError('NOTIFICATION_INVALID_LINK')
    # URLSearchParams uses form encoding: spaces as +, literal *, and encoded ~.
    query = urlencode([(key, parameters[key]) for key in keys if key in parameters
                       and not (page == 'crm-content' and key == 'tab' and parameters[key] == 'site')], safe='*').replace('~', '%7E')
    return f'{origin}/#{page}' + (f'?{query}' if query else '')

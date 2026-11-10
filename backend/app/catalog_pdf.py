"""Local, coordinate-based reader for the supplied 1C grid report. No OCR."""
from bisect import bisect_right
from dataclasses import asdict
from decimal import Decimal
import json
from pathlib import Path
import re

from .catalog_sources import CatalogSourceRow, cell_text, MAX_ROWS
from .common import ApiError

MAX_BYTES = 5 * 1024 * 1024
MAX_PAGES = 400
COLUMNS = (28.47, 62.55, 188.79, 442.23, 505.59, 568.95)
REPORT_HEADER = (
    ['Ценовая г', 'руппа', '', 'Цена', 'Остаток'],
    ['Набор но', 'менклатуры', '', '', ''],
    ['Бренд (Общие)', 'Артикул', 'Номенклатура', '', ''],
)


def report_number(value, *, places):
    value = cell_text(value)
    if not value:
        return None
    if not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?: [0-9]{3})+)(?:,[0-9]{1,' + str(places) + r'})?', value):
        raise ApiError(422, 'PDF_INVALID_NUMBER', 'Некорректное число в ячейке отчёта.')
    return Decimal(value.replace(' ', '').replace(',', '.'))


def _fail(page, message):
    raise ApiError(422, 'PDF_LAYOUT_UNSUPPORTED', message, {'page': page})


def parse_page(page, *, position, context=''):
    from pdfplumber.utils import extract_text
    n = page.page_number
    if abs(page.width - 595.32) > 1 or abs(page.height - 841.92) > 1 or not page.chars:
        _fail(n, 'Ожидается текстовый отчёт с исходной сеткой A4.')
    horizontal = [e for e in page.lines if abs(e['top'] - e['bottom']) < .2]
    ys = sorted({round(e['top'], 2) for e in horizontal})
    if len(ys) < 3 or len(ys) > 300:
        _fail(n, 'Не найдена полная сетка строк отчёта.')
    vertical = [e for e in page.lines if abs(e['x0'] - e['x1']) < .2]
    buckets = [[] for _ in range(len(ys) - 1)]
    for char in page.chars:
        cy = (char['top'] + char['bottom']) / 2
        cx = (char['x0'] + char['x1']) / 2
        idx = bisect_right(ys, cy) - 1
        if idx < 0 or idx >= len(buckets) or cx < COLUMNS[0] - 1 or cx > COLUMNS[-1] + 1:
            if char['text'].strip():
                _fail(n, 'Обнаружен текст вне распознанной сетки.')
            continue
        buckets[idx].append(char)
    result = []
    header_seen = n != 1
    header_position = 0
    for idx, chars in enumerate(buckets):
        top, bottom = ys[idx:idx + 2]
        if not chars:
            continue
        mid = (top + bottom) / 2
        boundaries = [any(abs(e['x0'] - x) < .5 and e['top'] <= mid <= e['bottom'] for e in vertical) for x in COLUMNS]
        if not all(boundaries[i] for i in (0, 3, 4, 5)):
            _fail(n, 'Границы числовых ячеек отчёта изменились.')
        cells = [[] for _ in range(5)]
        for char in chars:
            col = min(4, max(0, bisect_right(COLUMNS, (char['x0'] + char['x1']) / 2) - 1))
            cells[col].append(char)
        texts = [cell_text(extract_text(cell, x_tolerance=1, y_tolerance=2)) for cell in cells]
        if header_position < len(REPORT_HEADER) and texts == REPORT_HEADER[header_position]:
            header_position += 1
            header_seen = header_position == len(REPORT_HEADER)
            continue
        if not header_seen:
            _fail(n, 'Не найден заголовок исходного отчёта.')
        # Merged first three columns distinguish bundle headers from normal brand cells.
        merged = not boundaries[1] and not boundaries[2]
        if not merged and not all(boundaries):
            _fail(n, 'Неоднозначные границы текстовых ячеек.')
        if merged:
            name = cell_text(extract_text([c for c in chars if (c['x0'] + c['x1']) / 2 < COLUMNS[3]], x_tolerance=1, y_tolerance=2))
            brand, sku, kind = '', '', 'bundle'
            context = name
        else:
            brand, sku, name = texts[:3]
            kind = 'component' if context else 'ordinary'
        if not name:
            _fail(n, 'В строке отчёта отсутствует номенклатура.')
        price = report_number(texts[3], places=2)
        stock = report_number(texts[4], places=3)
        if price is None:
            _fail(n, 'В товарной строке отсутствует цена; пакет не будет применён.')
        position += 1
        result.append(CatalogSourceRow(position=position, page=n, name=name, sku=sku, brand=brand,
                                      stock=stock, price_kopecks=int(price * 100), kind=kind, context=context,
                                      raw_cells=texts, coordinates=[COLUMNS[0], top, COLUMNS[-1], bottom]))
    if not header_seen or not result:
        _fail(n, 'Страница не содержит распознанных товарных строк.')
    return result, context


def coordinate_page(page, index):
    import ctypes
    from contextlib import closing
    from types import SimpleNamespace
    import pypdfium2 as pdfium
    raw = pdfium.raw
    width, height = page.get_size()
    lines = []
    for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_PATH]):
        if raw.FPDFPath_CountSegments(obj) != 2:
            continue
        coords = []
        matrix = obj.get_matrix()
        for j in range(2):
            seg = raw.FPDFPath_GetPathSegment(obj, j)
            x, y = (ctypes.c_float(), ctypes.c_float())
            raw.FPDFPathSegment_GetPoint(seg, x, y)
            coords.append(matrix.on_point(x.value, y.value))
        (x0, y0), (x1, y1) = coords
        lines.append({'x0': min(x0, x1), 'x1': max(x0, x1), 'top': height - max(y0, y1), 'bottom': height - min(y0, y1)})
    with closing(page.get_textpage()) as text:
        chars = []
        for i in range(text.count_chars()):
            if raw.FPDFText_IsGenerated(text, i) == 1:
                continue
            ch = chr(raw.FPDFText_GetUnicode(text, i))
            if not ch.strip() and ch != ' ':
                continue
            left, bottom, right, top = text.get_charbox(i, loose=True)
            chars.append({'text': ch, 'x0': left, 'x1': right, 'top': height - top, 'bottom': height - bottom, 'doctop': height - top, 'upright': True, 'size': top - bottom})
    return SimpleNamespace(page_number=index + 1, width=width, height=height, lines=lines, chars=chars)


def parse_pdf(path):
    from contextlib import closing
    import pypdfium2 as pdfium
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ApiError(413, 'PDF_TOO_LARGE', 'PDF превышает 5 MiB.')
    with path.open('rb') as source:
        if source.read(5) != b'%PDF-':
            raise ApiError(422, 'PDF_UNREADABLE', 'Загрузите исходный PDF отчёта.')
    rows, context = [], ''
    try:
        with pdfium.PdfDocument(path) as pdf:
            if pdfium.raw.FPDF_GetSecurityHandlerRevision(pdf) >= 0:
                raise ApiError(422, 'PDF_UNREADABLE', 'Нужен исходный PDF без защиты.')
            if not 1 <= len(pdf) <= MAX_PAGES:
                raise ApiError(422, 'PDF_PAGE_LIMIT', 'PDF должен содержать не более 400 страниц.')
            page_count = len(pdf)
            for index in range(page_count):
                with closing(pdf[index]) as page:
                    parsed, context = parse_page(coordinate_page(page, index), position=len(rows), context=context)
                    rows.extend(parsed)
                    if len(rows) > MAX_ROWS:
                        raise ApiError(422, 'PDF_ROW_LIMIT', 'PDF содержит более 50 000 строк.')
            return rows, page_count
    except ApiError:
        raise
    except Exception:
        raise ApiError(422, 'PDF_UNREADABLE', 'Не удалось прочитать PDF. Нужен исходный текстовый отчёт без защиты.') from None


def parser_main():
    """The worker invokes this child with private input/output paths, never a URL."""
    import resource
    import socket
    import sys
    import pdfplumber  # Load dependencies before imposing the address-space cap.
    import pypdfium2

    def no_network(*args, **kwargs):
        raise RuntimeError('Network is disabled in the PDF parser')
    socket.socket = no_network
    resource.setrlimit(resource.RLIMIT_CPU, (300, 300))
    resource.setrlimit(resource.RLIMIT_FSIZE, (100 * 1024 * 1024, 100 * 1024 * 1024))
    # RLIMIT_AS is enforced on Linux; macOS has a much larger shared-library mapping.
    if sys.platform != 'darwin':
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    rows, pages = parse_pdf(sys.argv[1])
    payload = {'pageCount': pages, 'rows': [asdict(row) for row in rows]}
    Path(sys.argv[2]).write_text(json.dumps(payload, default=str, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    import sys
    try:
        parser_main()
    except ApiError as error:
        print(json.dumps({'code': error.code, 'message': error.message, 'details': error.details}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)

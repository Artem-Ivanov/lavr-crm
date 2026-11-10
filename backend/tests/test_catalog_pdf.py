from decimal import Decimal
import pytest


def test_report_number_accepts_thousands_and_comma_without_rounding():
    from app.catalog_pdf import report_number
    from app.common import ApiError
    assert report_number('1 468', places=3) == Decimal('1468')
    assert report_number('9,785', places=3) == Decimal('9.785')
    assert report_number('28 420,00', places=2) == Decimal('28420.00')
    assert report_number('', places=3) is None
    for value in ['1,0001','-1','NaN','1 23','1.2','1,2,3']:
        with pytest.raises(ApiError):
            report_number(value, places=3)


def test_malformed_pdf_is_rejected_as_package_error(tmp_path):
    from app.catalog_pdf import parse_pdf
    from app.common import ApiError
    path = tmp_path/'bad.pdf'
    path.write_bytes(b'%PDF-1.4\nmalformed')
    with pytest.raises(ApiError) as failure:
        parse_pdf(path)
    assert failure.value.code == 'PDF_UNREADABLE'


def test_native_coordinates_keep_empty_cells_exact_numbers_and_merged_bundles():
    from contextlib import closing
    from pathlib import Path
    import pypdfium2
    from app.catalog_pdf import coordinate_page, parse_page
    path=Path(__file__).resolve().parents[2]/'docs'/'номенклатура1.pdf'
    with pypdfium2.PdfDocument(path) as document:
        with closing(document[0]) as page:
            rows,_=parse_page(coordinate_page(page,0),position=0)
            assert (rows[0].name,rows[0].sku,rows[0].brand,rows[0].stock,rows[0].price_kopecks)==('Автопоилка АП-1А (пластм. чаша) 1,7л.','','',Decimal('14'),82000)
        with closing(document[261]) as page:
            rows,_=parse_page(coordinate_page(page,261),position=0)
            bundles=[row for row in rows if row.kind=='bundle']
            assert len(bundles)==3 and bundles[0].stock is None
            assert bundles[1].name=='Рессора передняя МАЗ 3-х листовая 6430-2902012-10 L=2100мм'
            assert bundles[1].price_kopecks==2842000 and bundles[1].stock==2


def test_changed_grid_page_rejects_entire_page():
    from contextlib import closing
    from pathlib import Path
    import pypdfium2
    from app.catalog_pdf import coordinate_page,parse_page
    from app.common import ApiError
    with pypdfium2.PdfDocument(Path(__file__).resolve().parents[2]/'docs'/'номенклатура1.pdf') as document:
        with closing(document[1]) as page:
            coordinates=coordinate_page(page,1)
    coordinates.lines=[line for line in coordinates.lines if abs(line['x0']-442.23)>.5 or abs(line['x0']-line['x1'])>.2]
    with pytest.raises(ApiError) as failure:
        parse_page(coordinates,position=0)
    assert failure.value.code=='PDF_LAYOUT_UNSUPPORTED' and failure.value.details['page']==2


def test_exact_report_header_repeats_on_later_pages_without_changing_product_rows():
    from contextlib import closing
    from dataclasses import asdict
    from pathlib import Path
    import pypdfium2
    from app.catalog_pdf import coordinate_page, parse_page
    from app.common import ApiError
    with pypdfium2.PdfDocument(Path(__file__).resolve().parents[2]/'docs'/'номенклатура1.pdf') as document:
        with closing(document[0]) as page:
            coordinates = coordinate_page(page, 0)
    first, first_context = parse_page(coordinates, position=0)
    coordinates.page_number = 2
    repeated, repeated_context = parse_page(coordinates, position=0)
    assert len(repeated) == len(first) == 91
    assert repeated_context == first_context
    assert [{**asdict(row), 'page': 1} for row in repeated] == [asdict(row) for row in first]
    # Changing a recognized header or an ordinary numeric cell still fails closed.
    for original, replacement in [('Цена', 'Сумм'), ('820,00', 'ошибка')]:
        from copy import deepcopy
        changed = deepcopy(coordinates)
        text = ''.join(char['text'] for char in changed.chars)
        start = text.index(original)
        for offset, char in enumerate(changed.chars[start:start + len(original)]):
            char['text'] = replacement if offset == 0 else ''
        with pytest.raises(ApiError):
            parse_page(changed, position=0)

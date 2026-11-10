"""Reviewed name hints for file imports; never create or alter categories."""
from collections import defaultdict
import json
from pathlib import Path
import re
import unicodedata

RULES = json.loads(Path(__file__).with_name('catalog_classification_rules.json').read_text())


def normalized_name(name):
    return ' '.join(unicodedata.normalize('NFC', name).split()).casefold()


def category_key(name):
    name = normalized_name(name)
    if name in RULES['exact']:
        return RULES['exact'][name]
    first = re.match(r'[^\W\d_]+(?:-[^\W\d_]+)*(?![\w-])', name)
    return RULES['firstWord'].get(first[0]) if first else None


def resolve_category_ids(categories):
    by_name = defaultdict(list)
    for category in categories:
        by_name[normalized_name(category.name)].append(category.id)
    resolved = {}
    for key, rule in RULES['categories'].items():
        candidates = by_name[normalized_name(rule['name'])]
        if rule['preferredId'] in candidates:
            resolved[key] = rule['preferredId']
        elif len(candidates) == 1:
            resolved[key] = candidates[0]
    return resolved


def infer_sale_unit(name):
    units = set(re.findall(r'(?<!\w)за\s*1\s*(кг|м)(?!\w)', normalized_name(name)))
    return next(iter(units)) if len(units) == 1 else None

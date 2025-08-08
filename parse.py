from collections import defaultdict
from pathlib import Path

from itertools import islice, tee
import fitz
from fitz import Document, Page, Rect

from typing import Generator, Any, DefaultDict


def find_table_headers(page: Page, headers: list[str]) -> dict[str, Rect]:
    """Finds the bounding box for all table headers (e.g. "Test", "Current Results and Flag", etc.)

    Returns a dictionary mapping those headers to their Rectangle bounding box.
    """
    for block in page.get_text("dict", sort=True)["blocks"]:
        if 'lines' not in block:
            continue

        if [line['spans'][0]['text'] for line in block['lines']] == headers:
            return {
                line['spans'][0]['text']: Rect(line['spans'][0]['bbox'])
                for line in block['lines']
            }
    msg = f'Could not find headers {headers}!'
    raise ValueError(msg)


def find_ordered_items(page: Page) -> list[str]:
    """Finds all of the tests listed after "Ordered Items:"

    These are use for subsequent section identification.
    """
    for x0, y0, x1, y1, text, _, _ in page.get_text("blocks"):
        if text.strip().startswith('Ordered Items'):
            section_headers = [
                t.strip().replace('\n', ' ') for t in
                text.strip().removeprefix('Ordered Items:').split(';')
            ]
            return section_headers, Rect(x0, y0, x1, y1)
    msg = 'Could not find ordered items!'
    raise ValueError(msg)


def iter_section_blocks(page: Page, section_headers: list[str], stop_text: str, clip: Rect = None) -> Generator[tuple[str, dict[str, Any]], None, None]:
    """Traverse a page, producing each block and the section that was last observed.
    """
    key = None
    stop = False
    for block in page.get_text('dict', sort=True, clip=clip)['blocks']:
        lines = []
        for line in block.get('lines', []):
            spans = []
            for span in line['spans']:
                text = span['text'].strip()
                for k in section_headers:
                    if text == k or text == f'{k} (Cont.)':
                        key = k
                if text == stop_text:
                    stop = True
                    break
                spans.append(span)
            if spans:
                line['spans'] = spans
            if stop:
                break
            lines.append(line)
        if lines:
            block['lines'] = lines

        if key is not None:
            yield key, block
        if stop:
            return


def extract_tables(doc: Page) -> DefaultDict[str, list[str]]:
    # extract expected section headers based on page 0 "Ordered Items:"
    section_headers, section_header_rect = find_ordered_items(doc[0])

    # extract table header locations to guide subsequent table parsing
    headers = ['Test', 'Current Result and Flag', 'Previous Result and Date', 'Units', 'Reference Interval']
    header_rects = find_table_headers(doc[0], headers=headers)

    table_data = defaultdict(list)
    for page in doc:
        clip = None
        if page.number == 0:
            clip = Rect(*section_header_rect)
            clip.y0 = clip.y1
            clip.y1 = page.rect.height
        for section, block in iter_section_blocks(page, section_headers, stop_text='Disclaimer', clip=clip):
            row = {text: [] for text in header_rects}
            if 'lines' not in block:
                continue

            # iterates left to right within the current block
            #   align current text against extracted header info
            for line in block['lines']:
                text, font_size, text_bbox = line['spans'][0]['text'], line['spans'][0]['size'], line['bbox']
                if not text.strip() or text.startswith(' ') or font_size != 9:
                    continue

                # find the most appropriate column based on maximum area overlap
                # carve the current "column" from the table and measure its
                #   intersection against the current text
                text_bbox = Rect(*text_bbox)
                closest_header = max(
                    header_rects,
                    key=lambda k:
                        Rect(x0=header_rects[k].x0, y0=0, x1=header_rects[k].x1, y1=page.rect.height)
                        .intersect(text_bbox).get_area()
                )
                if closest_header != 'Reference Interval' and row['Reference Interval'] != []:
                    continue
                row[closest_header].append(text)
            if any(row.values()):
                table_data[section].append(row)

    return table_data

def nwise(iterable, *, n=2):
    return zip(*(islice(it, i, None) for i, it in enumerate(tee(iterable, n))))

def extract_keyvalue(page, keys):
    result = {}
    kwords = [k.split() for k in keys]
    texts = (tup[4] for tup in page.get_text("words", sort=True))
    for words in nwise(texts, n=max(len(k) for k in kwords)+1):
        for k in keys:
            if k in result:
                continue
            if all(w.startswith(k) for w, k in zip(words, k.split())):
                result[k] = words[len(k.split())]

    return result


def parse_labcorp_pdf(doc: Page) -> tuple[dict[str, str], dict[str, str], list[dict[str, str]]]:
    subject_metadata = {
        **extract_keyvalue(doc[0], keys=['DOB', 'Age', 'Sex']),
        'Name': doc[0].get_text("blocks")[0][4].split('\n', maxsplit=1)
    }

    keys = ['Date Collected', 'Date Received', 'Date Reported', 'Fasting']
    sample_metadata = extract_keyvalue(doc[0], keys=keys)
    parsed_tables = extract_tables(doc)
    skip_tests = [v.casefold() for v in ['Note', 'Urinalysis Gross Exam']]

    clean_pdf_rows = []
    for name, data in parsed_tables.items():
        for row in data:
            if any(stest in ''.join(row['Test']).casefold() for stest in skip_tests):
                continue

            # split apart "Current Result/Flag" as well as "Previous Result/Date"
            match row.pop('Current Result and Flag'):
                case [result, flag]:
                    row.update({'Current Result': result, 'Flag': flag})
                case [result]:
                    row.update({'Current Result': result, 'Flag': ''})
                case []:
                    row.update({'Current Result': '', 'Flag': ''})
                case _:
                    msg = f'Could not split "Current Result and Flag" from {row = }'
                    raise ValueError(msg)

            match row.pop('Previous Result and Date'):
                case [result, date]:
                    row.update({'Previous Result': result, 'Date': date})
                case []:
                    row.update({'Previous Result': '', 'Date': ''})
                case _:
                    msg = f'Could not split "Previous Result and Date" from {row = }'
                    raise ValueError(msg)

            clean_pdf_rows.append(
                {k: v if isinstance(v, str) else ' '.join(v) for k, v in row.items()} | {'Panel': name}
            )

    return subject_metadata, sample_metadata, clean_pdf_rows

if __name__ == '__main__':
    from argparse import ArgumentParser
    from pathlib import Path
    from pprint import pp

    parser = ArgumentParser()
    parser.add_argument('pdfs', nargs='+', help='the pdf files you wish to procecss', type=Path)
    args = parser.parse_args()

    for pdf in args.pdfs:
        print(pdf)
        with fitz.open(pdf) as doc:
            pp(parse_labcorp_pdf(doc)[1])


"""Import a complete Google Calendar course snapshot for a London date range."""
import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from parcel_core import ZONE, uid

COURSE_NAMES = {
    'DESE71003': 'IoT',
    'DESE71009': 'DEP',
    'DESE71010': 'FTRM',
    'DESE71011': 'CDE',
    'DESE71013': 'IM',
}


def import_courses(path, events, start, stop):
    previous = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
    current = {item['id']: item for item in previous
               if not start <= date.fromisoformat(item['date']) < stop}
    imported = set()
    for event in events:
        words = event.get('summary', '').split(maxsplit=1)
        code = words[0] if words else ''
        if code not in COURSE_NAMES or event.get('status') == 'cancelled':
            continue
        begin, end = (datetime.fromisoformat(event[key]) for key in ('start', 'end'))
        if not begin.tzinfo or not end.tzinfo:
            continue
        begin, end = begin.astimezone(ZONE), end.astimezone(ZONE)
        if not start <= begin.date() < stop:
            continue
        if end <= begin or (end.date() != begin.date() and end.time() > begin.time()):
            raise ValueError(f'无法导入课程 {code} 的起止时间')
        event_id = f'google-course-{event["id"]}'
        imported.add(event_id)
        current[event_id] = dict(id=event_id, title=COURSE_NAMES[code], course=True,
                                 date=begin.date().isoformat(),
                                 start=begin.strftime('%H:%M'), end=end.strftime('%H:%M'))
    result = sorted(current.values(), key=lambda item: (item['date'], item['start'], item['id']))
    if result != previous:
        temp = path.with_name(f'{path.name}.{uid()}.tmp')
        temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(path)
    return len(imported), result != previous


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    parser.add_argument('start', type=date.fromisoformat)
    parser.add_argument('stop', type=date.fromisoformat)
    args = parser.parse_args()
    if args.stop <= args.start:
        parser.error('stop must be after start')
    count, changed = import_courses(args.path, json.load(sys.stdin), args.start, args.stop)
    print(json.dumps({'courses': count, 'changed': changed}))

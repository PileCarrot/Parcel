import copy
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from parcel_core import Clock, Store, default_settings, schedule, iso, parse_dt, quick_parse, next_half_hour_delay

AT = datetime(2026, 11, 5, 9)


def task(id='a', minutes=50, **kw):
    result = dict(id=id, title=id, category='学习', estimated_minutes=minutes, worked_minutes=0,
                  min_session=25, priority=3, due='', earliest='', done=False,
                  dependencies=[], energy='正常', task_type='普通', context='')
    result.update(kw)
    return result


def dataset(*tasks, days=1):
    cfg = default_settings()
    cfg.update(horizon_days=days, reserve_enabled=False,
               availability={str(i): [['09:00', '12:00'], ['14:00', '18:00']] for i in range(7)})
    return dict(tasks=list(tasks), settings=cfg, events=[], pins=[], schedule=[], active=None)


def work(plan):
    return [b for b in plan['blocks'] if b['kind'] in ('work', 'pinned', 'active')]


class SchedulingTests(unittest.TestCase):
    def test_actual_work_excludes_breaks_and_is_deterministic(self):
        data = dataset(task(minutes=100))
        plan = schedule(data, AT)
        self.assertEqual(sum(b['minutes'] for b in work(plan)), 100)
        self.assertEqual(len(work(plan)), 4)
        self.assertEqual(work(plan)[1]['start'], '2026-11-05T09:30:00')
        self.assertEqual(schedule(data, AT), plan)
        self.assertFalse(plan['overflow'])

    def test_fixed_and_reserve_never_overwritten(self):
        data = dataset(task(minutes=500))
        data['events'] = [dict(id='e', title='课程', date='2026-11-05', weekdays=[], start='09:30', end='11:00')]
        data['settings'].update(reserve_enabled=True, reserve_start='15:00', reserve_end='16:00')
        plan = schedule(data, AT)
        for b in work(plan):
            s,e = parse_dt(b['start']), parse_dt(b['end'])
            for fs,fe in [(AT.replace(hour=9, minute=30), AT.replace(hour=11)), (AT.replace(hour=15), AT.replace(hour=16))]:
                self.assertFalse(max(s,fs) < min(e,fe))
        self.assertTrue(plan['overflow'])

    def test_deadline_and_earliest_are_hard_constraints(self):
        data = dataset(task(minutes=100, due='2026-11-05 10:00', earliest='2026-11-05 09:20'))
        plan = schedule(data, AT)
        self.assertEqual(len(work(plan)), 1)
        self.assertGreaterEqual(parse_dt(work(plan)[0]['start']), AT.replace(minute=20))
        self.assertLessEqual(parse_dt(work(plan)[0]['end']), AT.replace(hour=10))
        self.assertEqual(plan['overflow'][0]['minutes'], 75)

    def test_expired_deadline_is_visible_not_silently_moved(self):
        plan = schedule(dataset(task(due='2026-11-04')), AT)
        self.assertFalse(work(plan))
        self.assertIn('截止日期已过', plan['overflow'][0]['reason'])
        self.assertEqual(plan['health'], 'red')

    def test_dependency_finishes_before_successor_and_inherits_urgency(self):
        data = dataset(task('later', priority=5), task('pre', minutes=25),
                       task('post', minutes=25, dependencies=['pre'], due='2026-11-05 10:00'))
        plan = schedule(data, AT)
        self.assertEqual([b['task_id'] for b in work(plan)[:2]], ['pre', 'post'])
        self.assertLessEqual(work(plan)[0]['end'], work(plan)[1]['start'])
        self.assertFalse(plan['overflow'])

    def test_cycles_and_missing_dependencies_are_reported(self):
        data = dataset(task('a', dependencies=['b']), task('b', dependencies=['a']), task('c', dependencies=['gone']))
        plan = schedule(data, AT)
        self.assertFalse(work(plan))
        self.assertEqual(len(plan['overflow']), 3)
        self.assertTrue(any('循环' in x['reason'] for x in plan['overflow']))

    def test_deadline_outranks_energy_alternation(self):
        data = dataset(task('a', minutes=50, energy='高', due='2026-11-05 10:00'), task('b', minutes=25, energy='低'))
        data['settings']['energy'] = '低精力'
        self.assertEqual([b['task_id'] for b in work(schedule(data, AT))[:2]], ['a', 'a'])

    def test_remaining_work_and_completed_history_not_mutated(self):
        data = dataset(task(minutes=100, worked_minutes=50))
        original = copy.deepcopy(data)
        plan = schedule(data, AT)
        self.assertEqual(sum(b['minutes'] for b in work(plan)), 50)
        self.assertEqual(original, data)

    def test_minimum_segment_never_violated_and_short_tail_merged(self):
        for duration in [26, 40, 60, 91]:
            data = dataset(task(minutes=duration))
            plan = schedule(data, AT)
            self.assertEqual(sum(b['minutes'] for b in work(plan)), duration)
            self.assertTrue(all(b['minutes'] >= 25 for b in work(plan)))

    def test_pins_survive_and_conflicting_pins_report(self):
        data = dataset(task(minutes=50))
        pin = dict(id='pin', task_id='a', start='2026-11-05T10:00:00', end='2026-11-05T10:25:00')
        data['pins'] = [pin]
        plan = schedule(data, AT)
        self.assertTrue(any(b.get('id') == 'pin' for b in work(plan)))
        self.assertEqual(sum(b['minutes'] for b in work(plan)), 50)
        data['events'] = [dict(id='e', title='课程', date='2026-11-05', weekdays=[], start='10:00', end='11:00')]
        plan = schedule(data, AT)
        self.assertTrue(plan['conflicts'])

    def test_active_task_reserved_and_not_double_counted(self):
        data = dataset(task(minutes=50))
        data['active'] = dict(task_id='a', phase='work', total_seconds=1500, elapsed_seconds=300)
        plan = schedule(data, AT)
        self.assertEqual(sum(b['minutes'] for b in work(plan)), 45)
        self.assertEqual(work(plan)[0]['kind'], 'active')
        self.assertEqual(work(plan)[0]['minutes'], 20)

    def test_overnight_fixed_event_and_weekly_repeat(self):
        data = dataset(task(minutes=25))
        data['events'] = [dict(id='e', title='夜班', date='2026-11-04', start='23:00', end='10:00', weekdays=[])]
        self.assertEqual(work(schedule(data, AT))[0]['start'], '2026-11-05T10:00:00')

    def test_all_work_blocks_never_overlap(self):
        data = dataset(*[task(str(i), minutes=100+i*17, priority=i%5+1) for i in range(15)], days=21)
        blocks = work(schedule(data, AT))
        self.assertTrue(all(a['end'] <= b['start'] for a,b in zip(blocks, blocks[1:])))

    def test_dependency_waits_for_future_pinned_segment(self):
        data = dataset(task('a', minutes=50), task('b', minutes=25, dependencies=['a']))
        data['pins'] = [dict(id='p', task_id='a', start='2026-11-05T11:00:00', end='2026-11-05T11:25:00')]
        blocks = work(schedule(data, AT))
        successor = next(b for b in blocks if b['task_id'] == 'b')
        self.assertGreaterEqual(successor['start'], '2026-11-05T11:25:00')

    def test_break_survives_adjacent_availability_windows(self):
        data = dataset(task(minutes=50))
        data['events'] = [dict(id='e', title='短会', date='2026-11-05', weekdays=[], start='09:25', end='09:27')]
        blocks = work(schedule(data, AT))
        self.assertEqual(blocks[1]['start'], '2026-11-05T09:30:00')

    def test_context_is_respected_when_selected(self):
        data = dataset(task(context='工作室'))
        data['settings']['context'] = '家'
        self.assertFalse(work(schedule(data, AT)))


class ClockAndDataTests(unittest.TestCase):
    def test_refresh_aligned_to_wall_clock_half_hours(self):
        self.assertEqual(next_half_hour_delay(5*3600 + 29*60 + 59.5), 500)
        self.assertEqual(next_half_hour_delay(5*3600 + 30*60), 1_800_000)
        self.assertEqual(next_half_hour_delay(5*3600 + 52*60), 8*60_000)

    def test_timer_pause_and_sleep(self):
        t = [100]
        clock = Clock(1500, source=lambda: t[0])
        clock.start()
        self.assertEqual(clock.left, 1500)
        t[0] += 90
        clock.pause()
        self.assertEqual(clock.left, 1410)
        t[0] += 100
        self.assertEqual(clock.left, 1410)
        clock.start()
        t[0] += 1600
        self.assertEqual(clock.left, 0)
        self.assertEqual(clock.elapsed, 1500)

    def test_atomic_store_roundtrip_and_backup(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as root:
            path = Path(root)/'parcel.json'
            store = Store(path)
            store.data['tasks'].append(task())
            store.save()
            self.assertEqual(Store(path).data['tasks'][0]['title'], 'a')
            self.assertTrue(list((path.parent/'backups').glob('*.json')))
            path.write_text('damaged', encoding='utf-8')
            with self.assertRaises(RuntimeError):
                Store(path)

    def test_date_parsing_accepts_old_unpadded_dates(self):
        self.assertEqual(parse_dt('2026-4-10').date().isoformat(), '2026-04-10')

    def test_offline_shorthand_only_parses_explicit_fields(self):
        result = quick_parse('明天 画草图 2小时', AT)
        self.assertEqual(result['due'], '2026-11-06')
        self.assertEqual(result['estimated_minutes'], 120)
        self.assertEqual(result['title'], '画草图')


if __name__ == '__main__':
    unittest.main()

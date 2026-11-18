from datetime import datetime, timedelta, time as dtime
from decimal import Decimal
from zoneinfo import ZoneInfo
import hashlib, json
from app.coerce import instant, number

LOCAL = ZoneInfo('Asia/Shanghai')
QUANT = Decimal('.0001')

def split_piece(row):
    # 把一个能量片段按本地自然日切成半开分段，每段保留原事件与采样依据。
    start = instant(row['start']).astimezone(LOCAL)
    end = instant(row['end']).astimezone(LOCAL)
    pieces = []
    cursor = start
    while cursor < end:
        midnight = datetime.combine(cursor.date() + timedelta(days=1), dtime.min, tzinfo=LOCAL)
        edge = min(end, midnight)
        seconds = int((edge - cursor).total_seconds())
        piece = {'day': cursor.date().isoformat(), 'device': row['device'],
                 'local_start': cursor.isoformat(), 'local_end': edge.isoformat(),
                 'seconds': seconds, 'code': row['code'], 'event_ids': row['event_ids'],
                 'sample_id': row['sample_id'], 'sample_at': row['sample_at'], 'loss_kwh': None}
        if row['loss_kwh'] is not None:
            loss = max(Decimal(0), number(row['baseline_kw']) - number(row['actual_kw'])) * Decimal(seconds) / 3600
            piece['loss_kwh'] = str(loss)
        pieces.append(piece)
        cursor = edge
    return pieces

def _crossed_keys(unions):
    keys = set()
    for u in unions:
        s = instant(u['start']).astimezone(LOCAL)
        e = instant(u['end']).astimezone(LOCAL)
        if (e - timedelta(microseconds=1)).date() != s.date():
            keys.add((u['device'], tuple(u['event_ids'])))
    return keys

def settle_days(energy_rows, unions):
    crossed = _crossed_keys(unions)
    pieces = []
    for row in energy_rows:
        for piece in split_piece(row):
            piece['crossed'] = (row['device'], tuple(row['event_ids'])) in crossed
            pieces.append(piece)
    grouped = {}
    for piece in pieces:
        grouped.setdefault(piece['day'], []).append(piece)
    settled = []
    for day in sorted(grouped):
        group = sorted(grouped[day], key=lambda p: (p['local_start'], p['device']))
        known = [p for p in group if p['loss_kwh'] is not None]
        total = sum((Decimal(p['loss_kwh']) for p in known), Decimal(0))
        entry = {'day': day, 'loss_kwh': str(total.quantize(QUANT)),
                 'seconds': sum(p['seconds'] for p in group),
                 'unknown_seconds': sum(p['seconds'] for p in group if p['loss_kwh'] is None),
                 'unknown_pieces': sum(1 for p in group if p['loss_kwh'] is None),
                 'pieces': group,
                 'event_ids': sorted({e for p in group for e in p['event_ids']}),
                 'devices': sorted({p['device'] for p in group}),
                 'crossed': any(p['crossed'] for p in group)}
        entry['digest'] = hashlib.sha256(json.dumps(entry, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        settled.append(entry)
    return settled, pieces

def midnight_anomalies(unions, pieces):
    anomalies = []
    for u in unions:
        s = instant(u['start']).astimezone(LOCAL)
        e = instant(u['end']).astimezone(LOCAL)
        last = e - timedelta(microseconds=1)
        if last.date() == s.date():
            continue
        days = []
        d = s.date()
        while d <= last.date():
            days.append(d.isoformat())
            d += timedelta(days=1)
        first_midnight = datetime.combine(s.date() + timedelta(days=1), dtime.min, tzinfo=LOCAL)
        related = [p for p in pieces if p['device'] == u['device'] and list(p['event_ids']) == list(u['event_ids']) and p['day'] > days[0]]
        post_unknown = sum(p['seconds'] for p in related if p['loss_kwh'] is None)
        anomalies.append({'device': u['device'], 'event_ids': u['event_ids'],
                          'local_start': s.isoformat(), 'local_end': e.isoformat(), 'days': days,
                          'post_seconds': int((e - first_midnight).total_seconds()),
                          'post_unknown_seconds': post_unknown,
                          'status': '未定' if post_unknown else '已确定'})
    return anomalies

def apply(state):
    settled, pieces = settle_days(state['energy_rows'], state['event_unions'])
    anomalies = midnight_anomalies(state['event_unions'], pieces)
    balance = state['balance']
    raw_total = sum((Decimal(p['loss_kwh']) for p in pieces if p['loss_kwh'] is not None), Decimal(0))
    check = {'loss_balanced': raw_total.quantize(QUANT) == Decimal(balance['loss_kwh']),
             'time_balanced': sum(d['seconds'] for d in settled) == balance['slice_seconds']
                              and sum(d['unknown_seconds'] for d in settled) == balance['unknown_seconds']}
    state['daily'] = settled
    state['midnight'] = anomalies
    state['settle_check'] = check
    rows = [{'day': d['day'], 'loss_kwh': d['loss_kwh'], 'seconds': d['seconds'],
             'unknown_seconds': d['unknown_seconds'], 'unknown_pieces': d['unknown_pieces'],
             'piece_count': len(d['pieces']), 'event_ids': d['event_ids'], 'crossed': d['crossed']} for d in settled]
    state['views']['settle'] = {'title': '日结汇总', 'rows': rows,
        'metrics': {'结算天数': len(settled), '跨零事件': len(anomalies), '跨零未定s': sum(a['post_unknown_seconds'] for a in anomalies)}}

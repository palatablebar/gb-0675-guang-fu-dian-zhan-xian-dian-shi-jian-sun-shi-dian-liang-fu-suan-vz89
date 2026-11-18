import pytest
from decimal import Decimal
from fastapi.testclient import TestClient
from app.engine import evaluate
from app.modules.settle.rules import split_piece
from app import main, store

@pytest.fixture
def overnight():
    return evaluate({'dataset':'overnight'})

def client(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'DB_PATH',str(tmp_path/'test.sqlite'));return TestClient(main.app)

def test_clean_single_day_settled(clean):
    assert [d['day'] for d in clean['daily']]==['2026-10-15']
    assert clean['daily'][0]['loss_kwh']=='26.6667'
    assert clean['midnight']==[]
    assert clean['settle_check']=={'loss_balanced':True,'time_balanced':True}

def test_dirty_unknown_stays_with_day(sample):
    day=sample['daily'][0]
    assert day['unknown_seconds']==1200 and day['unknown_pieces']==2
    assert sum(Decimal(d['loss_kwh']) for d in sample['daily'])==Decimal(sample['balance']['loss_kwh'])

def test_overnight_split_two_days(overnight):
    days={d['day']:d for d in overnight['daily']}
    assert list(days)==['2026-10-15','2026-10-16']
    # 整段记开始日会让次日少计：10-15只结23:50-24:00的10分钟，10-16结00:00-00:20的20分钟。
    assert days['2026-10-15']['loss_kwh']=='10.0000' and days['2026-10-15']['unknown_seconds']==0
    assert days['2026-10-16']['loss_kwh']=='13.3333' and days['2026-10-16']['unknown_seconds']==900
    assert days['2026-10-16']['crossed'] is True

def test_overnight_conservation(overnight):
    assert sum(Decimal(d['loss_kwh']) for d in overnight['daily'])==Decimal(overnight['balance']['loss_kwh'])
    assert sum(d['seconds'] for d in overnight['daily'])==overnight['balance']['slice_seconds']
    assert sum(d['unknown_seconds'] for d in overnight['daily'])==overnight['balance']['unknown_seconds']
    assert overnight['settle_check']=={'loss_balanced':True,'time_balanced':True}

def test_cross_midnight_anomalies(overnight):
    anomalies={a['device']:a for a in overnight['midnight']}
    assert set(anomalies)=={'INV-A','INV-B'}
    a=anomalies['INV-A']
    assert a['event_ids']==['EA-N1'] and a['days']==['2026-10-15','2026-10-16']
    assert a['post_seconds']==1200 and a['post_unknown_seconds']==0 and a['status']=='已确定'
    b=anomalies['INV-B']
    assert b['event_ids']==['EB-N1'] and b['post_seconds']==900
    assert b['post_unknown_seconds']==900 and b['status']=='未定'

def test_no_adjacent_day_backfill(overnight):
    # 跨零后无有效证据的分段必须保持未定，不能拿相邻日数据补齐。
    piece=next(p for d in overnight['daily'] for p in d['pieces'] if p['device']=='INV-B' and p['day']=='2026-10-16')
    assert piece['loss_kwh'] is None and piece['code']=='TARGET_GAP' and piece['sample_id']==''
    assert piece['seconds']==900

def test_piece_traceable_to_event(overnight):
    unions={(u['device'],tuple(u['event_ids'])) for u in overnight['event_unions']}
    for d in overnight['daily']:
        for p in d['pieces']:
            assert p['event_ids'] and (p['device'],tuple(p['event_ids'])) in unions
            assert p['sample_id'] or p['loss_kwh'] is None

def test_midnight_boundary_exact():
    base={'device':'INV-A','seconds':600,'code':'LOSS','event_ids':['E1'],'sample_id':'S1',
          'sample_at':'2026-10-15T15:50:00+00:00','loss_kwh':'6.6667','actual_kw':'60','baseline_kw':'100'}
    # 结束于零点不算跨零，整天归开始日。
    row=dict(base,start='2026-10-15T15:50:00+00:00',end='2026-10-15T16:00:00+00:00')
    pieces=split_piece(row);assert len(pieces)==1 and pieces[0]['day']=='2026-10-15'
    # 零点起算归次日。
    row=dict(base,start='2026-10-15T16:00:00+00:00',end='2026-10-15T16:10:00+00:00')
    assert split_piece(row)[0]['day']=='2026-10-16'
    # 跨零片段切开且能量守恒。
    row=dict(base,start='2026-10-15T15:55:00+00:00',end='2026-10-15T16:05:00+00:00')
    pieces=split_piece(row)
    assert [p['day'] for p in pieces]==['2026-10-15','2026-10-16'] and [p['seconds'] for p in pieces]==[300,300]
    assert sum(Decimal(p['loss_kwh']) for p in pieces).quantize(Decimal('.0001'))==Decimal('6.6667')

def test_settlement_rerun_no_duplicates_no_overwrite(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    run=c.post('/api/runs',json={'dataset':'overnight'}).json();rid=run['manifest']['run_id']
    assert [d['day'] for d in run['daily']]==['2026-10-15','2026-10-16']
    assert c.post(f'/api/runs/{rid}/settle/2026-10-15/review',json={'revision':1}).status_code==200
    # 同一天重跑：不新增、不覆盖已复核日结、不丢数据。
    c.post('/api/runs',json={'dataset':'overnight'});c.post(f'/api/runs/{rid}/rerun')
    persisted={s['day']:s for s in store.settlements(str(tmp_path/'test.sqlite'),rid)}
    assert len(persisted)==2
    assert persisted['2026-10-15']['status']=='已复核' and persisted['2026-10-15']['revision']==2
    assert persisted['2026-10-16']['status']=='待复核' and persisted['2026-10-16']['revision']==1
    # 陈旧版本409，不存在的日结404。
    assert c.post(f'/api/runs/{rid}/settle/2026-10-15/review',json={'revision':1}).status_code==409
    assert c.post(f'/api/runs/{rid}/settle/2026-10-17/review',json={'revision':1}).status_code==404
    assert c.post(f'/api/runs/{rid}/settle/2026-10-16/review',json={'revision':0}).status_code==422

def test_settle_page_shows_version_and_anomaly(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    rid=c.post('/api/runs',json={'dataset':'overnight'}).json()['manifest']['run_id']
    html=c.get('/settle?run_id='+rid).text
    assert '日结版本' in html and '跨零异常' in html and '2026-10-16' in html
    assert 'EA-N1' in html and '未定' in html
    assert c.post('/api/settle',json={'dataset':'overnight'}).status_code==200

def test_legacy_run_upgraded_not_lost(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    state=evaluate({'dataset':'clean'})
    old={'manifest':{**state['manifest'],'run_id':'oldrun0000000001','sha256':'0'*64},'source':'clean','views':{},
         'input_snapshot':state['input_snapshot'],'counts':state['counts'],'quarantine':[]}
    store.save(str(tmp_path/'test.sqlite'),old)
    response=c.get('/settle?run_id=oldrun0000000001',follow_redirects=False)
    assert response.status_code==303
    # 旧批次仍在，未因升级丢失。
    assert c.get('/api/runs/oldrun0000000001').status_code==200

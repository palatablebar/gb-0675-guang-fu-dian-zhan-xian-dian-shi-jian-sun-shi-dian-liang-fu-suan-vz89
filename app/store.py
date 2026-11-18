import sqlite3, json
from pathlib import Path
from app.errors import BusinessError

def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con=sqlite3.connect(path,timeout=15)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, result TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT '待复核')")
    con.execute("CREATE TABLE IF NOT EXISTS settlements (run_id TEXT NOT NULL, day TEXT NOT NULL, digest TEXT NOT NULL, result TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT '待复核', PRIMARY KEY (run_id, day))")
    return con

def save(path, result):
    run_id=result['manifest']['run_id']
    with connect(path) as con:
        con.execute('INSERT OR IGNORE INTO runs(id,digest,result) VALUES(?,?,?)', (run_id,result['manifest']['sha256'],json.dumps(result,ensure_ascii=False)))
        # 日结按(批次,结算日)幂等落库：同批重跑不新增、不覆盖已复核，已落库日结保持原样。
        for day in result.get('daily',[]):
            con.execute('INSERT OR IGNORE INTO settlements(run_id,day,digest,result) VALUES(?,?,?,?)', (run_id,day['day'],day['digest'],json.dumps(day,ensure_ascii=False)))
    return get(path,run_id)

def get(path, run_id):
    with connect(path) as con: row=con.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if not row: raise BusinessError('NOT_FOUND','批次不存在',404)
    result=json.loads(row['result']);result.update(revision=row['revision'],status=row['status'])
    return result

def history(path):
    with connect(path) as con:
        return [dict(r) for r in con.execute('SELECT id,revision,status FROM runs ORDER BY rowid DESC LIMIT 100')]

def review(path,run_id,revision):
    with connect(path) as con:
        cur=con.execute('UPDATE runs SET revision=revision+1,status=? WHERE id=? AND revision=?',('已复核',run_id,revision))
        if cur.rowcount!=1:
            if not con.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():
                raise BusinessError('NOT_FOUND','批次不存在',404)
            raise BusinessError('STALE_VERSION','复核版本已更新，请刷新',409)
    return get(path,run_id)

def settlements(path,run_id):
    with connect(path) as con:
        return [dict(r) for r in con.execute('SELECT run_id,day,revision,status,digest FROM settlements WHERE run_id=? ORDER BY day',(run_id,))]

def review_settlement(path,run_id,day,revision):
    with connect(path) as con:
        cur=con.execute("UPDATE settlements SET revision=revision+1,status='已复核' WHERE run_id=? AND day=? AND revision=?",(run_id,day,revision))
        if cur.rowcount!=1:
            if not con.execute('SELECT 1 FROM settlements WHERE run_id=? AND day=?',(run_id,day)).fetchone():
                raise BusinessError('NOT_FOUND','日结不存在',404)
            raise BusinessError('STALE_VERSION','日结版本已更新，请刷新',409)
    return settlements(path,run_id)

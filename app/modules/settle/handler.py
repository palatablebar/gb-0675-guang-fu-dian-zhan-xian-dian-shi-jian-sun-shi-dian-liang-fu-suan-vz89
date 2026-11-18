from fastapi import APIRouter, Request, Body
from app.engine import evaluate
from app.web import render
from app.errors import BusinessError
from app import store
router=APIRouter()
@router.get('/settle')
def page(request:Request):
    return render(request,'settle')
@router.post('/api/settle')
def calculate(payload:dict=Body(default={})):
    return evaluate(payload,until=8)
@router.post('/api/runs/{run_id}/settle/{day}/review')
def review_day(run_id:str,day:str,payload:dict=Body(...)):
    from app.main import DB_PATH
    revision=payload.get('revision')
    if not isinstance(revision,int) or revision<1: raise BusinessError('BAD_REVISION','版本号须为正整数')
    return store.review_settlement(DB_PATH,run_id,day,revision)

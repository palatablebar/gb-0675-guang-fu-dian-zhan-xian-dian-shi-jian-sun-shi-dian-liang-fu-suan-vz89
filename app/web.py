from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from app.config import ROOT, META
from app.engine import evaluate
from app.errors import BusinessError
from app import store

templates=Jinja2Templates(directory=str(ROOT/'app/templates'))

def render(request:Request,key):
    from app.main import DB_PATH
    run_id=request.query_params.get('run_id')
    if run_id:
        state=store.get(DB_PATH,run_id)
        if key not in state['views']:
            # 旧规则批次缺少新模块视图时，用不可变输入快照按当前规则升级，原批次保留不丢。
            if 'input_snapshot' not in state:
                raise BusinessError('INCOMPLETE_RUN','该批缺少此模块结果，请重新复算')
            fresh=store.save(DB_PATH,evaluate({'dataset':state['source']},snapshot=state['input_snapshot']))
            if fresh['manifest']['run_id']==run_id:
                raise BusinessError('INCOMPLETE_RUN','该批缺少此模块结果，请重新复算')
            return RedirectResponse(f"/{key}?run_id={fresh['manifest']['run_id']}",status_code=303)
    else:
        state=store.save(DB_PATH,evaluate({'dataset':request.query_params.get('dataset','dirty')}))
    context={'meta':META,'key':key,'state':state,'view':state['views'][key]}
    if key=='settle':
        context['persisted']={row['day']:row for row in store.settlements(DB_PATH,state['manifest']['run_id'])}
    return templates.TemplateResponse(request=request,name=f'{key}.html',context=context)

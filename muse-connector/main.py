"""Bearer-protected Muse connector for the Huobao Drama API."""
from __future__ import annotations
import base64, binascii, hmac, json, os
from pathlib import PurePosixPath
from typing import Any
import httpx
from mcp.server.fastmcp import FastMCP

API_BASE=os.getenv('HUOBAO_API_BASE','http://app:5679/api/v1').rstrip('/')
TOKEN=os.getenv('MUSE_BRIDGE_TOKEN','')
ALLOW={int(x) for x in os.getenv('ALLOWED_DRAMA_IDS','').split(',') if x.strip().isdigit()}
STATE_PATH=os.getenv('MUSE_STATE_PATH','/data/state.json')
MAX_IMAGE_BYTES=30*1024*1024
MAX_VIDEO_BYTES=100*1024*1024
mcp=FastMCP('Huobao Drama Muse Connector',stateless_http=True)

class HuobaoError(RuntimeError):
    def __init__(self, reason:str, status:int|None=None, code:Any=None):
        super().__init__(reason); self.status=status; self.code=code; self.reason=reason
    def data(self): return {'ok':False,'http_status':self.status,'error_code':self.code,'reason':self.reason}

def state():
    try:
        with open(STATE_PATH,encoding='utf-8') as f: return json.load(f)
    except (OSError,ValueError): return {'registered_drama_ids':[],'retries':{}}
def save_state(data):
    os.makedirs(os.path.dirname(STATE_PATH) or '.',exist_ok=True); tmp=STATE_PATH+'.tmp'
    with open(tmp,'w',encoding='utf-8') as f: json.dump(data,f)
    os.replace(tmp,STATE_PATH)
async def request(method,path,*,json_body=None,files=None):
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60,read=600)) as c: r=await c.request(method,API_BASE+path,json=json_body,files=files)
    except httpx.HTTPError as e: raise HuobaoError(f'后端不可达: {e}',502,'NETWORK_ERROR') from e
    try: p=r.json()
    except ValueError: p={'message':r.text[:1000]}
    if r.is_error or (isinstance(p,dict) and p.get('code',200)>=400):
        raise HuobaoError(str(p.get('message') or p.get('error') or f'HTTP {r.status_code}'),r.status_code,p.get('code'))
    return p.get('data',p) if isinstance(p,dict) else p

def check(did):
    if ALLOW and did not in ALLOW and did not in state().get('registered_drama_ids',[]): raise HuobaoError('剧集不在 ALLOWED_DRAMA_IDS 允许范围内',403,'DRAMA_NOT_ALLOWED')
async def drama(did):
    check(did); d=await request('GET',f'/dramas/{did}')
    if not isinstance(d,dict) or int(d.get('id',0))!=did: raise HuobaoError('剧集不存在',404,'NOT_FOUND')
    return d
async def episode(eid):
    dramas=await request('GET','/dramas')
    rows=dramas.get('items',[]) if isinstance(dramas,dict) else dramas
    for x in rows or []:
        did=int(x.get('id',0))
        try: d=await drama(did)
        except HuobaoError: continue
        for e in d.get('episodes',[]):
            if int(e.get('id',0))==eid:return d,e
    raise HuobaoError('集不存在或无权访问',404,'NOT_FOUND')
async def board(bid):
    rows=await request('GET','/dramas'); rows=rows.get('items',[]) if isinstance(rows,dict) else rows
    for x in rows or []:
        try:d=await drama(int(x.get('id',0)))
        except HuobaoError:continue
        for e in d.get('episodes',[]):
            bs=await request('GET',f"/episodes/{e['id']}/storyboards")
            for b in bs or []:
                if int(b.get('id',0))==bid:return d,e,b
    raise HuobaoError('分镜不存在或无权访问',404,'NOT_FOUND')
async def count_rel(d):
    eps=d.get('episodes') or []
    boards=0
    for e in eps:
        rows=await request('GET',f"/episodes/{e['id']}/storyboards")
        boards += len(rows or [])
    return {'episodes':len(eps),'storyboards':boards,'assets':sum(len(d.get(k) or []) for k in ('characters','scenes','props'))}
def parse(s):
    try: x=json.loads(s)
    except Exception as e: raise HuobaoError('fields_json 必须是合法 JSON') from e
    if not isinstance(x,dict):raise HuobaoError('fields_json 必须是 JSON 对象')
    return x

@mcp.tool()
async def list_dramas():
    rows=await request('GET','/dramas'); rows=rows.get('items',[]) if isinstance(rows,dict) else rows
    return [x for x in (rows or []) if not ALLOW or int(x.get('id',0)) in ALLOW or int(x.get('id',0)) in state().get('registered_drama_ids',[])]
@mcp.tool()
async def create_drama(title:str,description:str=''):
    d=await request('POST','/dramas',json_body={'title':title,'description':description}); did=int(d['id'])
    s=state(); s['registered_drama_ids']=sorted(set(s.get('registered_drama_ids',[])+[did])); save_state(s)
    return {'drama_id':did,'title':title,'read_write_available':True}
@mcp.tool()
async def delete_drama(drama_id:int):
    try:
        d=await drama(drama_id); counts=await count_rel(d); await request('DELETE',f'/dramas/{drama_id}')
        s=state(); s['registered_drama_ids']=[x for x in s.get('registered_drama_ids',[]) if x!=drama_id]; save_state(s)
        return {'ok':True,'deleted_id':drama_id,'type':'drama','related_before_delete':counts}
    except HuobaoError as e:return {'deleted_id':drama_id,'type':'drama',**e.data()}
@mcp.tool()
async def get_drama(drama_id:int): return await drama(drama_id)
@mcp.tool()
async def get_episode(episode_id:int): return (await episode(episode_id))[1]
@mcp.tool()
async def update_episode_script(episode_id:int,script_content:str):
    await episode(episode_id); return await request('PUT',f'/episodes/{episode_id}',json_body={'script_content':script_content})
@mcp.tool()
async def create_episode(drama_id:int,title:str,order:int|None=None):
    d=await drama(drama_id); num=order or len(d.get('episodes',[]))+1
    await request('PUT',f'/dramas/{drama_id}/episodes',json_body={'episodes':[{'episode_number':num,'title':title}]})
    d=await drama(drama_id); e=next((x for x in d.get('episodes',[]) if int(x.get('episode_number',0))==num),None)
    return {'episode_id':e.get('id') if e else None,'episode_number':num,'title':title}
@mcp.tool()
async def update_episode(episode_id:int,fields_json:str):
    await episode(episode_id); fields=parse(fields_json); allowed={'title','description','script_content','status','resolution','content'}
    if not fields or set(fields)-allowed:raise HuobaoError(f'仅支持字段: {sorted(allowed)}')
    return await request('PUT',f'/episodes/{episode_id}',json_body=fields)
@mcp.tool()
async def delete_episode(episode_id:int):
    try:
        d,e=await episode(episode_id); bs=await request('GET',f'/episodes/{episode_id}/storyboards'); n=len(bs or [])
        await request('DELETE',f'/episodes/{episode_id}')
        return {'ok':True,'deleted_id':episode_id,'type':'episode','related_before_delete':{'storyboards':n}}
    except HuobaoError as x:return {'deleted_id':episode_id,'type':'episode',**x.data()}

@mcp.tool()
async def list_storyboards(episode_id:int):
    await episode(episode_id); return await request('GET',f'/episodes/{episode_id}/storyboards')
@mcp.tool()
async def create_storyboard(episode_id:int,fields_json:str):
    await episode(episode_id); f=parse(fields_json); f['episode_id']=episode_id
    allowed={'episode_id','storyboard_number','title','description','scene_id','duration','character_ids','prop_ids'}
    if set(f)-allowed: raise HuobaoError('创建分镜只接受后端创建路由支持字段: '+', '.join(sorted(allowed)))
    return await request('POST','/storyboards',json_body=f)
@mcp.tool()
async def update_storyboard(storyboard_id:int,fields_json:str):
    await board(storyboard_id); f=parse(fields_json)
    allowed={'title','description','shot_type','angle','movement','duration','video_prompt','image_prompt','scene_id','location','time','atmosphere','result','bgm_prompt','sound_effect','video_url','composed_image','first_frame_image','last_frame_image'}
    if not f or set(f)-allowed:raise HuobaoError(f'仅支持字段: {sorted(allowed)}')
    return await request('PUT',f'/storyboards/{storyboard_id}',json_body=f)
@mcp.tool()
async def delete_storyboard(storyboard_id:int):
    try:
        _,_,b=await board(storyboard_id); links={k:len(b.get(k) or []) for k in ('characters','props')}
        await request('DELETE',f'/storyboards/{storyboard_id}')
        return {'ok':True,'deleted_id':storyboard_id,'type':'storyboard','related_before_delete':links}
    except HuobaoError as e:return {'deleted_id':storyboard_id,'type':'storyboard',**e.data()}

ENTITY={'character':('characters',{'name','role','description','appearance','styling','image_url','local_path','final_prompt'}),'scene':('scenes',{'name','location','time','prompt','description','lighting','image_url','local_path','final_prompt'}),'prop':('props',{'name','type','description','image_url','local_path','final_prompt'})}
async def entity_create(kind,drama_id,fields):
    d=await drama(drama_id); route,allowed=ENTITY[kind]
    if set(fields)-allowed:raise HuobaoError(f'{kind} 字段不受支持: {sorted(set(fields)-allowed)}')
    fields={**fields,'drama_id':drama_id}
    return await request('POST',f'/{route}',json_body=fields)
async def entity_row(kind,drama_id,eid):
    d=await drama(drama_id); key=ENTITY[kind][0]; row=next((x for x in d.get(key,[]) if int(x.get('id',0))==eid),None)
    if row is None:raise HuobaoError(f'{kind} 不属于该剧或不存在',404,'NOT_FOUND')
    return row
def register_entity_tools(kind):
    async def create_tool(drama_id: int, fields_json: str):
        return await entity_create(kind, drama_id, parse(fields_json))
    create_tool.__name__ = 'create_' + kind
    create_tool.__doc__ = f'Create a {kind} using Huobao API.'
    mcp.tool()(create_tool)

    async def update_tool(drama_id: int, entity_id: int, fields_json: str):
        await entity_row(kind, drama_id, entity_id)
        fields = parse(fields_json)
        allowed = ENTITY[kind][1]
        if not fields or set(fields) - allowed:
            raise HuobaoError(f'不支持字段: {sorted(set(fields)-allowed)}')
        return await request('PUT', f'/{ENTITY[kind][0]}/{entity_id}', json_body=fields)
    update_tool.__name__ = 'update_' + kind
    update_tool.__doc__ = f'Update a {kind}.'
    mcp.tool()(update_tool)

    async def delete_tool(drama_id: int, entity_id: int):
        try:
            await entity_row(kind, drama_id, entity_id)
            d = await drama(drama_id)
            links = sum(1 for e in d.get('episodes', []) if entity_id in (e.get(kind + 's') or []))
            await request('DELETE', f'/{ENTITY[kind][0]}/{entity_id}')
            return {'ok': True, 'deleted_id': entity_id, 'type': kind,
                    'related_before_delete': {'episode_links': links}}
        except HuobaoError as e:
            return {'deleted_id': entity_id, 'type': kind, **e.data()}
    delete_tool.__name__ = 'delete_' + kind
    delete_tool.__doc__ = f'Delete a {kind}.'
    mcp.tool()(delete_tool)

for _kind in ('character', 'scene', 'prop'):
    register_entity_tools(_kind)

async def upload_bytes(filename,b64,limit,kind):
    try:data=base64.b64decode(b64,validate=True)
    except (binascii.Error,ValueError) as e:raise HuobaoError('base64_data 无效') from e
    if not data:raise HuobaoError('文件不能为空')
    if len(data)>limit:raise HuobaoError(f'文件实际大小 {len(data)} bytes，超过上限 {limit} bytes ({limit//1048576} MiB)')
    safe=PurePosixPath(filename.replace('\\','/')).name
    return await request('POST',f'/upload/{kind}',files={'file':(safe,data,'application/octet-stream')})
@mcp.tool()
async def upload_storyboard_media(storyboard_id:int,media_kind:str,filename:str,base64_data:str):
    await board(storyboard_id); fields={'storyboard_image':('image','composed_image',MAX_IMAGE_BYTES),'first_frame':('image','first_frame_image',MAX_IMAGE_BYTES),'last_frame':('image','last_frame_image',MAX_IMAGE_BYTES),'video':('video','video_url',MAX_VIDEO_BYTES)}
    if media_kind not in fields:raise HuobaoError('media_kind 只能是 storyboard_image/first_frame/last_frame/video')
    typ,col,limit=fields[media_kind]; up=await upload_bytes(filename,base64_data,limit,typ); path=up.get('path')
    await request('PUT',f'/storyboards/{storyboard_id}',json_body={col:path}); return {'storyboard_id':storyboard_id,'media_kind':media_kind,'path':path}
@mcp.tool()
async def delete_storyboard_media(storyboard_id:int,media_kind:str):
    try:
        await board(storyboard_id); cols={'storyboard_image':'composed_image','first_frame':'first_frame_image','last_frame':'last_frame_image','video':'video_url'}
        if media_kind not in cols:raise HuobaoError('media_kind 无效')
        await request('PUT',f'/storyboards/{storyboard_id}',json_body={cols[media_kind]:''})
        return {'ok':True,'deleted_id':storyboard_id,'type':'storyboard_'+media_kind,'related_before_delete':{'record_field':cols[media_kind],'cleared':True,'physical_file_deleted':False}}
    except HuobaoError as e:return {'deleted_id':storyboard_id,'type':'storyboard_media',**e.data()}
@mcp.tool()
async def upload_drama_asset(drama_id:int,entity_type:str,entity_id:int,filename:str,base64_data:str):
    route=ENTITY.get(entity_type)
    if not route:raise HuobaoError('entity_type must be character, scene, or prop')
    await entity_row(entity_type,drama_id,entity_id); up=await upload_bytes(filename,base64_data,MAX_IMAGE_BYTES,'image'); path=up.get('path')
    await request('PUT',f'/{route[0]}/{entity_id}',json_body={'image_url':path,'local_path':path})
    return {'drama_id':drama_id,'entity_type':entity_type,'entity_id':entity_id,'path':path}
@mcp.tool()
async def delete_drama_asset(drama_id:int,entity_type:str,entity_id:int):
    try:
        route=ENTITY.get(entity_type)
        if not route:raise HuobaoError('entity_type must be character, scene, or prop')
        await entity_row(entity_type,drama_id,entity_id); await request('PUT',f'/{route[0]}/{entity_id}',json_body={'image_url':'','local_path':''})
        return {'ok':True,'deleted_id':entity_id,'type':entity_type+'_asset','related_before_delete':{'record_fields_cleared':['image_url','local_path'],'physical_file_deleted':False}}
    except HuobaoError as e:return {'deleted_id':entity_id,'type':entity_type+'_asset',**e.data()}

@mcp.tool()
async def import_drama_bundle(bundle_json:str,drama_id:int|None=None,episode_offset:int=0):
    b=parse(bundle_json)
    if not isinstance(b.get('episodes'),list):raise HuobaoError('bundle_json.episodes 必须为数组')
    if drama_id is None: drama_id=(await create_drama(str(b.get('title','Imported drama'))))['drama_id']
    d=await drama(drama_id); report={'drama_id':drama_id,'episode_offset':episode_offset,'created_episodes':[],'created_storyboards':[],'reused_entities':[],'created_entities':[],'uploaded_assets':[],'failures':[],'warnings':[]}
    # Entity identity is name-based. Existing entities are reused; backend currently has no attach-existing-to-episode endpoint.
    existing={k:{x.get('name'):x for x in d.get(ENTITY[k][0],[])} for k in ENTITY}
    names={k:set(v) for k,v in existing.items()}
    for asset in b.get('assets',[]):
        kind=asset.get('kind'); name=asset.get('name');
        if kind not in ENTITY or not name:report['failures'].append({'kind':'asset','name':name,'reason':'未知 kind 或缺 name'});continue
        row=existing[kind].get(name)
        try:
            if row: report['reused_entities'].append({'kind':kind,'name':name,'id':row['id']})
            else:
                result=await entity_create(kind,drama_id,({'location':name} if kind=='scene' else {'name':name})); eid=int(result['id']); row={'id':eid,'name':name}; existing[kind][name]=row; report['created_entities'].append({'kind':kind,'name':name,'id':eid})
            await upload_drama_asset(drama_id,kind,int(row['id']),str(asset.get('filename','asset.jpg')),str(asset.get('base64_data','')))
            report['uploaded_assets'].append({'kind':kind,'name':name,'id':row['id']})
        except HuobaoError as e:report['failures'].append({'kind':'asset','name':name,'reason':e.reason})
    for i,epdata in enumerate(b['episodes']):
        number=episode_offset+i+1
        try:
            d=await drama(drama_id); e=next((x for x in d.get('episodes',[]) if int(x.get('episode_number',0))==number),None)
            if not e:
                await request('PUT',f'/dramas/{drama_id}/episodes',json_body={'episodes':[{'episode_number':number,'title':epdata.get('title',''),'script_content':epdata.get('script_content','')}]})
                d=await drama(drama_id); e=next((x for x in d.get('episodes',[]) if int(x.get('episode_number',0))==number),None)
            if not e:raise HuobaoError('后端创建集后未能查回记录')
            report['created_episodes'].append({'episode_number':number,'episode_id':e['id']})
        except HuobaoError as er:report['failures'].append({'episode_number':number,'storyboard_number':None,'reason':er.reason});continue
        for j,sb in enumerate(epdata.get('storyboards',[]),1):
            try:
                # Resolve linked assets by names when available; string scene_id is an external label, not a DB id.
                names_for=lambda k:[existing[k][n]['id'] for n in sb.get('characters' if k=='character' else 'props',[]) if n in existing[k]]
                scene_name=sb.get('scene'); scene=existing['scene'].get(scene_name)
                payload={'episode_id':e['id'],'storyboard_number':j,'title':sb.get('title',''),'description':'','duration':0,'character_ids':names_for('character'),'prop_ids':names_for('prop')}
                if scene:payload['scene_id']=scene['id']
                result=await request('POST','/storyboards',json_body=payload); sid=result.get('id') if isinstance(result,dict) else None
                if not sid: sid=result.get('storyboard',{}).get('id') if isinstance(result,dict) else None
                if not sid:raise HuobaoError('后端创建分镜未返回 ID')
                fields={k:sb[k] for k in ('image_prompt','video_prompt') if sb.get(k) is not None}
                if fields:await request('PUT',f'/storyboards/{sid}',json_body=fields)
                report['created_storyboards'].append({'episode_number':number,'storyboard_number':j,'storyboard_id':sid})
                if sb.get('utterances'):report['warnings'].append({'episode_number':number,'storyboard_number':j,'reason':'上游无 utterances 持久化字段，未写入'})
                if sb.get('characters') or sb.get('props'):report['warnings'].append({'episode_number':number,'storyboard_number':j,'reason':'上游没有为已存在角色/道具关联到新集的 API；需先确保关系存在'})
            except HuobaoError as er:report['failures'].append({'episode_number':number,'storyboard_number':j,'reason':er.reason})
    return report
@mcp.tool()
async def verify_drama(drama_id:int):
    d=await drama(drama_id); eps=d.get('episodes',[]) or []; allb=[]
    for e in eps:allb.extend(await request('GET',f"/episodes/{e['id']}/storyboards") or [])
    missing=[]
    for k in ENTITY:
        for x in d.get(ENTITY[k][0],[]) or []:
            if not (x.get('image_url') or x.get('local_path')):missing.append({'kind':k,'name':x.get('name')})
    return {'drama_id':drama_id,'episode_count':len(eps),'storyboard_count':len(allb),'storyboard_images':sum(bool(x.get('composed_image')) for x in allb),'storyboard_videos':sum(bool(x.get('video_url')) for x in allb),'character_count':len(d.get('characters',[]) or []),'scene_count':len(d.get('scenes',[]) or []),'prop_count':len(d.get('props',[]) or []),'missing_images':missing}

STATUS={'pending':'排队中','queued':'排队中','processing':'进行中','success':'成功','completed':'成功','failed':'失败'}
def task_norm(t):
    def g(a,b):return t.get(a,t.get(b))
    return {'task_id':g('id','id'),'type':g('type','type'),'drama_id':g('dramaId','drama_id'),'episode_id':None,'storyboard_id':g('storyboardId','storyboard_id'),'status':STATUS.get(g('status','status'),g('status','status')),'progress_percent':None,'progress_note':'后端任务接口不提供百分比进度','failure_reason':g('errorMsg','error_msg'),'created_at':g('createdAt','created_at'),'updated_at':g('updatedAt','updated_at'),'retry_count':state().get('retries',{}).get(str(g('id','id')),0)}
@mcp.tool()
async def list_tasks(drama_id:int|None=None):
    q='?drama_id='+str(drama_id) if drama_id is not None else ''; rows=await request('GET','/tasks'+q); rows=rows.get('items',rows) if isinstance(rows,dict) else rows
    return [task_norm(t) for t in rows or [] if not ALLOW or int(t.get('dramaId',t.get('drama_id')) or 0) in ALLOW or int(t.get('dramaId',t.get('drama_id')) or 0) in state().get('registered_drama_ids',[])]
@mcp.tool()
async def get_task(task_id:int):
    t=await request('GET',f'/tasks/{task_id}')
    if t and ALLOW:
        did=int(t.get('dramaId',t.get('drama_id')) or 0)
        if did not in ALLOW and did not in state().get('registered_drama_ids',[]): raise HuobaoError('任务所属剧不在允许范围内',403,'DRAMA_NOT_ALLOWED')
    return task_norm(t) if t else None
@mcp.tool()
async def retry_task(task_id:int):
    t=await request('GET',f'/tasks/{task_id}')
    if t and ALLOW:
        did=int(t.get('dramaId',t.get('drama_id')) or 0)
        if did not in ALLOW and did not in state().get('registered_drama_ids',[]): raise HuobaoError('任务所属剧不在允许范围内',403,'DRAMA_NOT_ALLOWED')
    if not t or t.get('status')!='failed':raise HuobaoError('仅失败任务可重试',400,'NOT_RETRYABLE')
    st=state(); retries=st.setdefault('retries',{})
    if retries.get(str(task_id),0)>=1:raise HuobaoError('该任务已重试过一次',409,'RETRY_LIMIT')
    raw=t.get('params')
    try: params=json.loads(raw) if isinstance(raw,str) else (raw or {})
    except (TypeError,ValueError): params={}
    body={'type':t.get('type'),'drama_id':t.get('dramaId'),'storyboard_id':t.get('storyboardId'),
          'scene_id':t.get('sceneId'),'character_id':t.get('characterId'),'prop_id':t.get('propId'),
          'prompt':t.get('prompt'),'model':t.get('model'),'config_id':t.get('configId'),**params}
    if body['type']=='video': body['force_new']=True
    try:
        new=await request('POST','/tasks',json_body=body)
    except HuobaoError as e:
        return {'old_task_id':task_id,'type':t.get('type'),**e.data()}
    new_id=new.get('id') if isinstance(new,dict) else None
    if new_id is None: raise HuobaoError('后端重试响应未返回新任务 ID',502,'INVALID_TASK_RESPONSE')
    retries[str(task_id)]=1; retries[str(new_id)]=0; save_state(st)
    return {'old_task_id':task_id,'old_task_status':'failed','new_task_id':new_id,'type':t.get('type'),'retry_count':1,'old_task_unchanged':True}

def authorized(inner):
    async def app(scope,receive,send):
        if scope.get('type')=='http' and scope.get('path')=='/health':
            data=json.dumps({'status':'ok' if TOKEN else 'needs_configuration','token_configured':bool(TOKEN),'allowlist_count':len(ALLOW)}).encode()
            await send({'type':'http.response.start','status':200,'headers':[(b'content-type',b'application/json')]}); await send({'type':'http.response.body','body':data}); return
        if scope.get('type')=='http':
            hdr={k.lower():v for k,v in scope.get('headers',[])}; auth=hdr.get(b'authorization',b'').decode('latin1'); got=auth[7:] if auth.lower().startswith('bearer ') else ''
            if not TOKEN or not hmac.compare_digest(got,TOKEN):
                body=b'{"error":"unauthorized"}'; await send({'type':'http.response.start','status':401,'headers':[(b'content-type',b'application/json'),(b'content-length',str(len(body)).encode())]}); await send({'type':'http.response.body','body':body}); return
        await inner(scope,receive,send)
    return app
app=authorized(mcp.streamable_http_app())

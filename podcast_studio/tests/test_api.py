import os, sys, tempfile, importlib, io
from pathlib import Path
from fastapi.testclient import TestClient

def setup_module():
    global api, client
    os.environ['PODCAST_STUDIO_DATA']=tempfile.mkdtemp(prefix='dandy-test-')
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
    import app as api
    client=TestClient(api.app)

def test_health():
    r=client.get('/health');assert r.status_code==200;assert 'ffmpeg' in r.json();assert 'tesseract' in r.json()

def test_image_ocr_reaches_source_context(monkeypatch):
    from PIL import Image, ImageDraw
    image=Image.new('RGB',(500,120),'white');ImageDraw.Draw(image).text((20,35),'DANDY OCR TEST',fill='black')
    data=io.BytesIO();image.save(data,format='PNG')
    monkeypatch.setattr(api,'extract_image_text',lambda img:'DANDY OCR TEST')
    r=client.post('/sources/upload',files={'file':('test.png',data.getvalue(),'image/png')})
    assert r.status_code==200 and r.json()['ocr_characters']>0
    source=api.load('sources',r.json()['id'])
    assert 'LOCAL OCR' in source['text']

def test_image_is_saved_when_tesseract_missing(monkeypatch):
    from PIL import Image
    image=Image.new('RGB',(32,32),'white');data=io.BytesIO();image.save(data,format='PNG')
    monkeypatch.setattr(api,'extract_image_text',lambda img: (_ for _ in ()).throw(RuntimeError('Tesseract missing')))
    r=client.post('/sources/upload',files={'file':('photo.png',data.getvalue(),'image/png')})
    assert r.status_code==200 and r.json()['status']=='saved_without_text'
    source=api.load('sources',r.json()['id'])
    assert source['image'] is True and (Path(api.ROOT)/'sources'/r.json()['id']/'photo.png').exists()

def test_episode_context_limits():
    images=[api.record('sources',{'label':f'i{i}.png','text':'','kind':'image','image':True}) for i in range(11)]
    response=client.post('/episodes',json={'title':'Too many images','source_ids':images})
    assert response.status_code==413
    documents=[api.record('sources',{'label':f'd{i}.pdf','text':'document','kind':'document'}) for i in range(51)]
    response=client.post('/episodes',json={'title':'Too many documents','source_ids':documents})
    assert response.status_code==413

def test_sources_and_listing():
    r=client.post('/sources',json={'text':'Source grounded research material. '*10});assert r.status_code==200
    assert r.json()['id'] in [s['id'] for s in client.get('/sources').json()]

def test_ad_edit_and_slots():
    r=client.post('/ads',json={'name':'Example','copy':'This is the original advertisement','voice':'announcer'});assert r.status_code==200
    aid=r.json()['id']
    r=client.put('/ads/'+aid,json={'copy':'Revised commercial'});assert r.status_code==200
    assert any(a['copy']=='Revised commercial' for a in client.get('/ads').json())
    assert client.get('/ads/'+aid+'/download').status_code==404

def test_invalid_ids():
    assert client.get('/episodes/not-a-uuid').status_code==400
    assert client.get('/episodes/00000000-0000-0000-0000-000000000000').status_code==404

def test_missing_credentials():
    os.environ.pop('NVIDIA_API_KEY',None)
    r=client.post('/episodes',json={'title':'Test','source_ids':[]})
    assert r.status_code==503

def test_llm_rejects_empty_message_content(monkeypatch):
    monkeypatch.setenv('NVIDIA_API_KEY','test-key')
    class Response:
        status_code=200
        def json(self): return {'choices':[{'finish_reason':'length','message':{'content':None}}]}
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,*args,**kwargs): return Response()
    monkeypatch.setattr(api.httpx,'AsyncClient',lambda *args,**kwargs: Client())
    import asyncio
    try: asyncio.run(api.llm([{'role':'user','content':'test'}],8,model='test-model'))
    except api.HTTPException as exc: assert exc.status_code==502 and 'no message content' in exc.detail
    else: assert False,'Expected empty model content to be rejected'

def test_voice_library_manifest_and_assignment():
    manifest=client.get('/voices').json()
    assert 'voices' in manifest and 'hosts' in manifest
    # Invalid IDs are rejected and cannot silently assign a non-existent voice.
    response=client.put('/voices/assign',json={'host':'Phil','voice_id':'not-present'})
    assert response.status_code==404
    assert client.get('/voices/not-present/handoff').status_code==404

def test_service_status_safe():
    status=client.get('/service-status').json()
    assert 'nemotron_key_present' in status
    assert 'voicestudio' in status
    assert 'orbot' in status
    assert 'NVIDIA_API_KEY' not in str(status)

def test_production_rejects_missing_episode():
    missing='00000000-0000-0000-0000-000000000000'
    assert client.post(f'/episodes/{missing}/produce',json={'with_social':True}).status_code==404

def test_music_library_and_episode_metadata():
    library=client.get('/music')
    assert library.status_code==200
    assets={item['id']:item for item in library.json()}
    assert assets['signature_opener']['target_seconds']==12
    assert client.get('/music/show_opener_12s.mp3').status_code==200
    source=client.post('/sources',json={'text':'Music integration fixture source content. '*10}).json()['id']
    episode_id=api.record('episodes',{'title':'Music fixture','segments':[],'slots':{},'status':'script_ready'})
    result=client.put('/episodes/'+episode_id+'/music',json={'enabled':True,'opener_id':'signature_opener','opener_mode':'under_intro','closing_id':'exit_sting'})
    assert result.status_code==200 and result.json()['music']['opener_mode']=='under_intro'
    assert api.load('episodes',episode_id)['music']['closing_id']=='exit_sting'
    assert source

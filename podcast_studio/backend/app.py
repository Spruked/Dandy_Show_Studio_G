"""Podcast Studio API. Local-only, filesystem-persisted, no placeholder success responses."""
import os, json, uuid, shutil, subprocess, re, io, base64, asyncio, hashlib, wave, ipaddress, socket
from pathlib import Path
from urllib.parse import urlparse
import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware

ROOT=Path(os.environ.get('PODCAST_STUDIO_DATA',str(Path.home()/'PodcastStudio')))
DEFAULT_NEMOTRON_MODEL='nvidia/nemotron-3-nano-omni-30b-a3b-reasoning'
ROOT.mkdir(parents=True,exist_ok=True)
app=FastAPI(title='Podcast Studio',version='0.3')
app.add_middleware(CORSMiddleware,allow_origins=['http://127.0.0.1:5173','http://localhost:5173','tauri://localhost','http://tauri.localhost'],allow_methods=['*'],allow_headers=['*'])

class Source(BaseModel):
    url:str|None=None
    text:str|None=None
class Generate(BaseModel):
    title:str
    source_ids:list[str]
    minutes:int=Field(default=15,ge=2,le=60)
    hosts:list[str]=Field(default_factory=lambda:['Phil','Jim'])
    editorial_direction:str='Investigative, conversational, compelling; distinct host perspectives; source-grounded.'
class Ad(BaseModel):
    name:str
    copy:str
    voice:str=''
class AdEdit(BaseModel):
    name:str|None=None
    copy:str|None=None
    voice:str|None=None
class Slot(BaseModel):
    opening:str|None=None
    middle1:str|None=None
    middle2:str|None=None
    closing:str|None=None
class VoiceConfig(BaseModel):
    base_url:str
    endpoint:str='/synthesize'
    host_voices:dict[str,str]
    payload_template:dict=Field(default_factory=lambda:{'text':'{text}','voice':'{voice}'})


def record(kind, data):
    rid=str(uuid.uuid4()); folder=ROOT/kind/rid;folder.mkdir(parents=True)
    (folder/'record.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
    return rid

def load(kind,rid):
    try:uuid.UUID(rid)
    except ValueError:raise HTTPException(400,'Invalid record ID')
    p=ROOT/kind/rid/'record.json'
    if not p.exists():raise HTTPException(404,f'{kind} record not found')
    return json.loads(p.read_text(encoding='utf-8'))

def save(kind,rid,data):
    (ROOT/kind/rid/'record.json').write_text(json.dumps(data,indent=2),encoding='utf-8')

def ffmpeg(*args):
    if not shutil.which('ffmpeg'):raise HTTPException(503,'FFmpeg is required on PATH')
    p=subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',*map(str,args)],capture_output=True,text=True)
    if p.returncode:raise HTTPException(502,p.stderr[-1500:])

def ocr_status():
    try:
        import pytesseract
        executable=shutil.which('tesseract') or pytesseract.pytesseract.tesseract_cmd
        return {'available':bool(executable and Path(executable).exists()),'executable':executable or None}
    except ImportError:
        return {'available':False,'executable':None}

def extract_image_text(img):
    try:
        import pytesseract
    except ImportError as exc:
        raise RuntimeError('Local OCR is unavailable: install pytesseract and the Tesseract executable.') from exc
    status=ocr_status()
    if not status['available']:
        raise RuntimeError('Tesseract OCR is not installed or not on PATH. Install Tesseract and restart the backend.')
    from PIL import ImageOps, ImageFilter
    prepared=ImageOps.exif_transpose(img).convert('RGB')
    text=pytesseract.image_to_string(prepared,config='--psm 6').strip()
    if len(text)<12:
        gray=ImageOps.autocontrast(ImageOps.grayscale(prepared)).filter(ImageFilter.SHARPEN)
        fallback=pytesseract.image_to_string(gray,config='--psm 6').strip()
        if len(fallback)>len(text):text=fallback
    return text

def probe_duration(path):
    if not shutil.which('ffprobe'):raise HTTPException(503,'ffprobe required')
    p=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(path)],capture_output=True,text=True)
    if p.returncode:raise HTTPException(502,p.stderr)
    return float(p.stdout.strip())

@app.get('/health')
def health():
    return {'ok':True,'ffmpeg':bool(shutil.which('ffmpeg')),'tesseract':ocr_status()['available'],'nemotron_configured':bool(os.getenv('NVIDIA_API_KEY')),'voicestudio_configured':bool(os.getenv('VOICESTUDIO_URL'))}

@app.post('/sources')
async def add_source(source:Source):
    if source.url:
        parsed=urlparse(source.url)
        if parsed.scheme!='https' or not parsed.hostname:raise HTTPException(400,'HTTPS URL required')
        # Only public web URLs; reject private IPs and local targets, including DNS-resolved addresses.
        import socket, ipaddress
        try:
            ips={x[4][0] for x in socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM)}
            if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):raise ValueError('Non-public address')
        except (OSError,ValueError):raise HTTPException(400,'URL must resolve to a public address')
        async with httpx.AsyncClient(timeout=25,follow_redirects=False) as client:
            r=await client.get(source.url,headers={'User-Agent':'PodcastStudio/0.1'})
            r.raise_for_status()
            if r.status_code in (301,302,303,307,308):raise HTTPException(400,'Redirects must be submitted as their final HTTPS URL')
            if len(r.content)>5_000_000:raise HTTPException(413,'Source too large')
            if 'html' not in r.headers.get('content-type',''):raise HTTPException(415,'Expected HTML')
            soup=BeautifulSoup(r.text,'html.parser')
            for el in soup(['script','style','nav','footer']):el.decompose()
            text=soup.get_text(' ',strip=True)[:100_000]
        label=source.url
    else:
        text=(source.text or '').strip();label='Pasted text'
    if len(text)<40:raise HTTPException(422,'Not enough source content')
    rid=record('sources',{'label':label,'text':text})
    return {'id':rid,'label':label,'characters':len(text)}

@app.post('/sources/upload')
async def upload_source(file:UploadFile=File(...)):
    name=Path(file.filename or 'upload').name; ext=Path(name).suffix.lower()
    raw=await file.read(15_000_001)
    if len(raw)>15_000_000:raise HTTPException(413,'File too large')
    try:
        if ext in ('.txt','.md','.csv','.json','.html'):
            text=BeautifulSoup(raw.decode('utf-8',errors='replace'),'html.parser').get_text(' ',strip=True) if ext=='.html' else raw.decode('utf-8',errors='replace')
        elif ext=='.pdf':
            from pypdf import PdfReader
            text='\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(raw)).pages)
        elif ext=='.docx':
            from docx import Document
            text='\n'.join(p.text for p in Document(io.BytesIO(raw)).paragraphs)
        elif ext=='.pptx':
            from pptx import Presentation
            text='\n'.join(shape.text for slide in Presentation(io.BytesIO(raw)).slides for shape in slide.shapes if shape.has_text_frame)
        elif ext in ('.png','.jpg','.jpeg','.tiff','.tif','.bmp'):
            from PIL import Image
            img=Image.open(io.BytesIO(raw));img.load()
            ocr_warning=None
            try:ocr_text=extract_image_text(img)
            except RuntimeError as exc:ocr_text='';ocr_warning=str(exc)
            vision_text=''
            if os.getenv('NVIDIA_API_KEY') and os.getenv('NEMOTRON_VISION_MODEL'):
                mime='image/png' if ext=='.png' else 'image/bmp' if ext=='.bmp' else 'image/tiff' if ext in ('.tiff','.tif') else 'image/jpeg'
                vision_text=await llm([{'role':'system','content':'Describe this image accurately for source-grounded podcast research. Focus on visual information beyond transcribed text. Distinguish observations from interpretation, do not invent.'},{'role':'user','content':[{'type':'text','text':'Describe the visual content of this image for a podcast production research library. Avoid repeating visible text unless needed for meaning.'},{'type':'image_url','image_url':{'url':f'data:{mime};base64,{base64.b64encode(raw).decode()}'}}]}],1200,model=os.getenv('NEMOTRON_VISION_MODEL'))
            if not ocr_text and not vision_text:raise HTTPException(503,ocr_warning or 'Image contains no readable text and vision analysis is not configured')
            pieces=[]
            if ocr_text:pieces.append('LOCAL OCR (unverified transcription):\n'+ocr_text)
            if vision_text:pieces.append('VISION NOTES (unverified visual description):\n'+vision_text)
            combined='\n\n'.join(pieces)
            rid=record('sources',{'label':name,'text':combined,'image':True,'status':'analyzed','ocr':ocr_text,'vision':vision_text,'ocr_warning':ocr_warning})
            (ROOT/'sources'/rid/name).write_bytes(raw)
            result={'id':rid,'label':name,'characters':len(combined),'status':'analyzed','ocr_characters':len(ocr_text),'vision_available':bool(vision_text)}
            if ocr_warning:result['ocr_warning']=ocr_warning
            return result
        else:raise HTTPException(415,'Unsupported file type')
    except HTTPException:raise
    except Exception as exc:raise HTTPException(422,f'Could not parse file: {exc}')
    rid=record('sources',{'label':name,'text':text[:150_000]})
    return {'id':rid,'label':name,'characters':len(text)}

async def llm(messages,max_tokens=5000,model=None):
    key=(os.getenv('NVIDIA_API_KEY') or '').strip();model=(model or os.getenv('NEMOTRON_MODEL') or DEFAULT_NEMOTRON_MODEL).strip()
    if not key or not model:raise HTTPException(503,'Set NVIDIA_API_KEY and NEMOTRON_MODEL')
    async with httpx.AsyncClient(timeout=180) as client:
        r=await client.post(os.getenv('NVIDIA_BASE_URL','https://integrate.api.nvidia.com/v1').rstrip('/')+'/chat/completions',headers={'Authorization':f'Bearer {key}'},json={'model':model,'messages':messages,'max_tokens':max_tokens,'temperature':0.6})
        if r.status_code>=400:raise HTTPException(502,f'Nemotron error {r.status_code}: {r.text[:500]}')
        return r.json()['choices'][0]['message']['content']

@app.post('/episodes')
async def create_episode(req:Generate):
    sources=[load('sources',x) for x in req.source_ids]
    if any(not x.get('text') for x in sources):raise HTTPException(422,'Image sources require a vision adapter before script generation')
    context='\n\n'.join(f'SOURCE {i+1}: {x["label"]}\n{x["text"][:20000]}' for i,x in enumerate(sources))[:65000]
    outline=await llm([{'role':'system','content':'You are an expert investigative podcast producer. Plan a compelling multi-chapter episode grounded only in provided sources. Include tension, critical questions, clear narrative arc, and fact-check cautions. Return JSON object with chapters array of {title,goal,beats}, no markdown.'},{'role':'user','content':f'Title: {req.title}; duration {req.minutes} minutes; hosts {req.hosts}; direction {req.editorial_direction}\n{context}'}],3500)
    script=await llm([{'role':'system','content':f'Write a finished two-host podcast dialogue, with distinct recurring personalities and natural objections, interruptions, callbacks and transitions. Hosts: {req.hosts}. Target {req.minutes*125} spoken words. Use only supplied facts, qualify uncertain claims. Return JSON object with segments array, each {{"speaker":host_name,"text":spoken_dialogue,"chapter":chapter_title}}. No markdown or production directions inside spoken text.'},{'role':'user','content':f'Outline:\n{outline}\nSources:\n{context}\nDirection:\n{req.editorial_direction}'}],max(5000,min(14000,req.minutes*550)))
    try:
        data=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$','',script.strip()))
        if not isinstance(data.get('segments'),list) or not data['segments']:raise ValueError('No segments')
        for s in data['segments']:
            if s.get('speaker') not in req.hosts or not s.get('text'):raise ValueError('Invalid speaker or dialogue')
    except Exception as exc:raise HTTPException(502,f'Nemotron returned invalid episode JSON: {exc}; raw excerpt {script[:300]}')
    spoken_words=sum(len(seg['text'].split()) for seg in data['segments'])
    if spoken_words<req.minutes*65:
        raise HTTPException(502,f'Generated script too short: {spoken_words} words for {req.minutes} minutes. Try a shorter episode or larger model output limit.')
    rid=record('episodes',{'title':req.title,'outline':outline,'segments':data['segments'],'slots':Slot().model_dump(),'status':'script_ready','source_ids':req.source_ids,'word_count':spoken_words,'target_minutes':req.minutes})
    return {'id':rid,'segments':len(data['segments']),'status':'script_ready'}

@app.get('/episodes/{rid}')
def episode(rid:str):return load('episodes',rid)

@app.post('/ads')
def create_ad(ad:Ad):
    rid=record('ads',ad.model_dump()|{'status':'script_ready'})
    return {'id':rid}

@app.post('/ads/generate')
async def generate_ad(brief:dict):
    copy=await llm([{'role':'system','content':'Write one persuasive, factual, 20-35 second audio commercial from the supplied brief. Output only spoken copy. Do not invent product facts.'},{'role':'user','content':json.dumps(brief)}],600)
    rid=record('ads',{'name':brief.get('name','New commercial'),'copy':copy,'voice':brief.get('voice',''),'status':'script_ready'})
    return {'id':rid,'copy':copy}

@app.put('/ads/{rid}')
def edit_ad(rid:str,change:AdEdit):
    ad=load('ads',rid)
    for k,v in change.model_dump(exclude_none=True).items():ad[k]=v
    ad['status']='script_ready'
    wav=ROOT/'ads'/rid/'voice.wav'
    if wav.exists():wav.unlink()
    save('ads',rid,ad)
    return {'id':rid,**ad}

@app.get('/ads/{rid}/download')
def download_ad(rid:str):
    load('ads',rid)
    wav=ROOT/'ads'/rid/'voice.wav'
    if not wav.exists():raise HTTPException(404,'Render ad first')
    return FileResponse(wav,media_type='audio/wav',filename=f'ad-{rid}.wav')

@app.get('/episodes')
def list_episodes():
    return [{'id':p.name,**json.loads((p/'record.json').read_text())} for p in (ROOT/'episodes').glob('*') if (p/'record.json').exists()]

@app.get('/sources')
def list_sources():
    return [{'id':p.name,'label':json.loads((p/'record.json').read_text()).get('label')} for p in (ROOT/'sources').glob('*') if (p/'record.json').exists()]

@app.get('/ads')
def list_ads():return [{'id':p.name,**json.loads((p/'record.json').read_text())} for p in (ROOT/'ads').glob('*') if (p/'record.json').exists()]

@app.put('/episodes/{rid}/slots')
def update_slots(rid:str,slots:Slot):
    ep=load('episodes',rid)
    for aid in slots.model_dump().values():
        if aid:load('ads',aid)
    ep['slots']=slots.model_dump();save('episodes',rid,ep)
    return {'ok':True,'slots':ep['slots']}

async def synth(text,voice,dest):
    """VoiceStudio HTTP adapter. Supports direct audio bytes, JSON base64 and audio URL.
    Configure the request shape using VOICESTUDIO_PAYLOAD_JSON, e.g.
    {"text":"{text}","speaker":"{voice}","format":"wav"}.
    """
    base=os.getenv('VOICESTUDIO_URL','http://127.0.0.1:3900')
    endpoint=os.getenv('VOICESTUDIO_ENDPOINT','/v1/audio/speech')
    try: template=json.loads(os.getenv('VOICESTUDIO_PAYLOAD_JSON','{"input":"{text}","voice":"{voice}","model":"tts-1","response_format":"wav"}'))
    except ValueError:raise HTTPException(500,'Invalid VOICESTUDIO_PAYLOAD_JSON')
    def replace(obj):
        if isinstance(obj,str):return obj.replace('{text}',text).replace('{voice}',voice)
        if isinstance(obj,dict):return {k:replace(v) for k,v in obj.items()}
        if isinstance(obj,list):return [replace(v) for v in obj]
        return obj
    request_error=None
    for attempt in range(3):
      try:
        async with httpx.AsyncClient(timeout=240) as client:
          r=await client.post(base.rstrip('/')+'/'+endpoint.lstrip('/'),json=replace(template))
          if r.status_code>=500 and attempt<2:
              await asyncio.sleep(0.75*(attempt+1));continue
          if r.status_code>=400:raise HTTPException(502,f'VoiceStudio error: {r.status_code}: {r.text[:350]}')
          if r.headers.get('content-type','').startswith('audio/'):
              dest.write_bytes(r.content)
          else:
              try: result=r.json()
              except ValueError:raise HTTPException(502,'VoiceStudio returned neither audio nor JSON')
              encoded=result.get('audio_base64') or result.get('audio')
              if isinstance(encoded,str) and len(encoded)>100:
                  dest.write_bytes(base64.b64decode(encoded.split(',')[-1]))
              elif result.get('audio_url'):
                  audio_url=result['audio_url']
                  if not audio_url.startswith(base.rstrip('/')+'/'):
                      raise HTTPException(502,'VoiceStudio audio URL must be on configured server')
                  ar=await client.get(audio_url);ar.raise_for_status();dest.write_bytes(ar.content)
              else:raise HTTPException(502,'VoiceStudio response lacks audio bytes/audio_base64/audio_url')
          request_error=None
          break
      except HTTPException:raise
      except (httpx.HTTPError,TimeoutError) as exc:
          request_error=exc
          if attempt<2:await asyncio.sleep(0.75*(attempt+1))
    if request_error:raise HTTPException(502,f'VoiceStudio connection failed after retries: {request_error}')
    if not dest.exists() or dest.stat().st_size<100:raise HTTPException(502,'VoiceStudio returned empty audio')

@app.post('/ads/{rid}/render')
async def render_ad(rid:str):
    ad=load('ads',rid);p=ROOT/'ads'/rid/'voice.wav'
    await synth(ad['copy'],ad.get('voice') or os.getenv('DEFAULT_AD_VOICE','default'),p)
    ad['status']='audio_ready';save('ads',rid,ad)
    return {'id':rid,'duration':probe_duration(p)}

@app.post('/episodes/{rid}/render')
async def render_episode(rid:str):
    ep=load('episodes',rid); folder=ROOT/'episodes'/rid;clips=[]
    voices=assigned_voices()
    for i,seg in enumerate(ep['segments']):
        raw=folder/f'{i:04d}.wav';norm=folder/f'{i:04d}.mp3'
        if not raw.exists():await synth(seg['text'],voices.get(seg['speaker'],seg['speaker']),raw)
        ffmpeg('-i',raw,'-ar','44100','-ac','2','-codec:a','libmp3lame','-b:a','192k',norm)
        clips.append(norm)
    (folder/'dialogue.json').write_text(json.dumps([str(x) for x in clips]))
    ep['status']='dialogue_audio_ready';save('episodes',rid,ep)
    return {'status':ep['status'],'clips':len(clips)}

@app.post('/episodes/{rid}/mix')
def mix_episode(rid:str):
    ep=load('episodes',rid);folder=ROOT/'episodes'/rid
    clips=[folder/f'{i:04d}.mp3' for i in range(len(ep['segments']))]
    if any(not p.exists() for p in clips):raise HTTPException(409,'Render dialogue first')
    slots=ep['slots'];n=len(clips);boundaries=[0,n//3,2*n//3,n]
    plan=[]
    for i,p in enumerate(clips):
        for slot,position in [('opening',0),('middle1',boundaries[1]),('middle2',boundaries[2])]:
            if i==position and slots.get(slot):
                aid=slots[slot];ad=ROOT/'ads'/aid/'voice.wav'
                if not ad.exists():raise HTTPException(409,f'Render ad {aid} before mixing')
                normalized=folder/f'ad_{slot}.mp3';ffmpeg('-i',ad,'-ar','44100','-ac','2','-codec:a','libmp3lame','-b:a','192k',normalized)
                plan.append(normalized)
        plan.append(p)
    if slots.get('closing'):
        aid=slots['closing'];ad=ROOT/'ads'/aid/'voice.wav'
        if not ad.exists():raise HTTPException(409,f'Render ad {aid} before mixing')
        normalized=folder/'ad_closing.mp3';ffmpeg('-i',ad,'-ar','44100','-ac','2','-codec:a','libmp3lame','-b:a','192k',normalized)
        plan.append(normalized)
    listing=folder/'concat.txt'
    listing.write_text(''.join("file '"+str(p).replace("'","'\\''")+"'\n" for p in plan))
    final=folder/'episode.mp3';ffmpeg('-f','concat','-safe','0','-i',listing,'-c:a','libmp3lame','-b:a','192k',final)
    ep['status']='mixed';save('episodes',rid,ep)
    return {'status':'mixed','duration':probe_duration(final),'download':f'/episodes/{rid}/download'}

@app.get('/episodes/{rid}/download')
def download(rid:str):
    p=ROOT/'episodes'/rid/'episode.mp3'
    if not p.exists():raise HTTPException(404,'Episode not mixed')
    return FileResponse(p,media_type='audio/mpeg',filename='episode.mp3')

@app.post('/episodes/{rid}/social')
def social(rid:str):
    ep=load('episodes',rid);folder=ROOT/'episodes'/rid;audio=folder/'episode.mp3'
    if not audio.exists():raise HTTPException(409,'Mix episode first')
    # Actual MP4 export with static branded title card; subtitles and motion graphics remain future work.
    from PIL import Image,ImageDraw,ImageFont
    img=Image.new('RGB',(1080,1920),(15,20,38));d=ImageDraw.Draw(img)
    title=ep['title'][:100]
    d.multiline_text((100,760),'PODCAST STUDIO\n\n'+ '\n'.join(title[i:i+23] for i in range(0,len(title),23)),fill='white',spacing=24)
    cover=folder/'cover.png';img.save(cover)
    video=folder/'social_vertical.mp4'
    ffmpeg('-loop','1','-i',cover,'-i',audio,'-c:v','libx264','-tune','stillimage','-c:a','aac','-b:a','192k','-pix_fmt','yuv420p','-shortest','-movflags','+faststart',video)
    return {'video':f'/episodes/{rid}/social/download','format':'1080x1920 MP4 static cover + full episode audio'}

@app.get('/episodes/{rid}/social/download')
def social_download(rid:str):
    p=ROOT/'episodes'/rid/'social_vertical.mp4'
    if not p.exists():raise HTTPException(404,'Social video not rendered')
    return FileResponse(p,media_type='video/mp4',filename='podcast_vertical.mp4')

class SocialClip(BaseModel):
    start_seconds:float=Field(ge=0)
    duration_seconds:float=Field(default=45,ge=5,le=180)
    headline:str=''
    aspect:str='9:16'

@app.post('/episodes/{rid}/clips')
def create_clip(rid:str,req:SocialClip):
    ep=load('episodes',rid);folder=ROOT/'episodes'/rid
    audio=folder/'episode.mp3'
    if not audio.exists():raise HTTPException(409,'Mix episode first')
    duration=probe_duration(audio)
    if req.start_seconds>=duration:raise HTTPException(422,'Clip begins after episode ends')
    from PIL import Image,ImageDraw,ImageFont
    if req.aspect not in ('9:16','1:1','16:9'):raise HTTPException(422,'Unsupported aspect')
    w,h={'9:16':(1080,1920),'1:1':(1080,1080),'16:9':(1920,1080)}[req.aspect]
    image=Image.new('RGB',(w,h),(15,21,40));d=ImageDraw.Draw(image)
    headline=(req.headline or ep['title']).strip()
    font_path='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
    font=ImageFont.truetype(font_path,max(32,w//25)) if Path(font_path).exists() else ImageFont.load_default()
    words=headline.split();lines=[];line=''
    for word in words:
        proposed=(line+' '+word).strip()
        if d.textbbox((0,0),proposed,font=font)[2]>w-130 and line:lines.append(line);line=word
        else:line=proposed
    if line:lines.append(line)
    y=h//2-len(lines)*45
    for line in lines:d.text((65,y),line,fill=(246,248,255),font=font);y+=70
    d.text((65,h-120),'THE DANDY SHOW',fill=(236,93,84),font=font)
    clip_id=str(uuid.uuid4());poster=folder/f'clip-{clip_id}.png';poster and image.save(poster)
    video=folder/f'clip-{clip_id}.mp4'
    ffmpeg('-loop','1','-framerate','1','-i',poster,'-ss',str(req.start_seconds),'-t',str(min(req.duration_seconds,duration-req.start_seconds)),'-i',audio,'-c:v','libx264','-preset','veryfast','-tune','stillimage','-r','24','-c:a','aac','-b:a','160k','-pix_fmt','yuv420p','-shortest','-movflags','+faststart',video)
    clips=ep.setdefault('social_clips',[]);clips.append({'id':clip_id,'start':req.start_seconds,'duration':req.duration_seconds,'headline':headline,'aspect':req.aspect});save('episodes',rid,ep)
    return {'id':clip_id,'download':f'/episodes/{rid}/clips/{clip_id}/download'}

@app.get('/episodes/{rid}/clips/{clip_id}/download')
def download_clip(rid:str,clip_id:str):
    ep=load('episodes',rid)
    if clip_id not in [x['id'] for x in ep.get('social_clips',[])]:raise HTTPException(404,'Clip not found')
    p=ROOT/'episodes'/rid/f'clip-{clip_id}.mp4'
    if not p.exists():raise HTTPException(404,'Clip not rendered')
    return FileResponse(p,media_type='video/mp4',filename='dandy-short.mp4')

@app.get('/episodes/{rid}/social-copy')
async def social_copy(rid:str):
    ep=load('episodes',rid)
    dialogue=' '.join(s['text'] for s in ep['segments'])[:12000]
    content=await llm([{'role':'system','content':'Produce truthful platform-specific promotion copy based ONLY on this podcast script. Return valid JSON with keys youtube_title, youtube_description, instagram_caption, tiktok_caption, linkedin_post, hashtags (array). No unsupported promises.'},{'role':'user','content':f'Title: {ep["title"]}\n{dialogue}'}],1800)
    try:data=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$','',content.strip()))
    except ValueError:raise HTTPException(502,'Model did not return valid social JSON')
    folder=ROOT/'episodes'/rid;(folder/'social-copy.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
    return data

@app.get('/episodes/{rid}/script/download')
def script_download(rid:str):
    ep=load('episodes',rid)
    script='\n\n'.join(f"{s['speaker']}: {s['text']}" for s in ep['segments'])
    return Response(script,media_type='text/plain',headers={'Content-Disposition':'attachment; filename="episode-script.txt"'})


# ---- Reusable voice references, production status, and exports ----
from fastapi import BackgroundTasks
from fastapi.responses import StreamingResponse
from datetime import datetime, timezone

VOICE_DIR=ROOT/'voice_library';VOICE_DIR.mkdir(exist_ok=True)
JOBS={}

def voice_manifest():
    p=VOICE_DIR/'voices.json'
    if not p.exists():
        p.write_text(json.dumps({'hosts':{'Phil':None,'Jim':None},'voices':[]},indent=2))
    return json.loads(p.read_text(encoding='utf-8'))

def assigned_voices():
    manifest=voice_manifest()
    result=json.loads(os.getenv('HOST_VOICES','{"Phil":"Phil","Jim":"Jim"}'))
    for host,vid in manifest.get('hosts',{}).items():
        if vid:
            voice=next((v for v in manifest['voices'] if v['id']==vid),None)
            if voice:result[host]=voice.get('studio_voice_id') or voice['name']
    return result

def write_manifest(data):
    (VOICE_DIR/'voices.json').write_text(json.dumps(data,indent=2),encoding='utf-8')

@app.get('/voices')
def get_voices():return voice_manifest()

@app.post('/voices/upload')
async def import_voice(file:UploadFile=File(...),name:str=''):
    ext=Path(file.filename or '').suffix.lower()
    if ext not in ('.wav','.mp3','.m4a','.ogg','.flac'):raise HTTPException(415,'Use WAV, MP3, M4A, OGG or FLAC')
    data=await file.read(20_000_001)
    if not data or len(data)>20_000_000:raise HTTPException(413,'Sample must be between 1 byte and 20 MB')
    vid=str(uuid.uuid4());folder=VOICE_DIR/'samples';folder.mkdir(exist_ok=True)
    src=folder/(vid+ext);src.write_bytes(data)
    # Normalize samples to a predictable format for local playback and VoiceStudio handoff.
    normalized=folder/(vid+'.wav')
    try:ffmpeg('-i',src,'-ac','1','-ar','24000',normalized)
    except HTTPException:
        src.unlink(missing_ok=True);normalized.unlink(missing_ok=True);raise
    if normalized.stat().st_size<200:raise HTTPException(422,'Invalid voice sample')
    if src!=normalized:src.unlink(missing_ok=True)
    voice_name=name.strip() or Path(file.filename or 'Voice').stem
    studio_voice_id=''
    if os.getenv('VOICESTUDIO_REGISTER_PROFILES','true').lower() not in ('0','false','no'):
        base=os.getenv('VOICESTUDIO_URL','http://127.0.0.1:3900').rstrip('/')
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                with normalized.open('rb') as audio:
                    response=await client.post(base+'/profiles',files={'ref_audio':(normalized.name,audio,'audio/wav')},data={'name':voice_name,'kind':'clone','language':'Auto'})
                if response.status_code>=400:raise HTTPException(502,f'VoiceStudio profile registration failed: {response.status_code}: {response.text[:350]}')
                profile=response.json();studio_voice_id=str(profile.get('id') or profile.get('profile_id') or '')
                if not studio_voice_id:raise HTTPException(502,'VoiceStudio profile registration returned no profile ID')
        except HTTPException:raise
        except httpx.HTTPError as exc:raise HTTPException(502,f'VoiceStudio profile registration failed: {exc}')
    manifest=voice_manifest();manifest['voices'].append({'id':vid,'name':voice_name,'sample':f'samples/{vid}.wav','studio_voice_id':studio_voice_id,'created':datetime.now(timezone.utc).isoformat()});write_manifest(manifest)
    return {'id':vid,'name':manifest['voices'][-1]['name']}

@app.get('/voices/{vid}/sample')
def sample_voice(vid:str):
    manifest=voice_manifest()
    voice=next((v for v in manifest['voices'] if v['id']==vid),None)
    if not voice:raise HTTPException(404,'Voice not found')
    sample=voice.get('sample')
    if not sample:raise HTTPException(404,'No reference sample for this VoiceStudio voice')
    return FileResponse(VOICE_DIR/sample,media_type='audio/wav')

class VoiceAssignment(BaseModel):
    host:str
    voice_id:str

@app.put('/voices/assign')
def assign_voice(req:VoiceAssignment):
    manifest=voice_manifest()
    if req.host not in ('Phil','Jim'):raise HTTPException(422,'Host must be Phil or Jim')
    if not any(v['id']==req.voice_id for v in manifest['voices']):raise HTTPException(404,'Voice not found')
    manifest['hosts'][req.host]=req.voice_id;write_manifest(manifest)
    return manifest

class VoiceStudioID(BaseModel):
    studio_voice_id:str

@app.put('/voices/{vid}/studio-id')
def voice_studio_id(vid:str,req:VoiceStudioID):
    manifest=voice_manifest();voice=next((v for v in manifest['voices'] if v['id']==vid),None)
    if not voice:raise HTTPException(404,'Voice not found')
    voice['studio_voice_id']=req.studio_voice_id.strip();write_manifest(manifest)
    return voice

@app.get('/voices/{vid}/handoff')
def voice_handoff(vid:str):
    manifest=voice_manifest();voice=next((v for v in manifest['voices'] if v['id']==vid),None)
    if not voice:raise HTTPException(404,'Voice not found')
    # Manifest is a local contract; no claim that VoiceStudio supports importing references.
    sample_path=str((VOICE_DIR/voice['sample']).resolve()) if voice.get('sample') else None
    return {'name':voice['name'],'studio_voice_id':voice['studio_voice_id'],'sample_path':sample_path,'sample_url':f'/voices/{vid}/sample' if voice.get('sample') else None,'assigned_hosts':[h for h,v in manifest['hosts'].items() if v==vid]}

class VoicePreview(BaseModel):
    text:str=Field(min_length=1,max_length=500)
    voice_id:str

@app.post('/voices/preview')
async def preview_voice(req:VoicePreview):
    manifest=voice_manifest();voice=next((v for v in manifest['voices'] if v['id']==req.voice_id),None)
    if not voice:raise HTTPException(404,'Voice not found')
    target=VOICE_DIR/'previews';target.mkdir(exist_ok=True)
    dest=target/(str(uuid.uuid4())+'.wav')
    await synth(req.text,voice.get('studio_voice_id') or voice['name'],dest)
    return FileResponse(dest,media_type='audio/wav',filename='voice-preview.wav')

@app.get('/episodes/{rid}/assets')
def episode_assets(rid:str):
    ep=load('episodes',rid);folder=ROOT/'episodes'/rid
    names={'mp3':'episode.mp3','video':'social_vertical.mp4','cover':'cover.png','script':'record.json','social_copy':'social-copy.json'}
    return {'id':rid,'title':ep['title'],'status':ep['status'],'assets':{key:{'available':(folder/name).exists(),'url':f'/episodes/{rid}/'+('download' if key=='mp3' else 'social/download' if key=='video' else 'script/download' if key=='script' else 'cover' if key=='cover' else 'social-copy/download')} for key,name in names.items()}}

@app.get('/episodes/{rid}/cover')
def download_cover(rid:str):
    load('episodes',rid);p=ROOT/'episodes'/rid/'cover.png'
    if not p.exists():raise HTTPException(404,'Create video first')
    return FileResponse(p,media_type='image/png')

@app.get('/episodes/{rid}/social-copy/download')
def download_social_copy(rid:str):
    load('episodes',rid);p=ROOT/'episodes'/rid/'social-copy.json'
    if not p.exists():raise HTTPException(404,'Generate social copy first')
    return FileResponse(p,media_type='application/json',filename='social-copy.json')

@app.get('/service-status')
async def service_status():
    results={}
    for key,url in [('voicestudio',os.getenv('VOICESTUDIO_URL','http://127.0.0.1:3900')),('orbot',os.getenv('ORBOT_URL','http://127.0.0.1:16521'))]:
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                r=await client.get(url.rstrip('/')+'/health')
                results[key]={'reachable':True,'http_status':r.status_code,'url':url}
        except httpx.HTTPError:results[key]={'reachable':False,'url':url}
    results['nemotron_key_present']=bool(os.getenv('NVIDIA_API_KEY'))
    results['nemotron_model_present']=bool(os.getenv('NEMOTRON_MODEL') or DEFAULT_NEMOTRON_MODEL)
    results['ffmpeg']=bool(shutil.which('ffmpeg'))
    return results

async def production_job(job_id,episode_id,with_social):
    try:
        JOBS[job_id].update(stage='Synthesizing hosts',progress=10)
        await render_episode(episode_id)
        JOBS[job_id].update(stage='Mixing audio and commercials',progress=65)
        mix_episode(episode_id)
        if with_social:
            JOBS[job_id].update(stage='Rendering social video',progress=80)
            await asyncio.to_thread(social,episode_id)
        JOBS[job_id].update(stage='Complete',progress=100,status='completed')
    except Exception as exc:
        JOBS[job_id].update(stage='Failed',status='failed',error=str(exc))

class ProductionRequest(BaseModel):
    with_social:bool=True

@app.post('/episodes/{rid}/produce')
async def produce_episode(rid:str,req:ProductionRequest,background_tasks:BackgroundTasks):
    load('episodes',rid)
    job_id=str(uuid.uuid4());JOBS[job_id]={'id':job_id,'episode_id':rid,'status':'running','stage':'Queued','progress':0}
    background_tasks.add_task(production_job,job_id,rid,req.with_social)
    return JOBS[job_id]

@app.get('/jobs/{job_id}')
def job_status(job_id:str):
    if job_id not in JOBS:raise HTTPException(404,'Job not found')
    return JOBS[job_id]

@app.get('/episodes/{rid}/captions')
def captions(rid:str):
    ep=load('episodes',rid);folder=ROOT/'episodes'/rid
    clips=[folder/f'{i:04d}.mp3' for i in range(len(ep['segments']))]
    if any(not c.exists() for c in clips):raise HTTPException(409,'Render dialogue first')
    def stamp(seconds):
        ms=round(seconds*1000);h,rem=divmod(ms,3600000);m,rem=divmod(rem,60000);s,ms=divmod(rem,1000)
        return f'{h:02}:{m:02}:{s:02},{ms:03}'
    cursor=0.0;blocks=[]
    for i,(clip,seg) in enumerate(zip(clips,ep['segments']),1):
        duration=probe_duration(clip)
        blocks.append(f'{i}\n{stamp(cursor)} --> {stamp(cursor+duration)}\n{seg["speaker"]}: {seg["text"]}\n')
        cursor+=duration
    return Response('\n'.join(blocks),media_type='text/plain',headers={'Content-Disposition':'attachment; filename="dialogue.srt"'})

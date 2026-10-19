import os, re, json, time, shutil, subprocess, urllib.request, urllib.parse, base64, math
from pathlib import Path
from datetime import datetime, timezone

def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode:
        raise RuntimeError(f"Command failed ({p.returncode}): {' '.join(cmd)}\n{p.stderr[-2500:]}")
    return p.stdout.strip()

def seconds_text(s):
    return f"{int(s//3600):02}:{int((s%3600)//60):02}:{s%60:06.3f}".replace(".", ",")

def write_srt(scenes, path):
    with open(path,"w",encoding="utf-8") as f:
        for i,s in enumerate(scenes,1):
            f.write(f"{i}\n{seconds_text(s['start'])} --> {seconds_text(s['end'])}\n{s['text'].strip()}\n\n")

def split_sentences(text):
    # Keep sentence boundaries while merging tiny fragments to avoid flashing shots.
    parts = [x.strip() for x in re.split(r'(?<=[.!?])\s+|\n+', text.strip()) if x.strip()]
    if not parts: return []
    out=[]
    for p in parts:
        if out and len(p.split()) < 4: out[-1] += " " + p
        else: out.append(p)
    return out

def download_url(url, dest):
    parsed=urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("audio_url must use HTTPS")
    req=urllib.request.Request(url, headers={"User-Agent":"IncuBrix-PS3-assessment/0.1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data=r.read(100*1024*1024+1)
    if len(data)>100*1024*1024: raise ValueError("Audio URL exceeds 100 MB")
    dest.write_bytes(data)

def make_narration(payload, work):
    if payload.get("_uploaded_audio_b64"):
        audio=work / ("input"+payload.get("_uploaded_ext",".mp3"))
        audio.write_bytes(base64.b64decode(payload["_uploaded_audio_b64"]))
        return audio, None, "uploaded_audio"
    if payload.get("audio_url"):
        audio=work/"input_audio"
        download_url(payload["audio_url"],audio)
        return audio, None, "audio_url"
    text=payload.get("script") or " ".join(s["text"] for s in payload.get("scenes",[]))
    if not text.strip(): raise ValueError("Script is empty")
    audio=work/"narration.wav"
    voice=payload.get("voice","en")
    # espeak-ng is included in the container; voice value is configurable but maps to installed voices.
    selected = voice if re.fullmatch(r"[a-zA-Z0-9_-]{1,30}", voice) else "en"
    run(["espeak-ng","-v",selected,"-s","155","-w",str(audio),text[:30000]])
    return audio, text, "espeak-ng"

def transcribe(audio, supplied_text=None):
    if supplied_text:
        return supplied_text, "provided_script"
    try:
        from faster_whisper import WhisperModel
        model=WhisperModel(os.getenv("WHISPER_MODEL","base"), device="cpu", compute_type="int8")
        segments,_=model.transcribe(str(audio), vad_filter=True)
        text=" ".join(s.text.strip() for s in segments).strip()
        if text: return text, "faster-whisper"
    except Exception:
        pass
    # Explicit fallback: audio duration is preserved as one scene; don't invent a transcript.
    return "Voiceover audio (transcription unavailable)", "transcription_unavailable"

def duration(path):
    return float(run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(path)]))

def pexels_search(query, avoid):
    key=os.getenv("PEXELS_API_KEY","")
    if not key: return None
    q=urllib.parse.quote(query[:180])
    req=urllib.request.Request(f"https://api.pexels.com/videos/search?query={q}&per_page=15&orientation=landscape",
                               headers={"Authorization":key,"User-Agent":"IncuBrix-PS3-assessment/0.1"})
    try:
        with urllib.request.urlopen(req,timeout=15) as r: data=json.loads(r.read())
        avoid_l=[x.lower() for x in avoid]
        videos=[]
        for v in data.get("videos",[]):
            url=next((f.get("link") for f in sorted(v.get("video_files",[]),key=lambda z:z.get("width",0),reverse=True) if f.get("link")),None)
            if not url: continue
            desc=(v.get("url","")+" "+str(v.get("user",{}).get("name",""))).lower()
            if any(a in desc for a in avoid_l): continue
            videos.append({"url":url,"source_url":v.get("url","https://www.pexels.com/license/"),
                "license":"Pexels License (verify provider terms for each asset)",
                "attribution":v.get("user",{}).get("name","Pexels contributor"),
                "provider":"Pexels","video_id":v.get("id"),"width":v.get("width"),"height":v.get("height")})
        return videos
    except Exception:
        return None

def fetch_clip(url,dest):
    req=urllib.request.Request(url,headers={"User-Agent":"IncuBrix-PS3-assessment/0.1"})
    with urllib.request.urlopen(req,timeout=25) as r: data=r.read(80*1024*1024+1)
    if len(data)>80*1024*1024: raise ValueError("Clip exceeds 80 MB")
    dest.write_bytes(data)

def make_fallback_clip(text, dest, seconds, width=1280, height=720):
    # Original generated motion graphic: safe deterministic fallback, not stock footage.
    # The narration phrase is shown as a title; FFmpeg generates moving gradient-free abstract shapes.
    clean=re.sub(r"[^A-Za-z0-9 ,.'!?-]","",text)[:100]
    clean=clean.replace(":"," ")
    draw=clean.replace("\\","\\\\").replace(":","\\:").replace("'","\\'")
    vf=f"drawbox=x='mod(t*90\\,w)':y=80:w=220:h=120:color=0x236B8E@0.7:t=fill,drawtext=text='{draw}':fontcolor=white:fontsize=38:x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=black@0.45:boxborderw=24,zoompan=z='min(zoom+0.001,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps=30,zoompan=z='min(zoom+0.001,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps=30,format=yuv420p"
    run(["ffmpeg","-y","-f","lavfi","-i",f"color=c=0x142B3B:s={width}x{height}:r=30:d={max(1,seconds)}",
         "-vf",vf,"-an","-c:v","libx264","-preset","ultrafast","-pix_fmt","yuv420p",str(dest)])
    return {"provider":"original_generated_motion_graphic","source_url":None,
            "license":"Original procedural output generated by this service; no third-party footage",
            "attribution":"Generated by IncuBrix PS3 assessment implementation","ai_generated":True}

def render_scene(clip, audio, out, start, end, ratio, burn_in=False, caption=""):
    width,height={"16:9":(1920,1080),"9:16":(1080,1920),"1:1":(1080,1080)}[ratio]
    # Scale/crop to fill; trim and reset timestamps. Scene video length is controlled by narration segment.
    filt=f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},fps=30,format=yuv420p"
    run(["ffmpeg","-y","-stream_loop","-1","-i",str(clip),"-ss","0","-t",str(max(.1,end-start)),"-vf",filt,
         "-an","-c:v","libx264","-preset","veryfast","-pix_fmt","yuv420p",str(out)])

def process_job(job_id,payload,inputs,outputs,progress_cb=lambda n:None):
    started=time.time()
    work=inputs/job_id; outdir=outputs/job_id
    work.mkdir(parents=True,exist_ok=True); outdir.mkdir(parents=True,exist_ok=True)
    audio,script,tts_provider=make_narration(payload,work)
    audio_duration=duration(audio)
    if audio_duration <= 0.05: raise ValueError("Audio is silent or empty")
    if audio_duration > 600: raise ValueError("Audio exceeds 10-minute limit")
    transcript,transcription_provider=transcribe(audio,script)
    if script is None and transcription_provider=="transcription_unavailable":
        scene_texts=[{"text":"Voiceover segment","visual_hint":"abstract narration visualization"}]
    elif payload.get("scenes"):
        scene_texts=[s if isinstance(s,dict) else s.model_dump() for s in payload["scenes"]]
    else:
        scene_texts=[{"text":s,"visual_hint":None} for s in split_sentences(transcript)]
    if not scene_texts: raise ValueError("No usable scenes; script may be empty")
    # Timing is proportional to word count, ending exactly at narration duration.
    weights=[max(1,len(s["text"].split())) for s in scene_texts]
    total=sum(weights); cursor=0.0; timeline=[]
    for i,(s,w) in enumerate(zip(scene_texts,weights)):
        end=audio_duration if i==len(weights)-1 else cursor+audio_duration*w/total
        timeline.append({"scene_index":i,"text":s["text"],"visual_hint":s.get("visual_hint"),
                         "start":round(cursor,3),"end":round(end,3),"duration":round(end-cursor,3)})
        cursor=end
    # Per scene, attempt Pexels if configured; otherwise use original motion graphic fallback.
    avoid=payload.get("avoid",[])
    clips=[]
    for i,scene in enumerate(timeline):
        progress_cb(10+int(45*(i/max(1,len(timeline)))))
        query=scene.get("visual_hint") or scene["text"]
        results=pexels_search(query,avoid)
        chosen=None
        if results:
            for candidate in results:
                if candidate.get("video_id") in {c.get("video_id") for c in clips}: continue
                try:
                    dest=work/f"clip_{i}.mp4"; fetch_clip(candidate["url"],dest)
                    chosen={**candidate,"path":dest,"ai_generated":False}; break
                except Exception: continue
        if chosen is None:
            dest=work/f"clip_{i}_fallback.mp4"
            chosen={**make_fallback_clip(scene["text"],dest,scene["duration"]),"path":dest,"video_id":None}
        clips.append(chosen)
    progress_cb(60)
    # Create scene clips trimmed to assigned narration time and aspect ratio.
    output_links={}
    for ri,ratio in enumerate(payload.get("aspect_ratios",["16:9"])):
        pieces=[]
        for i,(scene,clip) in enumerate(zip(timeline,clips)):
            p=work/f"render_{ratio.replace(':','x')}_{i}.mp4"
            render_scene(clip["path"],audio,p,scene["start"],scene["end"],ratio)
            pieces.append(p)
        concat=work/f"concat_{ratio.replace(':','x')}.txt"
        concat.write_text("".join(f"file '{p.as_posix()}'\n" for p in pieces),encoding="utf-8")
        silent=work/f"silent_{ratio.replace(':','x')}.mp4"
        run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(concat),"-c","copy","-an",str(silent)])
        final=outdir/f"video_{ratio.replace(':','x')}.mp4"
        run(["ffmpeg","-y","-i",str(silent),"-i",str(audio),"-map","0:v:0","-map","1:a:0",
             "-c:v","libx264","-preset","veryfast","-pix_fmt","yuv420p","-r","30","-c:a","aac","-b:a","192k",
             "-shortest","-movflags","+faststart",str(final)])
        output_links[f"video_{ratio.replace(':','x')}.mp4"]=f"/api/v1/files/{job_id}/{final.name}"
        progress_cb(65+int(25*(ri+1)/len(payload.get("aspect_ratios",["16:9"]))))
    if payload.get("captions",{}).get("srt",True): write_srt(timeline,outdir/"captions.srt")
    for i,(scene,clip) in enumerate(zip(timeline,clips)):
        scene.update({"clip_used":clip["path"].name,"source":clip["provider"],"source_url":clip["source_url"],
            "license":clip["license"],"attribution":clip["attribution"],"ai_generated":clip["ai_generated"],
            "selection_reason":"visual_hint/text query; Pexels search if configured, otherwise original procedural fallback",
            "relevance_score":None,"relevance_score_note":"Not independently model-scored; human rating required"})
    (outdir/"timeline.json").write_text(json.dumps({"job_id":job_id,"audio_duration_seconds":audio_duration,"scenes":timeline},indent=2),encoding="utf-8")
    warnings=[]
    if tts_provider=="espeak-ng": warnings.append("Text narration uses eSpeak NG; voice quality is synthetic.")
    if transcription_provider=="transcription_unavailable": warnings.append("Transcription unavailable; audio-only input uses a generic visual fallback. Set up faster-whisper for transcript-driven scenes.")
    if not os.getenv("PEXELS_API_KEY"): warnings.append("PEXELS_API_KEY not configured; original generated motion graphics used instead of stock footage.")
    manifest={"job_id":job_id,"created_at":datetime.now(timezone.utc).isoformat(),"providers":{"tts":tts_provider,"transcription":transcription_provider,"footage":"Pexels if configured; procedural fallback otherwise","renderer":"FFmpeg"},
      "versions":{"python":os.getenv("PYTHON_VERSION","container"),"ffmpeg":run(["ffmpeg","-version"]).splitlines()[0]},
      "settings":{"aspect_ratios":payload.get("aspect_ratios",["16:9"]),"style":payload.get("style",""),"avoid":avoid,"voice":payload.get("voice","en")},
      "seed":None,"timings":{"total_seconds":round(time.time()-started,2),"audio_seconds":audio_duration},
      "cost_estimate_usd":None,"cost_note":"No service billing estimate configured; measure actual hosting/API charges before reporting cost.",
      "warnings":warnings,"rights":{"per_scene_source_and_license_recorded":True,"commercial_rights_must_be_verified_for_each_provider_asset":True}}
    (outdir/"manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    return {"files":output_links | ({"captions.srt":f"/api/v1/files/{job_id}/captions.srt"} if (outdir/"captions.srt").exists() else {}) |
        {"timeline.json":f"/api/v1/files/{job_id}/timeline.json","manifest.json":f"/api/v1/files/{job_id}/manifest.json"},
        "audio_duration_seconds":audio_duration,"scene_count":len(timeline),"warnings":warnings,
        "timings":{"total_seconds":round(time.time()-started,2)}}

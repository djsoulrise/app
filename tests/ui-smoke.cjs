// Browser contract test with synthetic tracks; never opens Traktor or scans real music.
const fs=require('fs');
const {chromium}=require('playwright');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const page=await browser.newPage({viewport:{width:1400,height:1000}});
 let current=1,saved=.97,job={running:false},starts=[],stops=[];
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const track=id=>({id,artist:'Test Artist',title:'Track '+id,bpm:124,camelot:'10A',energy:6,fmt:'flac',tags:[],score:98,label:'AUDIO RITAM',why:['Test audio'],style:'{}'});
 const match=id=>({id,artist:'Test Artist',title:'Track '+id,score:98,groove:99,tempo_delta:1,audio:{bpm:124},accepted:true,reasons:[]});
 const diagnostic=id=>({current_id:id,current:track(id),threshold:saved,current_audio:{bpm:124,periodicity:.8,consistency:.9,variable_tempo_suspected:false},pool:50,compared:45,counts:{},top:[],accepted_top:Array.from({length:10},(_,i)=>match(i+20))});
 await page.route('http://127.0.0.1:8765/**',async route=>{
  const u=new URL(route.request().url());let body;
  if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync('app/static/index.html','utf8')});
  if(u.pathname.startsWith('/static/'))return route.fulfill({contentType:u.pathname.endsWith('.css')?'text/css':'application/javascript',body:fs.readFileSync('app'+u.pathname,'utf8')});
  if(u.pathname==='/api/status')body={counts:{n:3804},job,broadcast:{},event:null,audio_revision:0,audio_threshold:saved};
  else if(u.pathname==='/api/settings')body={style_strictness:'normalno'};
  else if(u.pathname==='/api/styles')body={};
  else if(u.pathname==='/api/now')body={track_id:current,track:track(current)};
  else if(u.pathname==='/api/suggest')body={current:track(current),items:Array.from({length:5},(_,i)=>track(i+3)),audio_diagnostics:diagnostic(current)};
  else if(u.pathname==='/api/audio/status')body={counts:{ok:3066,error:2},threshold:saved,errors:[]};
  else if(u.pathname==='/api/audio/tracks')body=[track(1),track(2)];
  else if(u.pathname.startsWith('/api/track/'))body=track(+u.pathname.split('/').pop());
  else if(u.pathname==='/api/audio/threshold'){saved=JSON.parse(route.request().postData()).threshold;body={ok:true,threshold:saved};}
  else if(u.pathname==='/api/audio/scan'||u.pathname==='/api/grids/audio-search'){
   const kind=u.pathname==='/api/audio/scan'?'audio':'grid_audio';starts.push(kind);job={running:true,kind,cancellable:true,done:1,total:10,phase:'Test scan'};body={started:true};
  }
  else if(u.pathname==='/api/job/stop'){stops.push(JSON.parse(route.request().postData()).kind);job={...job,running:false,cancelled:true};body={stopping:true};}
  else if(u.pathname==='/api/grids')body={items:[],nml_checked:true};
  else if(u.pathname==='/api/audio/matches')body={current:track(1),scope:'Test library',threshold:+u.searchParams.get('threshold'),saved_threshold:saved,pool:50,compared:45,accepted_total:1,items:[match(20)],accepted_items:[match(20)],recommendation:diagnostic(1)};
  else body=[];
  return route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
 });
 await page.goto('http://127.0.0.1:8765/');
 await page.waitForFunction(()=>document.querySelectorAll('.live-audio-item').length===10);
 if(await page.getByText('Kopiraj putanju',{exact:true}).count())throw Error('Unwanted path button');
 current=2;
 await page.waitForFunction(()=>document.querySelector('#liveaudio > p').textContent.includes('Track 2'),{},{timeout:10000});
 await page.locator('[data-p="audio"]').click();
 await page.locator('#audioThresholdNumber').fill('90');
 if(await page.locator('#audioThresholdRange').inputValue()!=='90')throw Error('Slider sync');
 await page.locator('[data-act="audiothreshold"]').click();
 await page.waitForFunction(()=>document.querySelector('#audioResults').textContent.includes('sacuvani prag preporuka: 90%'));
 await page.locator('[data-scan-start="audio"]').click();
 await page.locator('[data-scan-stop="audio"]:not([disabled])').waitFor();
 await page.locator('[data-scan-stop="audio"]').click();
 await page.locator('[data-p="grids"]').click();
 await page.locator('[data-scan-start="grid_audio"]').click();
 await page.locator('[data-scan-stop="grid_audio"]:not([disabled])').waitFor();
 await page.locator('[data-scan-stop="grid_audio"]').click();
 if(starts.join(',')!=='audio,grid_audio'||stops.join(',')!=='audio,grid_audio')throw Error('Wrong scan control target');
 if(errors.length)throw Error(errors.join('\n'));
 console.log('UI PASS: live 10, track updates, no path button, saved slider, audio/grid Start+Stop');
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});

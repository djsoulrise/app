// Browser contract test with synthetic tracks; never opens Traktor or scans real music.
const fs=require('fs');
const {chromium}=require('playwright');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const page=await browser.newPage({viewport:{width:1100,height:680}});
 let current=1,saved=.97,job={running:false},starts=[],stops=[];
 let shortcuts={actions:{toggle_window:'Prikazi / minimizuj Assistant',focus_search:'Fokus na pretragu',next_suggestion:'Sledeci predlog',copy_song:'Copy Song'},settings:{keyboard_enabled:true,keyboard:{toggle_window:{vk:88,modifiers:2,label:'Ctrl+X'}},midi:{}},sequence:0,connected:false};
 let midiLearns=[],events=[],fileCopies=[],settingsWrites=[],chorusBody=null,chorusResult=null,chorusOptions={auto_scan:true,rhythm:true,tonal:true,rhythm_cutoff:90,tonal_cutoff:85},uiPrefs={font_scale:115,ctrl_wheel_text:true};
 const settings={style_strictness:'normalno',music_folders:[],weights:{hist:3,key:1,bpm:1,vibe:1,energy:1,year:1,genre:1,feedback:1},audience:{},guest_limit:{count:2,minutes:30},broadcast_port:8000};
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const navigate=async p=>{const item=page.locator('#nav [data-p="'+p+'"]');if(!await item.isVisible())await page.locator('#settingsbtn').click();await item.click();};
 const track=id=>({id,artist:'Test Artist',title:'Track '+id,bpm:124,camelot:'10A',energy:6,fmt:'flac',tags:[],score:98,label:'AUDIO RITAM',why:['Test audio'],style:'{}'});
 const match=id=>({match_kind:'Ritam',rhythm_score:98,tonal_score:70,id,artist:'Test Artist',title:'Track '+id,score:98,groove:99,tempo_delta:1,audio:{bpm:124},accepted:true,reasons:[]});
 const diagnostic=id=>({engine:'chorus',options:{rhythm:true,tonal:true,rhythm_cutoff:90,tonal_cutoff:85},shortlist_limit:120,sections:[],current_id:id,current:track(id),threshold:saved,current_audio:{bpm:124,periodicity:.8,consistency:.9,variable_tempo_suspected:false},pool:50,compared:45,counts:{},top:[],accepted_top:Array.from({length:10},(_,i)=>match(i+20))});
 await page.route('http://127.0.0.1:8765/**',async route=>{
  const u=new URL(route.request().url());let body;
  if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync('app/static/index.html','utf8')});
  if(u.pathname.startsWith('/static/'))return route.fulfill({contentType:u.pathname.endsWith('.css')?'text/css':'application/javascript',body:fs.readFileSync('app'+u.pathname,'utf8')});
  if(u.pathname==='/api/audio/chorus/status')body={settings:chorusOptions,counts:{ok:3066},live:{phase:'Spremno'},note:'Tonalni shortlist 120'};
  else if(u.pathname==='/api/audio/chorus/settings'){chorusOptions=JSON.parse(route.request().postData());body=chorusOptions;}
  else if(u.pathname==='/api/audio/chorus/results')body={current:track(current),audio_diagnostics:diagnostic(current)};
  else if(u.pathname==='/api/search')body=[track(1),track(2)];
  else if(u.pathname==='/api/ui-preferences'){if(route.request().method()==='POST')uiPrefs=JSON.parse(route.request().postData());body=uiPrefs;}
  else if(u.pathname==='/api/shortcuts')body={...shortcuts,sequence:events.at(-1)?.sequence || 0};
  else if(u.pathname==='/api/shortcuts/ports')body={ports:['DJ Assistant']};
  else if(u.pathname==='/api/shortcuts/events')body={events:events.filter(e=>e.sequence>+u.searchParams.get('after'))};
  else if(u.pathname==='/api/shortcuts/keyboard'){const d=JSON.parse(route.request().postData());if(d.binding)shortcuts.settings.keyboard[d.action]=d.binding;else delete shortcuts.settings.keyboard[d.action];body=shortcuts;}
  else if(u.pathname==='/api/shortcuts/midi/learn'){midiLearns.push(JSON.parse(route.request().postData()));body=shortcuts;}
  else if(u.pathname.startsWith('/api/shortcuts/'))body=shortcuts;
  else if(u.pathname.startsWith('/api/copy-song/')){fileCopies.push(+u.pathname.split('/').pop());body={ok:true};}
  else if(u.pathname==='/api/status')body={counts:{n:3804},job,broadcast:{},history:{},event:null,audio_revision:0,audio_threshold:saved};
  else if(u.pathname==='/api/settings'){if(route.request().method()==='POST')settingsWrites.push(JSON.parse(route.request().postData()));body=settings;}
  else if(u.pathname==='/api/styles')body={};
  else if(u.pathname==='/api/now')body={track_id:current,track:track(current)};
  else if(u.pathname==='/api/suggest')body={current:track(current),items:Array.from({length:5},(_,i)=>track(i+3)),audio_diagnostics:diagnostic(current)};
  else if(u.pathname==='/api/audio/status')body={counts:{ok:3066,error:2},threshold:saved,errors:[]};
  else if(u.pathname==='/api/audio/tracks')body=[track(1),track(2)];
  else if(u.pathname.startsWith('/api/track/'))body=track(+u.pathname.split('/').pop());
  else if(u.pathname==='/api/audio/threshold'){saved=JSON.parse(route.request().postData()).threshold;body={ok:true,threshold:saved};}
  else if(u.pathname==='/api/audio/scan'||u.pathname==='/api/audio/chorus/scan'||u.pathname==='/api/grids/audio-search'){
   const kind=u.pathname==='/api/audio/scan'?'audio':u.pathname==='/api/audio/chorus/scan'?'chorus_library':'grid_audio';starts.push(kind);job={running:true,kind,cancellable:true,done:1,total:10,phase:'Test scan'};body={started:true};
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
 async function checkConsole(width,height){
  await page.setViewportSize({width,height});
  await page.waitForTimeout(100);
  const result=await page.evaluate(()=>{
   const rows=[...document.querySelectorAll('.recommendation')],live=[...document.querySelectorAll('.live-audio-item')];
   const inside=el=>{const b=el.getBoundingClientRect();return b.top>=0&&b.bottom<=innerHeight&&b.left>=0&&b.right<=innerWidth;};
   const boxes=[document.querySelector('.suggestions-panel').getBoundingClientRect(),document.querySelector('.live-panel').getBoundingClientRect()];
   return {rows:rows.length,live:live.length,visible:[...rows,...live,...document.querySelectorAll('.rec-actions button,.live-actions button')].every(inside),equal:Math.abs(boxes[0].height-boxes[1].height)<1,side:boxes[1].left>boxes[0].left,noScroll:document.documentElement.scrollHeight<=innerHeight+1,noClip:[document.querySelector('#sug'),document.querySelector('.live-list')].every(el=>el.scrollHeight<=el.clientHeight+1),hidden:!document.querySelector('#nav').getBoundingClientRect().height,overflow:rows.some(el=>el.scrollHeight>el.clientHeight+2)||live.some(el=>el.scrollHeight>el.clientHeight+2)};
  });
  await page.screenshot({path:'ui-console-'+width+'x'+height+'.png'});
  if(result.rows!==5||result.live!==10||!result.visible||!result.equal||!result.side||!result.noScroll||!result.noClip||!result.hidden||result.overflow)throw Error('Compact console '+width+'x'+height+': '+JSON.stringify(result));
 }
 await checkConsole(1100,680);await checkConsole(940,610);await page.setViewportSize({width:1100,height:680});
 await page.locator('[data-act="consoledetails"]').click();
 await page.locator('#modal:not(.hidden)').waitFor();
 await page.locator('[data-act="closemodal"]').click();
 current=2;
 await page.waitForFunction(()=>document.querySelector('#liveaudio .sr-only').textContent.includes('Track 2'),{},{timeout:10000});
 await navigate('audio');
 await page.locator('#chorusR').fill('93');
 await page.locator('#chorusTonal').uncheck();
 await page.locator('[data-act="chorussave"]').click();
 await page.waitForTimeout(100);
 if(chorusOptions.rhythm_cutoff!==93||chorusOptions.tonal!==false||!chorusOptions.auto_scan||!chorusOptions.whole_library)throw Error('Automatic audio settings');
 if(await page.locator('#nav [data-p="chorus"]').count())throw Error('Unwanted separate manual comparison');
 await page.locator('[data-scan-start="chorus_library"]').click();
 await page.locator('[data-scan-stop="chorus_library"]:not([disabled])').waitFor();
 await page.locator('[data-scan-stop="chorus_library"]').click();
 await navigate('grids');
 await page.locator('[data-scan-start="grid_audio"]').click();
 await page.locator('[data-scan-stop="grid_audio"]:not([disabled])').waitFor();
 await page.locator('[data-scan-stop="grid_audio"]').click();
 if(starts.join(',')!=='chorus_library,grid_audio'||stops.join(',')!=='chorus_library,grid_audio')throw Error('Wrong scan control target');
 await navigate('shortcuts');
 await page.locator('[data-act="shortcutkey"][data-action="focus_search"]').click();
 await page.waitForFunction(()=>state.keyLearn==='focus_search');
 await page.keyboard.press('Control+Alt+f');
 await page.waitForFunction(()=>document.querySelector('#key_focus_search').textContent==='Ctrl+Alt+F');
 if(shortcuts.settings.keyboard.toggle_window.label!=='Ctrl+X')throw Error('Keyboard learn changed another action');
 await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/shortcuts/midi/learn')),page.locator('[data-act="shortcutmidi"][data-action="toggle_window"]').click()]);
 if(midiLearns.length!==1||!midiLearns[0].combination)throw Error('MIDI combination learning');
 await page.locator('#combo_focus_search').uncheck();
 await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/shortcuts/midi/learn')),page.locator('[data-act="shortcutmidi"][data-action="focus_search"]').click()]);
 if(midiLearns[1].combination)throw Error('MIDI single button learning');
 if(shortcuts.settings.keyboard.focus_search.label!=='Ctrl+Alt+F')throw Error('MIDI learn overwrote keyboard');
 events.push({sequence:1,action:'focus_search'});
 await page.locator('#q').waitFor();
 await page.waitForFunction(()=>document.activeElement?.id==='q');
 events.push({sequence:2,action:'next_suggestion'},{sequence:3,action:'copy_song'});
 await page.waitForFunction(()=>document.querySelector('[data-shortcut-id="4"]').classList.contains('shortcut-selected'));
 await new Promise(resolve=>setTimeout(resolve,600));
 if(fileCopies.join(',')!=='4')throw Error('Copy must target selected suggestion exactly once: '+fileCopies);
 await navigate('set');
 await page.locator('#fontScaleRange').waitFor();
 const before=await page.evaluate(()=>({font:parseFloat(getComputedStyle(document.body).fontSize),width:innerWidth,scale:fontScale}));
 await page.keyboard.down('Control');await page.mouse.wheel(0,-100);await page.keyboard.up('Control');
 await page.waitForFunction(()=>fontScale===120);
 const after=await page.evaluate(()=>({font:parseFloat(getComputedStyle(document.body).fontSize),width:innerWidth}));
 if(after.font<=before.font||after.width!==before.width)throw Error('Ctrl wheel must enlarge text without zooming viewport');
 await page.locator('#ctrlWheelText').uncheck();
 await page.keyboard.down('Control');await page.mouse.wheel(0,-100);await page.keyboard.up('Control');
 await page.waitForTimeout(150);
 if(await page.evaluate(()=>fontScale)!==120)throw Error('Disabled Ctrl wheel changed font');
 await page.waitForTimeout(250);
 await page.evaluate(()=>{localStorage.removeItem('djFontScale');localStorage.removeItem('djCtrlWheelText');});
 await page.reload();await page.waitForFunction(()=>document.querySelectorAll('.recommendation').length===5 && fontScale===120 && ctrlWheelText===false);
 if(await page.evaluate(()=>fontScale)!==120||await page.evaluate(()=>ctrlWheelText)!==false)throw Error('Font preferences did not persist');
 await navigate('set');await page.locator('#ctrlWheelText').check();
 await page.locator('[data-act="fontreset"]').click();
 if(await page.locator('#fontScaleValue').textContent()!=='115%')throw Error('Font reset');
 await page.locator('[data-act="saveset"]').first().click();
 await page.waitForTimeout(150);
 const savedWeights=settingsWrites.findLast(s=>s.weights);
 if(!savedWeights||Object.keys(savedWeights.weights).some(k=>k==='ntScaleRange'))throw Error('Text slider contaminated ranking weights');
 if(await page.title()!=='DJ Miljan Assistant')throw Error('App name');
 if(errors.length)throw Error(errors.join('\n'));
 console.log('UI PASS: compact console 5+10 without scroll at 1100x680 and 940x610, menu, Start/Stop, keyboard+MIDI learn, selected file shortcuts');
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});


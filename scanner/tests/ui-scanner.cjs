const fs=require('fs'),{chromium}=require('playwright');
(async()=>{
 const browser=await chromium.launch(process.platform==='win32'?{channel:'msedge',headless:true}:{headless:true});
 const page=await browser.newPage({viewport:{width:1150,height:800}}),errors=[],calls=[];
 page.on('pageerror',e=>errors.push(e.message));let job={running:false,phase:'Miruje',done:0,total:0,active:{},workers:0,errors:[]},settings={roots:['D:/Music'],workers:4,turbo:false,thresholds:{tonal:55,rhythm:65,sample:70}};
 const track=(hash,path)=>({hash,path});const metric={score:84,contrast:2.4,seconds:12,a_section:[1,13],b_section:[3,15]};
 await page.route('http://127.0.0.1:8766/**',async route=>{
 const u=new URL(route.request().url());let data={};
 if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync('scanner/static/index.html','utf8')});
 calls.push(u.pathname);
 if(u.pathname==='/api/status')data={job,settings,counts:{ok:3,error:1},pairs:2,cpu:16,database:'C:/Users/Test/AppData/Roaming/DJ Miljan Audio Scanner/scanner.db'};
 else if(u.pathname==='/api/pairs')data=[{a:track('a','D:/Music/Alpha.mp3'),b:track('b','D:/Music/Beta.mp3'),accepted:['tonal'],metrics:{tonal:metric,rhythm:metric,sample:metric},feedback:0}];
 else if(u.pathname==='/api/settings'){settings=JSON.parse(route.request().postData());data=settings;}
 else if(u.pathname==='/api/scan'){job={...job,running:true,phase:'Skeniram audio osobine',total:4,done:1,active:{one:'Alpha.mp3',two:'Beta.mp3'},workers:2};data={started:true};}
 else if(u.pathname.startsWith('/api/control/')){const action=u.pathname.split('/').pop();if(action==='stop')job={...job,running:false,active:{},cancelled:true,phase:'Zaustavljeno'};else job.paused=action==='pause';data={ok:true};}
 else if(u.pathname.startsWith('/api/export/'))data={path:'C:/Exports/Test.djmatches',tracks:3,pairs:2};
 else if(u.pathname==='/api/files')data=[{path:'D:/Music/Alpha.mp3',status:'ok',error:null}];
 return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
 });
 await page.goto('http://127.0.0.1:8766/');await page.locator('.pair').waitFor();
 if(await page.locator('.metric').count()!==3)throw Error('Missing independent criteria');
 await page.locator('#workers').fill('3');await page.locator('#turbo').check();await page.locator('#save').click();
 if(settings.workers!==3||!settings.turbo)throw Error('Worker settings were not saved');
 await page.locator('#scan').click();await page.waitForFunction(()=>document.querySelector('#scan').disabled);
 await page.locator('#pause').click();await page.waitForTimeout(100);if(!job.paused)throw Error('Pause');
 await page.locator('#resume').click();await page.waitForTimeout(100);if(job.paused)throw Error('Resume');
 await page.locator('#stop').click();await page.waitForFunction(()=>!document.querySelector('#scan').disabled);
 await page.locator('[data-export="results"]').click();await page.waitForFunction(()=>document.querySelector('#notice').textContent.includes('.djmatches'));
 await page.locator('summary').filter({hasText:'Proveri sta je skenirano'}).click();await page.locator('#check').click();await page.waitForFunction(()=>document.querySelector('#files').textContent.includes('Alpha.mp3'));
 if(calls.some(x=>/midi/.test(x)))throw Error('Controller connection attempted');
 await page.screenshot({path:'ui-scanner.png',fullPage:true});
 if(errors.length)throw Error(errors.join('\n'));
 console.log('Scanner UI PASS: criteria, workers, Start Pause Resume Stop, export, read-only cache check; no controller endpoints');await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});

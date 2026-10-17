'use strict';
// AGPL-3.0-or-later. Uses unmodified Essentia.js 0.1.3 (AGPL-3.0).
const fs=require('fs'),cp=require('child_process'),path=require('path');
const W=require('./node_modules/essentia.js/dist/essentia-wasm.umd.js');
const E=require('./node_modules/essentia.js/dist/essentia.js-core.umd.js');
const e=new E(W);
function vv(rows){const v=new W.VectorVectorFloat();for(const row of rows){const x=e.arrayToVector(Float32Array.from(row));v.push_back(x);x.delete();}return v;}
function extract(file,decoder){
 const p=cp.spawnSync(decoder,['-v','error','-threads','1','-i',file,'-vn','-ac','1','-ar','22050','-t','1800','-f','f32le','pipe:1'],{windowsHide:true,maxBuffer:1800*22050*4+65536,timeout:90000});
 if(p.error||p.status!==0)throw Error(p.error?.message||p.stderr.toString().slice(-500));
 const y=new Float32Array(p.stdout.buffer,p.stdout.byteOffset,p.stdout.length/4),out=[];let sum=Array(12).fill(0),count=0;
 if(y.length<22050*8)throw Error('Audio je kraci od 8 sekundi.');
 for(let at=-2048;at<y.length;at+=2048){
  const frame=new Float32Array(4096),from=Math.max(0,at),end=Math.min(y.length,at+4096);frame.set(y.subarray(from,end),from-at);
  const v=e.arrayToVector(frame),win=e.Windowing(v,true,4096,'blackmanharris62',0,true).frame,spec=e.Spectrum(win).spectrum;
  const peaks=e.SpectralPeaks(spec,1e-5,4000,100,100,'frequency',22050);
  const h=e.HPCP(peaks.frequencies,peaks.magnitudes,true,500,0,4000,false,100,false,'unitMax',440,22050,12,'squaredCosine',1).hpcp;
  const a=e.vectorToArray(h);for(let i=0;i<12;i++)sum[i]+=a[i];count++;
  if(count===2){out.push(sum.map(x=>x/count));sum.fill(0);count=0;}
  for(const x of [v,win,spec,peaks.frequencies,peaks.magnitudes,h])x.delete();
 }
 if(count)out.push(sum.map(x=>x/count));
 if(!out.every(row=>row.every(Number.isFinite)))throw Error('Nekonacne audio osobine.');
 return {hpcp:out,step:4096/22050,seconds:y.length/22050};
}
function align(a,b,kind='tonal',control=false){
 const stack=kind==='rhythm'?1:9,va=vv(a),vb=vv(b);let cross,res;
 try{
 cross=kind==='tonal'?e.ChromaCrossSimilarity(va,vb):e.CrossSimilarityMatrix(va,vb,true,.095,stack,1);
 res=e.CoverSongSimilarity(cross.csm);
 let peak=0,pi=0,pj=0;const matrix=[];
 for(let i=0;i<res.scoreMatrix.size();i++){const v=res.scoreMatrix.get(i),row=Array.from(e.vectorToArray(v));matrix.push(row);v.delete();for(let j=0;j<row.length;j++)if(row[j]>peak){peak=row[j];pi=i;pj=j;}}
 let i=pi,j=pj,steps=0;while(i>2&&j>2&&matrix[i][j]>0){const choices=[[i-1,j-1],[i-2,j-1],[i-1,j-2]].sort((a,b)=>matrix[b[0]][b[1]]-matrix[a[0]][a[1]]);[i,j]=choices[0];if(++steps>20000)break;}
 return {distance:Number.isFinite(res.distance)?res.distance:999,peak,start:[i,j],end:[pi+stack,pj+stack]};
 }finally{for(const v of [va,vb,cross?.csm,res?.scoreMatrix])if(v)v.delete();}
}
function shuffled(rows){const blocks=[];for(let i=0;i<rows.length;i+=8)blocks.push(rows.slice(i,i+8));let seed=731;for(let i=blocks.length-1;i>0;i--){seed=(Math.imul(seed,1664525)+1013904223)>>>0;const j=seed%(i+1);[blocks[i],blocks[j]]=[blocks[j],blocks[i]];}return blocks.flat();}
function compare(request){
 const out={};
 for(const kind of ['tonal','rhythm','sample']){
  const a=request.a[kind],b=request.b[kind];if(!a||!b||a.length<12||b.length<12)continue;
  const match=align(a,b,kind),control=align(a,shuffled(b),kind);
  out[kind]={...match,control:control.distance,contrast:match.peak/Math.max(1,control.peak),score:100/(1+5*match.distance)};
 }
 return out;
}
function handle(input){
 const result=input.operation==='extract'?extract(input.path,input.decoder):input.operation==='compare'?compare(input):{ok:true,engine:'Essentia.js 0.1.3',pid:process.pid};
 return {ok:true,result};
}
function reply(text){try{return JSON.stringify(handle(JSON.parse(text)));}catch(err){return JSON.stringify({ok:false,error:String(err.message).slice(0,600)});}}
if(process.argv.includes('--persistent')){
 const readline=require('readline');
 const input=readline.createInterface({input:process.stdin,crlfDelay:Infinity});
 input.on('line',line=>{process.stdout.write(reply(line)+'\n');});
}else{
 const output=reply(fs.readFileSync(0,'utf8'));process.stdout.write(output);
 if(!JSON.parse(output).ok)process.exitCode=1;
}

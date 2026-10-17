"""Reproducible export of the published MIT-licensed Discogs-VINet checkpoint."""
import hashlib
import json
import sys
import os
os.environ['ORT_DISABLE_TELEMETRY']='1'
import urllib.request
from pathlib import Path
import numpy as np
import torch
import onnxruntime as ort
from vinet_model import CQTNet

ROOT=Path(__file__).resolve().parent.parent
REVISION='3029d61c6f8e6ef9991acd2f0e4743e748134d9a'
SHA='9d0b1d5cdd459a22b1062f69f0ef12ff59d0681a5d90d338827598dc55e8bf13'
URL=f'https://raw.githubusercontent.com/raraz15/Discogs-VINet/{REVISION}/logs/checkpoints/Discogs-VINet/model_checkpoint.pth'

def main():
 torch.set_num_threads(2);folder=ROOT/'assets';folder.mkdir(exist_ok=True)
 checkpoint=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'build'/'vinet-checkpoint.pth'
 if not checkpoint.exists():
  checkpoint.parent.mkdir(exist_ok=True)
  with urllib.request.urlopen(URL,timeout=120) as source,checkpoint.open('wb') as output:
   while block:=source.read(1024*1024):output.write(block)
 if hashlib.sha256(checkpoint.read_bytes()).hexdigest()!=SHA:raise ValueError('Pretrained checkpoint checksum mismatch')
 model=CQTNet().eval();state=torch.load(checkpoint,map_location='cpu',weights_only=True);model.load_state_dict(state['model_state_dict'],strict=True)
 example=torch.rand(1,1,84,400);target=folder/'discogs-vinet.onnx'
 torch.onnx.export(model,example,str(target),opset_version=17,dynamo=False,input_names=['cqt'],output_names=['embedding'],do_constant_folding=True)
 session=ort.InferenceSession(str(target),providers=['CPUExecutionProvider'])
 errors=[]
 for seed in range(3):
  torch.manual_seed(seed);x=torch.rand(1,1,84,400)
  with torch.inference_mode():expected=model(x).numpy()
  actual=session.run(None,{'cqt':x.numpy()})[0];error=float(np.max(np.abs(actual-expected)));errors.append(error)
  if error>1e-4:raise ValueError(f'ONNX differs from original model: {error}')
 metadata={'name':'Discogs-VINet ISMIR2024','source_revision':REVISION,'checkpoint_sha256':SHA,'onnx_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'onnx_parity_max_error':max(errors),'sample_rate':22050,'hop_length':512,'bins':84,'context_frames':8000,'downsample_factor':20,'input_shape':[1,1,84,400],'embedding_size':512,'trained_here':False,'license':'MIT'}
 (folder/'discogs-vinet.json').write_text(json.dumps(metadata,indent=2));print(json.dumps(metadata),flush=True)
if __name__=='__main__':main()

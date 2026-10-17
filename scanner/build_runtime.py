"""Fetch a pinned official Windows Node runtime and verify its published SHA256."""
import hashlib,urllib.request,zipfile
from pathlib import Path
VERSION='24.19.0'
EXPECTED='57f71ab3652e797d84acddc79c81cc9ff1c6ddb2a1974cdb83f00fee9bff4c73'
root=Path(__file__).parent/'runtime';root.mkdir(exist_ok=True)
name=f'node-v{VERSION}-win-x64.zip';base=f'https://nodejs.org/dist/v{VERSION}/'
checks=urllib.request.urlopen(base+'SHASUMS256.txt',timeout=60).read().decode()
expected=next(line.split()[0] for line in checks.splitlines() if line.split()[-1]==name)
assert expected==EXPECTED,'Official checksum differs from pinned build'
archive=root/'node.zip';urllib.request.urlretrieve(base+name,archive)
assert hashlib.sha256(archive.read_bytes()).hexdigest()==expected,'Node SHA256 mismatch'
with zipfile.ZipFile(archive) as z:
    for suffix,destination in [('node.exe','node.exe'),('LICENSE','NODE-LICENSE.txt')]:
        key=next(n for n in z.namelist() if n.endswith('/'+suffix));(root/destination).write_bytes(z.read(key))
archive.unlink()
import subprocess,imageio_ffmpeg
version=imageio_ffmpeg.get_ffmpeg_version()
license_text=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-L'],capture_output=True,text=True,check=True)
(root/'FFMPEG-LICENSE.txt').write_text(license_text.stdout+'\n'+license_text.stderr+'\nFFmpeg version: '+version+'\nSource/build: https://github.com/imageio/imageio-ffmpeg/tree/v0.6.0\nUpstream source: https://ffmpeg.org/download.html\n',encoding='utf-8')
print('Verified Node',VERSION,expected)

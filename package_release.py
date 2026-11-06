"""Explicit allowlist: never include snapshots, keys, chat records, or build caches."""
import hashlib
import importlib.metadata as metadata
import json
import shutil
import sys
import zipfile
from pathlib import Path
from version import BUILD_NAME, VERSION

root=Path(__file__).resolve().parent
release=root/(sys.argv[1] if len(sys.argv)>1 else 'dist')/BUILD_NAME
if release.resolve().parent.parent != root or not (release/(BUILD_NAME+'.exe')).is_file():
    raise ValueError('Expected the versioned executable in a local build directory')
source=release/'source'
source.mkdir(exist_ok=True)
files=['app.py','database.py','exporter.py','media.py','native.py','snapshot.py','version.py','Shiliao.spec','package_release.py','PYTHON-LICENSE.txt',
       'offline_guard.py','test_export.py','fetch_tk_resources.py','requirements.txt',
       'README.md','NOTICE.txt','LICENSE-Apache-2.0.txt','TESTING.md']
for name in files:
    shutil.copyfile(root/name,source/name)
for name in ['README.md','NOTICE.txt','LICENSE-Apache-2.0.txt','TESTING.md','packaged-test.json']:
    shutil.copyfile(root/name,release/name)
licenses=release/'third-party-licenses'
licenses.mkdir(exist_ok=True)
shutil.copyfile(root/'PYTHON-LICENSE.txt',licenses/'PYTHON-LICENSE.txt')
for name in ('tcl','tk'):
    shutil.copyfile(root/'vendor'/(name+'-license.terms'),licenses/(name+'-license.terms'))
for package in ['av','cryptography','Pillow','zstandard','pyinstaller']:
    distribution=metadata.distribution(package)
    for item in distribution.files or []:
        if '.dist-info/' in item.as_posix() and any(x in item.name.lower() for x in ('license','copying','notice')):
            dest=licenses/package/Path(*item.parts[1:])
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(distribution.locate_file(item),dest)
manifest={}
for p in sorted(release.rglob('*')):
    if not p.is_file() or p == release/'SHA256SUMS.json': continue
    assert p.suffix.lower() not in ('.db','.dat','.walcopy','.encrypted','.partial'),p
    assert 'Shiliao-' not in p.name or p == release/(BUILD_NAME+'.exe'),p
    manifest[p.relative_to(release).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
(release/'SHA256SUMS.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
outputs=Path(sys.argv[2]).resolve() if len(sys.argv)>2 else root/'outputs'
outputs.mkdir(exist_ok=True)
target=outputs/f'拾聊-{VERSION}-Windows.zip'
staged=target.with_suffix('.building')
with zipfile.ZipFile(staged,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
    for p in sorted(release.rglob('*')):
        if p.is_file(): archive.write(p,Path(BUILD_NAME)/p.relative_to(release))
with zipfile.ZipFile(staged) as archive:
    assert archive.testzip() is None
staged.replace(target)
print(json.dumps({'file':str(target),'bytes':target.stat().st_size,'files':len(manifest)+1,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}))

"""Build-time only: obtain Tcl/Tk scripts from their official source repositories."""
import io
import zipfile
from pathlib import Path
from urllib.request import urlopen

root=Path(__file__).resolve().parent/'vendor'
for project,commit in [('tcl','eea5dbac2620e113f750915cfb87561075082368'),('tk','f0b89d22b06e23dd4a415f5c1fcf6f8e9281d9c5')]:
    with urlopen('https://codeload.github.com/tcltk/'+project+'/zip/'+commit,timeout=45) as response:
        raw=response.read()
    count=0
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for name in archive.namelist():
            parts=name.split('/')
            if len(parts)>2 and parts[1]=='library' and not name.endswith('/'):
                dest=root/(project+'8.6')/Path(*parts[2:])
                assert dest.resolve().is_relative_to(root.resolve())
                dest.parent.mkdir(parents=True,exist_ok=True)
                dest.write_bytes(archive.read(name))
                count+=1
            elif len(parts)==2 and parts[1]=='license.terms':
                root.mkdir(parents=True,exist_ok=True)
                (root/(project+'-license.terms')).write_bytes(archive.read(name))
    print(project,'scripts',count)

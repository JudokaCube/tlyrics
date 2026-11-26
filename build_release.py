#!/usr/bin/env python3
"""Build the public release archive and checksum without personal files."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import deployment


def build(source, output):
    version, files = deployment.manifest(source)
    files = sorted(set(files) | {'install.sh', 'README.md'})
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f'tlyrics-{version}.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        contents = {name: (source / name).read_bytes() for name in files}
        contents['release.json'] = json.dumps({'version': version, 'files': files}).encode()
        for name, content in sorted(contents.items()):
            info = tarfile.TarInfo(f'tlyrics-{version}/{name}')
            info.size = len(content)
            info.mode = 0o755 if name == 'install.sh' else 0o644
            tar.addfile(info, io.BytesIO(content))
    (output / 'SHA256SUMS').write_text(f'{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n')
    return archive


if __name__ == '__main__':
    print(build(Path(__file__).resolve().parent, Path('dist')))

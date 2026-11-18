"""Compile checked-in LSMS message catalogues without system gettext. Revisions: Git."""

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import polib


def main(*, check=False):
    root = Path(__file__).resolve().parents[1]
    for path in sorted((root / 'locale').glob('*/LC_MESSAGES/*.po')):
        destination = path.with_suffix('.mo')
        with TemporaryDirectory() as temp:
            compiled = Path(temp) / destination.name
            polib.pofile(str(path)).save_as_mofile(str(compiled))
            content = compiled.read_bytes()
            if check:
                if not destination.is_file() or content != destination.read_bytes():
                    raise SystemExit(f'Stale catalogue: {destination.relative_to(root)}')
            else:
                destination.write_bytes(content)
            print(f'{"Checked" if check else "Compiled"} {destination.relative_to(root)}')


if __name__ == '__main__':
    main(check='--check' in sys.argv)

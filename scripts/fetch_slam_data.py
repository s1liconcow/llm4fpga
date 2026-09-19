#!/usr/bin/env python3
"""Fetch the small, pinned Intel Research Lab CARMEN dataset, with attribution."""
import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

COMMIT = 'e3f9e14ee00a07edcb13cebf5a019bfdf0eff11b'
URL = f'https://raw.githubusercontent.com/MOLAorg/mola_test_datasets/{COMMIT}/datasets/radish/intel.clf.bz2'
SHA256 = 'd9ce4addefc9405d5e78fd883d2214766552537d262009f8e6f0137885f9f72a'
ATTRIBUTION = {
    'dataset':'Intel Research Lab 2D laser data',
    'raw_log_provided_by':'Dirk Haehnel',
    'attribution_source':'https://www.ipb.uni-bonn.de/datasets/',
    'mirror':'https://molaorg.github.io/mola_test_datasets/datasets/radish/',
    'license':'CC-BY-3.0, as distributed by the MOLA mirror',
    'license_url':f'https://github.com/MOLAorg/mola_test_datasets/blob/{COMMIT}/datasets/radish/LICENSE',
    'url':URL,'sha256':SHA256,'modified':False,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=Path('runs/data/intel.clf.bz2'))
    args = parser.parse_args()
    if args.out.exists():
        data = args.out.read_bytes()
    else:
        with urllib.request.urlopen(URL,timeout=60) as response:
            data = response.read(8_000_001)
    if hashlib.sha256(data).hexdigest()!=SHA256:
        raise SystemExit('Dataset checksum mismatch; existing files were left unchanged')
    args.out.parent.mkdir(parents=True,exist_ok=True)
    if not args.out.exists():
        args.out.write_bytes(data)
    args.out.with_suffix('.attribution.json').write_text(json.dumps(ATTRIBUTION,indent=2)+'\n')
    print(args.out)
    print('Intel Research Lab data, provided by Dirk Haehnel; MOLA mirror, CC-BY-3.0.')


if __name__=='__main__':
    main()

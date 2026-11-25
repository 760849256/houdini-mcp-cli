"""Create a hashed, bridge-only immutable candidate without editing the checkout."""
from __future__ import annotations
import argparse,hashlib,json,re,zipfile
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
EXCLUDED={".git","__pycache__", ".pytest_cache", ".test_home", ".blib_hou_workflows", ".venv"}
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--extract-to",type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists() or args.extract_to.exists():parser.error("candidate paths already exist; choose a new revision")
    files={}
    for path in sorted(ROOT.rglob("*")):
        rel=path.relative_to(ROOT)
        if not path.is_file() or any(part in EXCLUDED for part in rel.parts):continue
        if path.suffix in {".pyc",".pyo"} or path.name.endswith(".local.json") or path.name=="release_manifest.json":continue
        files[rel.as_posix()]=path.read_bytes()
    version=re.search(r'BRIDGE_VERSION = "([^"]+)"',files["scripts/python/blib_hou_bridge/protocol.py"].decode()).group(1)
    manifest={"schema":1,"version":version,"created_at":datetime.now(timezone.utc).isoformat(),
        "status":"candidate; endurance and actual working-day gates remain separate",
        "files":{name:hashlib.sha256(data).hexdigest() for name,data in files.items()}}
    files["release_manifest.json"]=(json.dumps(manifest,indent=2)+"\n").encode()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(args.output,"x",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for name,data in files.items():archive.writestr(name,data)
    with zipfile.ZipFile(args.output) as archive:archive.extractall(args.extract_to)
    result={"version":version,"file_count":len(files),"zip":str(args.output.resolve()),
        "sha256":hashlib.sha256(args.output.read_bytes()).hexdigest(),"snapshot":str(args.extract_to.resolve())}
    print(json.dumps(result,indent=2))
if __name__=="__main__":main()

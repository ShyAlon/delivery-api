"""Print the third-party requirements of the built wheels, one per line, for `pip install -r`.

The product installs its packages without dependency resolution (scripts/build-dist.sh uses --no-deps):
verification-loop still pins an old vmodel-contracts commit by git URL, which cannot be fetched in the
image. vmodel-contracts is installed from the vendored wheel instead. Optional extras are skipped."""
import glob,os,re,sys,zipfile

SKIP={'vmodel-contracts','requi-child-contract'}


def norm(name):
    return re.sub(r'[-_.]+','-',name).lower()


def requirements(wheel_dir):
    out=set()
    for wheel in sorted(glob.glob(os.path.join(wheel_dir,'*.whl'))):
        with zipfile.ZipFile(wheel) as z:
            meta=[n for n in z.namelist() if n.endswith('.dist-info/METADATA')][0]
            for line in z.read(meta).decode('utf-8').splitlines():
                if not line.startswith('Requires-Dist:'):continue
                req=line.split(':',1)[1].strip()
                spec,_,marker=req.partition(';')
                if 'extra' in marker or ' @ ' in spec:continue
                name=re.match(r'[A-Za-z0-9_.-]+',spec.strip()).group(0)
                if norm(name) in SKIP:continue
                out.add(req)
    return sorted(out)


if __name__=='__main__':
    print('\n'.join(requirements(sys.argv[1])))

"""Read only the launcher host, never Docker's guest/container memory.

Available memory is a reclaimability estimate, not a reservation. Purgeable
pages overlap other page lists and must not be added again. The sampler lives
only as long as the already-running attended preview launcher.
"""
import contextlib,json,platform,re,subprocess,threading,time
from pathlib import Path

CONTAINER_DIRECTORY='/run/requi-host'
CONTAINER_SNAPSHOT=CONTAINER_DIRECTORY+'/memory.json'


def parse_mac(total,text,*,now=None,machine='arm64'):
    total=int(total.strip())
    page=re.search(r'page size of (\d+) bytes',text)
    if not page or int(page[1]) not in (4096,16384,65536) or total<=0:raise ValueError('host memory unavailable')
    counts=[]
    for name in ('free','inactive','speculative'):
        found=re.search(r'^Pages '+name+r':\s+(\d+)\.\s*$',text,re.M)
        if not found:raise ValueError('incomplete host memory counters')
        counts.append(int(found[1]))
    available=sum(counts)*int(page[1])
    if not 0<=available<=total:raise ValueError('inconsistent host memory counters')
    return {'schema':1,'source':'macos-launcher','platform':'Darwin','machine':machine,
        'sampled_at':time.time() if now is None else now,'total_bytes':total,
        'available_estimate_bytes':available,
        'available_method':'free+inactive+speculative pages; reclaimability estimate'}


def sample():
    if platform.system()!='Darwin':return None
    def read(args):
        result=subprocess.run(args,capture_output=True,text=True,timeout=3,env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','LC_ALL':'C'})
        if result.returncode:raise ValueError('host memory command unavailable')
        return result.stdout
    try:return parse_mac(read(['/usr/sbin/sysctl','-n','hw.memsize']),read(['/usr/bin/vm_stat']),machine=platform.machine())
    except (OSError,ValueError,subprocess.TimeoutExpired):return None


class HostMemory:
    def __init__(self,directory,*,reader=None,interval=10):
        self.directory=Path(directory).absolute();self.reader=reader or sample;self.interval=interval
        self.stop=threading.Event();self.thread=None
    @property
    def snapshot(self):return self.directory/'memory.json'
    def update(self):
        from image_receipt import atomic_write
        value=self.reader()
        if self.stop.is_set():return
        # A failed sample invalidates the previous reading immediately.
        atomic_write(self.snapshot,json.dumps(value or {'schema':1,'source':'unavailable'}).encode())
        # Directory is mounted read-only into an unprivileged container. This
        # file has host capacity only, no identifiers, credentials or paths.
        self.snapshot.chmod(0o644)
    def __enter__(self):
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o755)
        if self.directory.is_symlink():raise ValueError('host telemetry directory must not be a symlink')
        self.directory.chmod(0o755);self.update()
        def loop():
            while not self.stop.wait(self.interval):
                try:self.update()
                except OSError:pass # Existing reading expires; never invent a fit.
        self.thread=threading.Thread(target=loop,name='local-host-memory',daemon=True);self.thread.start()
        return self
    def __exit__(self,*args):
        self.stop.set()
        if self.thread:self.thread.join(8)
        with contextlib.suppress(OSError):self.snapshot.unlink()
    def mount_arguments(self):
        path=str(self.directory)
        if any(c in path for c in ',\n\r\0'):raise ValueError('invalid host telemetry directory')
        return ['--mount','type=bind,source='+path+',target='+CONTAINER_DIRECTORY+',readonly']

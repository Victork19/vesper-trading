import threading,time
from collections import defaultdict,deque
from datetime import datetime,timezone

class MetricsRegistry:
 def __init__(self):
  self.lock=threading.RLock();self.counters=defaultdict(float);self.gauges=defaultdict(float);self.histograms=defaultdict(lambda: {'count':0,'sum':0.0,'buckets':defaultdict(int)});self.recent_errors=deque(maxlen=500);self.started_at=time.time()
  self.buckets=(.005,.01,.025,.05,.1,.25,.5,1,2.5,5,10)
 def _key(self,name,labels):return name+''.join(f'{{{k}="{str(v).replace(chr(34),chr(39))}"}}' for k,v in sorted((labels or {}).items()))
 def inc(self,name,value=1,labels=None):
  with self.lock:self.counters[self._key(name,labels)]+=value
 def set(self,name,value,labels=None):
  with self.lock:self.gauges[self._key(name,labels)]=value
 def observe(self,name,value,labels=None):
  key=self._key(name,labels)
  with self.lock:
   h=self.histograms[key];h['count']+=1;h['sum']+=value
   for bucket in self.buckets:
    if value<=bucket:h['buckets'][bucket]+=1
 def error(self,kind):
  with self.lock:self.recent_errors.append(time.time());self.inc('vesper_errors_total',labels={'kind':kind})
 def snapshot(self):
  with self.lock:
   now=time.time();recent=sum(1 for x in self.recent_errors if now-x<300);return {'uptime_seconds':round(now-self.started_at,3),'counters':dict(self.counters),'gauges':dict(self.gauges),'recent_errors_5m':recent}
 def prometheus(self):
  with self.lock:
   lines=['# HELP vesper_process_uptime_seconds Process uptime.','# TYPE vesper_process_uptime_seconds gauge',f'vesper_process_uptime_seconds {time.time()-self.started_at}']
   for key,value in self.counters.items():lines.extend([f'# TYPE {key.split("{")[0]} counter',f'{key} {value}'])
   for key,value in self.gauges.items():lines.extend([f'# TYPE {key.split("{")[0]} gauge',f'{key} {value}'])
   for key,h in self.histograms.items():
    base=key.split('{')[0];labels=key[len(base):] if '{' in key else ''
    lines.append(f'# TYPE {base} histogram')
    for bucket,count in h['buckets'].items():
     suffix=labels[:-1]+(',' if labels else '{')+f'le="{bucket}"}}' if labels else f'{{le="{bucket}"}}'
     lines.append(f'{base}_bucket{suffix} {count}')
    lines.extend([f'{base}_count{labels} {h["count"]}',f'{base}_sum{labels} {h["sum"]}'])
   return '\n'.join(lines)+'\n'

telemetry=MetricsRegistry()

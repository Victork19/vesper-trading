from dataclasses import dataclass
@dataclass
class Edge: source:str;relation:str;target:str
class ExperienceGraph:
 def __init__(self,memory):self.memory=memory
 def link(self,source,relation,target):self.memory.put('WARM','edge_'+source+'_'+relation+'_'+target,{'source':source,'relation':relation,'target':target});self.memory.event('graph_edge',{'source':source,'relation':relation,'target':target})
 def edges(self):return [x for x in self.memory.all('WARM') if isinstance(x,dict) and {'source','relation','target'}<=set(x)]
 def related(self,node):return [e for e in self.edges() if e['source']==node or e['target']==node]

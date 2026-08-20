import json,os
class SecondarySignals:
    """LLMs may summarize evidence; they never determine final size."""
    def analyze(self,question,context):
        if os.getenv('GROQ_API_KEY'):
            try:
                from groq import Groq
                r=Groq(api_key=os.getenv('GROQ_API_KEY')).chat.completions.create(model=os.getenv('GROQ_MODEL','llama-3.3-70b-versatile'),temperature=.2,response_format={'type':'json_object'},messages=[{'role':'system','content':'Return JSON only with keys resolution_risk, missing_evidence, qualitative_signal. Never give a trade size.'},{'role':'user','content':json.dumps({'question':question,'context':context})}])
                return json.loads(r.choices[0].message.content)
            except Exception:return {'resolution_risk':'unknown','missing_evidence':['llm_unavailable']}
        return {'resolution_risk':'unknown','missing_evidence':['no_secondary_provider']}

import json
import urllib.request
from pathlib import Path

payload = json.loads(Path('app/payload.json').read_text())
req = urllib.request.Request(
    'http://127.0.0.1:8001/api/github/webhook',
    data=json.dumps(payload).encode(),
    headers={
        'Content-Type': 'application/json',
        'X-GitHub-Event': 'workflow_run',
    },
    method='POST',
)
with urllib.request.urlopen(req) as resp:
    print(resp.status)
    print(resp.read().decode())

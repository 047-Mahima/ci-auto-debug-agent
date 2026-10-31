import urllib.request

with urllib.request.urlopen('http://127.0.0.1:8001/api/github/failures') as resp:
    print(resp.status)
    print(resp.read().decode())

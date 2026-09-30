import sys, urllib.request

url = sys.argv[1]
req = urllib.request.Request(url, headers={
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
})
with urllib.request.urlopen(req, timeout=20) as r:
    print(r.read().decode('utf-8', errors='ignore'))

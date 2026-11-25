"""Parse a Netscape bookmark file with Linkding's own parser, via manage.py shell."""
import subprocess, sys, json, base64, os

path = sys.argv[1]
data = open(path,'rb').read()
b64 = base64.b64encode(data).decode()

script = f'''
import base64, json
from bookmarks.services.parser import parse

content = base64.b64decode("{b64}").decode()
bms = parse(content)
print("PARSED:", len(bms))
bad_date = [b for b in bms if not b.date_added or b.date_added == "0"]
print("no date:", len(bad_date))
no_tags = [b for b in bms if not b.tag_names]
print("no tags:", len(no_tags))
from collections import Counter
tc = Counter()
for b in bms:
    for t in b.tag_names:
        t = t.strip()
        if t: tc[t]+=1
print("tags:", dict(tc))
sample = bms[0]
print("sample url:", sample.href[:80])
print("sample title:", sample.title[:60])
print("sample date_added:", sample.date_added)
print("sample desc:", (sample.description or "")[:70])
urls = [b.href_normalized for b in bms]
print("distinct normalized:", len(set(urls)))
'''
open('/tmp/val.py','w').write(script)
subprocess.run(["scp","-q","/tmp/val.py","myriad-docker:/tmp/val.py"], check=True)
subprocess.run(["ssh","myriad-docker","docker cp /tmp/val.py linkding:/tmp/val.py"], check=True)
r = subprocess.run(["ssh","myriad-docker",
    'docker exec linkding sh -c "cd /etc/linkding && .venv/bin/python manage.py shell < /tmp/val.py"'],
    capture_output=True, text=True)
print(r.stdout)
if r.stderr: print("STDERR:", r.stderr[-500:], file=sys.stderr)

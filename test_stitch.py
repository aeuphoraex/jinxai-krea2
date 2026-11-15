import base64, json, sys, time, urllib.error, urllib.request

URL = "https://xzyorb--orbital.us-east.modal.run"
clips = []
for p in ("out/clip.mp4", "out/clip24.mp4"):
    clips.append(base64.b64encode(open(p, "rb").read()).decode())
print("clips:", [len(c) / 1e6 for c in clips], "MB b64")

req = urllib.request.Request(URL + "/api/video/stitch",
                             data=json.dumps({"clips": clips, "fps": 24}).encode(),
                             headers={"content-type": "application/json"})
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=900) as r:
        d = json.loads(r.read())
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read()[:800]); sys.exit(1)
except Exception as e:
    print("ERR", type(e).__name__, str(e)[:400]); sys.exit(1)
if d.get("error"):
    print("ERROR", d["error"]); sys.exit(1)
data = base64.b64decode(d["video"])
open("out/stitch.mp4", "wb").write(data)
print(f"OK clips={d['clips']} {d['width']}x{d['height']} {d['fps']}fps "
      f"{len(data)/1e6:.2f}MB head={data[4:12]!r} wall={time.time()-t0:.1f}s")

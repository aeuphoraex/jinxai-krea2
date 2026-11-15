import base64, json, sys, time, urllib.error, urllib.request
URL = "https://xzyorb--orbital.us-east.modal.run"
img = base64.b64encode(open("out/krea2_309210369_0.png", "rb").read()).decode()
payload = {"prompt": "the animal turns its head toward the camera, fur moving in the wind, shallow depth of field",
           "init_image": img, "model": "wan22-a14b", "frames": 33, "quality": 480,
           "steps": 12, "cfg": 5.0, "fps": 24, "seed": 31}
req = urllib.request.Request(URL + "/api/video", data=json.dumps(payload).encode(),
                             headers={"content-type": "application/json"})
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=2400) as r:
        d = json.loads(r.read())
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read()[:900]); sys.exit(1)
except Exception as e:
    print("ERR", type(e).__name__, str(e)[:500]); sys.exit(1)
if d.get("error"):
    print("ERROR", d["error"]); sys.exit(1)
open("out/a14b.mp4", "wb").write(base64.b64decode(d["video"]))
open("out/a14b_last.jpg", "wb").write(base64.b64decode(d["last_frame"]))
open("out/a14b_thumb.jpg", "wb").write(base64.b64decode(d["thumb"]))
print(f"OK {d['model']} {d['width']}x{d['height']} {d['frames']}f steps={d['steps']} "
      f"render={d['seconds']}s elapsed={d['elapsed']} wall={time.time()-t0:.1f}s")

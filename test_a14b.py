import base64, json, sys, time, urllib.error, urllib.request

URL = "https://xzyorb--orbital.us-east.modal.run"
img = base64.b64encode(open("out/krea2_309210369_0.png", "rb").read()).decode()
payload = {
    "prompt": "the animal turns its head toward the camera, fur shifting in the wind, "
              "shallow depth of field, handheld documentary camera",
    "init_image": img,
    "model": "wan22-a14b",
    "frames": 49,
    "quality": 480,
    "steps": 20,
    "cfg": 5.0,
    "fps": 24,
    "seed": 31,
}
req = urllib.request.Request(URL + "/api/video", data=json.dumps(payload).encode(),
                             headers={"content-type": "application/json"})
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=1800) as r:
        d = json.loads(r.read())
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read()[:900]); sys.exit(1)
except Exception as e:
    print("ERR", type(e).__name__, str(e)[:500]); sys.exit(1)
if d.get("error"):
    print("ERROR", d["error"]); sys.exit(1)
mp4 = base64.b64decode(d["video"])
open("out/a14b.mp4", "wb").write(mp4)
open("out/a14b_last.jpg", "wb").write(base64.b64decode(d["last_frame"]))
print(f"OK {d['model']} {d['width']}x{d['height']} {d['frames']}f @{d['fps']}fps "
      f"steps={d['steps']} render={d['seconds']}s elapsed={d['elapsed']} wall={time.time()-t0:.1f}s")
print(f"mp4={len(mp4)/1e6:.2f}MB head={mp4[4:12]!r} last_frame={'yes' if d.get('last_frame') else 'NO'}")

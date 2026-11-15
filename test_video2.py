import base64, json, sys, time, urllib.request, urllib.error

URL = "https://xzyorb--orbital.us-east.modal.run"
img = base64.b64encode(open("out/krea2_309210369_0.png", "rb").read()).decode()
payload = {
    "prompt": "the animal turns its head slowly toward the camera, fur shifting, shallow depth of field, handheld",
    "init_image": img, "frames": 81, "quality": 480,
    "steps": 24, "cfg": 5.0, "fps": 24, "seed": 99,
}
req = urllib.request.Request(URL + "/api/video", data=json.dumps(payload).encode(),
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
mp4 = base64.b64decode(d["video"])
open("out/clip24.mp4", "wb").write(mp4)
open("out/clip24_thumb.jpg", "wb").write(base64.b64decode(d["thumb"]))
print(f"OK {d['width']}x{d['height']} {d['frames']}f @{d['fps']}fps steps={d['steps']} "
      f"render={d['seconds']}s elapsed={d['elapsed']} wall={time.time()-t0:.1f}s")
print(f"mp4={len(mp4)/1e6:.2f}MB head={mp4[4:12]!r} model={d['model']}")

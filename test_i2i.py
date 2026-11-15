import base64, json, struct, time, urllib.request, urllib.error, glob, sys

URL = "https://xzyorb--orbital.us-east.modal.run"
src = (glob.glob("out/krea2_*.png") + glob.glob("out/p_2k.png") + glob.glob("out/preview_2k.jpg"))[0]
img = base64.b64encode(open(src, "rb").read()).decode()
payload = {
    "prompt": "the same subject, repainted as thick oil impasto with visible brush strokes, gallery lighting",
    "init_image": img, "strength": 0.55,
    "width": 1024, "height": 1024, "steps": 8, "cfg": 0,
    "variant": "uncensored", "text_encoder": "abliterated", "seed": 12345,
}
req = urllib.request.Request(URL + "/api/generate", data=json.dumps(payload).encode(),
                             headers={"content-type": "application/json"})
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.loads(r.read())
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read()[:400]); sys.exit(1)
except Exception as e:
    print("ERR", type(e).__name__, e); sys.exit(1)
if d.get("error"):
    print("ERROR", d["error"]); sys.exit(1)
im = d["images"][0]
b = base64.b64decode(im["data"])
w, h = struct.unpack(">II", b[16:24])
open("out/i2i.png", "wb").write(b)
print(f"OK {w}x{h} {len(b)/1e6:.2f}MB {im['seconds']}s mode={d['mode']} strength={d['strength']} elapsed={d['elapsed']} init={src}")

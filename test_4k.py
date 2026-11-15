import base64, json, struct, sys, time, urllib.error, urllib.request

URL = "https://xzyorb--orbital.us-east.modal.run"
payload = {
    "prompt": "a lighthouse on a granite headland at sunset, crashing surf, weathered stone texture, "
              "every rivet and lichen patch visible, 35mm film, natural light",
    "width": 2048, "height": 2048, "steps": 8, "cfg": 0,
    "num_images": 1, "seed": 4242, "upscale": 3840,
    "variant": "uncensored", "text_encoder": "abliterated",
}
req = urllib.request.Request(URL + "/api/generate", data=json.dumps(payload).encode(),
                             headers={"content-type": "application/json"})
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=1500) as r:
        d = json.loads(r.read())
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read()[:800]); sys.exit(1)
except Exception as e:
    print("ERR", type(e).__name__, str(e)[:400]); sys.exit(1)
if d.get("error"):
    print("ERROR", d["error"]); sys.exit(1)
im = d["images"][0]
b = base64.b64decode(im["data"])
w, h = struct.unpack(">II", b[16:24])
open("out/4k.png", "wb").write(b)
print(f"OK {w}x{h} {len(b)/1e6:.2f}MB render={im['seconds']}s elapsed={d['elapsed']} "
      f"upscale={d.get('upscale')} wall={time.time()-t0:.1f}s")

import base64, json, sys, time, urllib.error, urllib.request
URL = "https://xzyorb--orbital.us-east.modal.run"
name = sys.argv[1] if len(sys.argv) > 1 else "self_test"
scale = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
payload = {"prompt": "a lighthouse on a granite headland at sunset, weathered stone, 35mm film",
           "width": 1024, "height": 1024, "steps": 8, "cfg": 0, "num_images": 1,
           "seed": 777, "lora": False, "style_lora": name, "style_scale": scale,
           "variant": "uncensored", "text_encoder": "abliterated"}
req = urllib.request.Request(URL + "/api/generate", data=json.dumps(payload).encode(),
                             headers={"content-type": "application/json"})
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=900) as r:
        d = json.loads(r.read())
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read()[:600]); sys.exit(1)
except Exception as e:
    print("ERR", type(e).__name__, str(e)[:400]); sys.exit(1)
if d.get("error"):
    print("ERROR", d["error"]); sys.exit(1)
open(f"out/style_{name}.png", "wb").write(base64.b64decode(d["images"][0]["data"]))
print(f"OK {d['images'][0]['width']}x{d['images'][0]['height']} {d['images'][0]['seconds']}s "
      f"lora={d.get('style_lora')} scale={d.get('style_scale')} layers={d.get('style_adapters')} "
      f"wall={time.time()-t0:.1f}s")

import base64, json, sys, time, urllib.error, urllib.request
URL = "https://xzyorb--orbital.us-east.modal.run"
payload = {"prompt": "a lighthouse on a granite headland at sunset, weathered stone, 35mm film",
           "width": 1024, "height": 1024, "steps": 8, "cfg": 0,
           "num_images": 1, "seed": 777, "lora": True,
           "variant": "uncensored", "text_encoder": "abliterated"}
req = urllib.request.Request(URL + "/api/generate", data=json.dumps(payload).encode(),
                             headers={"content-type": "application/json"})
t0 = time.time()
with urllib.request.urlopen(req, timeout=600) as r:
    d = json.loads(r.read())
if d.get("error"):
    print("ERROR", d["error"]); sys.exit(1)
open("out/lora_img.png", "wb").write(base64.b64decode(d["images"][0]["data"]))
jobs = d.get("lora_jobs") or []
print(f"image {d['images'][0]['width']}x{d['images'][0]['height']} {d['images'][0]['seconds']}s "
      f"| lora jobs {len(jobs)} rank {d.get('lora_rank')} wall {time.time()-t0:.1f}s")
if not jobs:
    print("NO LORA JOB"); sys.exit(1)
job = jobs[0]["id"]
for i in range(90):
    time.sleep(4)
    with urllib.request.urlopen(f"{URL}/api/lora/{job}", timeout=60) as r:
        s = json.loads(r.read())
    if s.get("status") != "training":
        break
print("status", s.get("status"), {k: v for k, v in s.items() if k != "url"})
if s.get("status") == "ready":
    with urllib.request.urlopen(f"{URL}{s['url']}", timeout=120) as r:
        blob = r.read()
    open("out/lora_img.safetensors", "wb").write(blob)
    print(f"safetensors {len(blob)} bytes head={blob[:8]!r}")

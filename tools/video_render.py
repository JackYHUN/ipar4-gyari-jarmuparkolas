"""Render a labelled graphical replay of video_capture.py observer snapshots.

Authoring dependencies: Pillow and an ffmpeg executable (H.264/libx264).
Runtime dependencies of the yard application are unaffected. No interpolation is
performed: every drawn position/state is the last received observer snapshot.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

INK = "#173748"
TEAL = "#207985"
NAVY = "#213f56"
AMBER = "#b9843d"
BG = "#eef3f5"
MUTED = "#65808e"
STATE = {"waiting": "Indulásra vár", "requesting": "Parkolóhelyet kér", "driving": "Halad",
         "pedestrian_braking": "Fékez a zebra miatt", "pedestrian_stop": "Gyalogos miatt áll",
         "sensor_stop": "Érzékelőhiba miatt áll", "communication_stop": "Életjelre vár",
         "aligning": "Íven igazodik", "maneuvering": "Lassan beparkol", "parking_check": "Ellenőrzi a véghelyzetet",
         "parking_pending": "Nyugtára vár", "parking_reconciling": "Foglalást egyeztet",
         "parking_error": "Parkolási hiba", "parked": "Parkolás nyugtázva"}
COLORS = {"free": ("#edf7f1", "#80a98f"), "reserved": ("#e4effc", "#6492c7"), "occupied": ("#dfe7ec", "#8ca3b0")}
FONT = Path("C:/Windows/Fonts/arial.ttf")
BOLD = Path("C:/Windows/Fonts/arialbd.ttf")
_fonts = {}


def font(size, bold=False):
    return _fonts.setdefault((size, bold), ImageFont.truetype(str(BOLD if bold else FONT), size))


def text(draw, xy, value, size=18, color=INK, bold=False):
    draw.text(xy, str(value), font=font(size, bold), fill=color)


def wrap(draw, xy, value, max_width, size=18, color=INK, bold=False, lines=5):
    result, line = [], ""
    for word in str(value).split():
        candidate = (line + " " + word).strip()
        if line and draw.textlength(candidate, font=font(size, bold)) > max_width:
            result.append(line)
            line = word
        else:
            line = candidate
    if line:
        result.append(line)
    for index, content in enumerate(result[:lines]):
        text(draw, (xy[0], xy[1] + index * (size + 6)), content, size, color, bold)
    return (size + 6) * min(lines, len(result))


def panel(draw, box, fill="white", outline="#d7e2e7", radius=12):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=1)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def title_image(end=False):
    im = Image.new("RGB", (1280, 720), BG)
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 1280, 150), fill=NAVY)
    text(d, (65, 45), "IPAR 4.0  /  GYÁRI JÁRMŰPARKOLÁS", 32, "white", True)
    text(d, (65, 98), "Szimulált folyamat · tényleges TCP/MQTT adatút", 22, "#bcd9e5")
    panel(d, (50, 190, 1230, 610))
    if end:
        text(d, (80, 225), "A bemutatóban megfigyelt viselkedések", 30, INK, True)
        statements = ["Foglalás a központi szolgáltatás visszaigazolása után.",
                      "Gyalogos, érzékelőhiba és életjelkimaradás esetén megállás.",
                      "Telített parkoló mellett várakozás, felszabadítás után új foglalás.",
                      "Íves igazodás, ellenőrzött véghelyzet és parkolási nyugta."]
        for i, value in enumerate(statements):
            text(d, (85, 292 + i * 49), f"0{i+1}", 21, TEAL, True)
            text(d, (135, 291 + i * 49), value, 23)
        text(d, (80, 526), "A logok, a forráskód és a videó hash-e külön ellenőrzési jegyzékben.", 22, MUTED)
    else:
        text(d, (80, 225), "Gyártósortól a parkolóhelyig", 37, INK, True)
        wrap(d, (80, 292), "Ez a felvétel friss, valódi MQTT-futás megfigyelőjének mentett állapotait játssza vissza. A kirajzolt pozíciók között nincs interpoláció.", 1070, 25)
        wrap(d, (80, 405), "A grafika technikai rekonstrukció. A járművek és érzékelők szimuláltak; böngészős képernyő- és fizikai kamerafelvétel nem készült.", 1070, 23, MUTED)
        text(d, (80, 535), "Alaphelyzet → zebra → érzékelőhiba → életjelhiba → telített parkoló", 22, TEAL, True)
    text(d, (65, 654), "Oktatási modell · önálló beparkolás · idealizált érzékelés és hibamegállás", 20, MUTED)
    return im


def render(frame, elapsed, total):
    s = frame["state"]
    world = s["world"]
    cars = world.get("cars", [])
    chosen = cars[0] if frame["phase"].startswith(("6a", "6b")) else next(
        (c for c in cars if c["state"] not in ("waiting", "parked")), cars[0])
    im = Image.new("RGB", (1280, 720), BG)
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 1280, 100), fill=NAVY)
    text(d, (28, 15), frame["phase"], 25, "white", True)
    text(d, (29, 58), "MQTT-megfigyelő mentett állapotainak grafikus visszajátszása", 19, "#c0dbe6")
    panel(d, (22, 118, 870, 652))
    panel(d, (890, 118, 1258, 652))
    text(d, (42, 134), "GYÁRI UDVAR", 17, MUTED, True)
    text(d, (640, 134), f"Modellidő  {world['sim_time']:.1f} s", 18, INK, True)
    scale, ox, oy = 12.0, 46, 168

    def pos(x, y):
        return ox + x * scale, oy + y * scale

    for x in range(0, 65, 2):
        for y in range(0, 39, 2):
            px, py = pos(x, y)
            d.ellipse((px, py, px + 1, py + 1), fill="#dbe4e7")
    road = [pos(*p) for p in world["map"]["road"]]
    d.line(road, fill="#c3d0d8", width=78, joint="curve")
    for p in road:
        d.ellipse((p[0]-39,p[1]-39,p[0]+39,p[1]+39), fill="#c3d0d8")
    d.line(road, fill="#dfe7ec", width=71, joint="curve")
    for p in road:
        d.ellipse((p[0]-35,p[1]-35,p[0]+35,p[1]+35), fill="#dfe7ec")
    for a,b in zip(road, road[1:]):
        dist = math.dist(a,b)
        for k in range(0, int(dist), 19):
            p1 = (a[0]+(b[0]-a[0])*k/dist, a[1]+(b[1]-a[1])*k/dist)
            p2 = (a[0]+(b[0]-a[0])*min(k+9,dist)/dist, a[1]+(b[1]-a[1])*min(k+9,dist)/dist)
            d.line((p1,p2), fill="#a2b5c0", width=2)
    central = {p["id"]:p for p in (s.get("coordinator") or {}).get("slots", [])}
    for slot in world["slots"]:
        x,y = pos(slot["x"],slot["y"])
        status = central.get(slot["id"],slot)["state"]
        fill,stroke = COLORS[status]
        d.line((x-18,y+39,x-18,oy+14*scale,x+18,oy+14*scale,x+18,y+39), fill="#c5d1d7", width=2)
        panel(d,(x-25,y-37,x+25,y+37),fill,stroke,5)
        text(d,(x-20,y-63),slot["id"],15,MUTED,True)
        d.line((x-20,y+32,x+20,y+32), fill=stroke,width=3)
    gx,gy = pos(3,18)
    panel(d,(gx,gy,gx+137,gy+72),"#e1eaef","#c2d2dc",8)
    text(d,(gx+13,gy+23),"GYÁRTÓSOR",16,MUTED,True)
    text(d,pos(1,32.5),"INDULÁSI SOR",14,MUTED,True)
    text(d,pos(26,19.5),"RÖGZÍTETT ÚTVONAL",15,"#8299a6")
    crossing = world["crossing"]
    cross = crossing["active"]
    zx,zy = pos(crossing["x"], crossing["y"])
    half_length = crossing.get("half_length_m", 2.1) * scale
    half_width = crossing.get("half_width_m", 3.1) * scale
    d.rectangle((zx-half_length,zy-half_width,zx+half_length,zy+half_width),
                fill="#e3bb7d" if cross else "#a6bdc9")
    for stripe in range(7):
        y = zy - half_width + (stripe + 0.5) * 2 * half_width / 7
        d.rectangle((zx-half_length+4,y,zx+half_length-4,y+5),fill="white")
    stop_x = crossing.get("stop_x", 25)
    d.line((pos(stop_x,25.5),pos(stop_x,30.5)),fill=AMBER if cross else MUTED,width=4)
    text(d,(zx-37,zy-64),"ZEBRA",14,AMBER if cross else MUTED,True)
    route = chosen.get("route",[])
    if route:
        d.line([pos(*p) for p in route], fill="#72a3b7",width=2,joint="curve")
    if chosen.get("sensor",{}).get("valid") and chosen.get("sensor",{}).get("distance_to_stop_m") is not None:
        x,y=pos(chosen["x"],chosen["y"])
        radius = chosen["sensor"]["range_m"]*scale
        wedge = Image.new("RGBA",im.size,(0,0,0,0));wd=ImageDraw.Draw(wedge)
        wd.pieslice((x-radius,y-radius,x+radius,y+radius),-18,18,fill=(205,150,66,40) if chosen["sensor"]["pedestrian"] else (34,140,142,30))
        im=Image.alpha_composite(im.convert("RGBA"),wedge).convert("RGB");d=ImageDraw.Draw(im)

    def car_glyph(car, static=False):
        x,y=pos(car["x"],car["y"])
        angle=math.radians(car.get("heading",-90 if static else 0))
        color=NAVY if static or car["state"] in ("parked","waiting") else AMBER if car["state"].endswith("stop") else TEAL
        def polygon(length,width,fill):
            poly=[(x+a*math.cos(angle)-b*math.sin(angle),y+a*math.sin(angle)+b*math.cos(angle)) for a,b in [(-length,-width),(length,-width),(length,width),(-length,width)]]
            d.polygon(poly,fill=fill,outline="white")
        polygon(25,11,color);polygon(10,7,"#b8d6e0")
        label="Helyben" if static else car["id"]
        yoff=40 if static else 21 if car["state"]=="waiting" else 25
        w=d.textlength(label,font=font(14,True))
        d.rounded_rectangle((x-w/2-5,y+yoff-1,x+w/2+5,y+yoff+18),radius=4,fill="white")
        text(d,(x-w/2,y+yoff),label,14,color,True)

    for car in world.get("parked_cars",[]):
        car_glyph(car,True)
    for car in cars:
        car_glyph(car)
    if cross:
        d.ellipse((zx-7,zy-12,zx+7,zy+2),fill=NAVY,outline="white",width=2)
        d.line((zx,zy+1,zx,zy+18),fill=NAVY,width=4)
        d.line((zx-9,zy+9,zx,zy+4,zx+9,zy+9),fill=NAVY,width=3)
        d.line((zx-8,zy+27,zx,zy+17,zx+8,zy+27),fill=NAVY,width=3)
    for i,(state,label) in enumerate((("free","Szabad"),("reserved","Lefoglalt"),("occupied","Foglalt"))):
        lx=52+i*176
        panel(d,(lx,627,lx+13,640),COLORS[state][0],COLORS[state][1],2)
        text(d,(lx+22,624),label,15,MUTED)
    text(d,(910,137),"FOGADOTT ÁLLAPOT",16,MUTED,True)
    connected=s.get("connected")
    d.ellipse((911,172,923,184),fill=TEAL if connected else AMBER)
    text(d,(935,167),"Életjel rendben" if connected else "Nincs érvényes életjel",19,TEAL if connected else AMBER,True)
    text(d,(910,206),f"MQTT-üzenet  {s['mqtt']['received']}",18)
    metrics=world["metrics"]
    text(d,(910,242),f"Beparkolt    {metrics['completed']} / {metrics['total']}",24,INK,True)
    text(d,(910,279),f"Helyben álló    {metrics['preparked']}",18,MUTED)
    d.line((910,313,1237,313),fill="#d9e4e8",width=1)
    text(d,(910,330),f"{chosen['id']}  ·  {chosen['speed']:.1f} m/s",22,TEAL,True)
    wrap(d,(910,366),STATE.get(chosen["state"],chosen["state"]),321,19,AMBER if chosen["state"].endswith("stop") else INK,True,2)
    sensor=chosen["sensor"]
    sensor_label="Hibás érzékelőminta" if not sensor["valid"] else "Ezen a szakaszon nem aktív" if sensor["distance_to_stop_m"] is None else "Gyalogos észlelve" if sensor["pedestrian"] else "Nincs észlelt gyalogos"
    text(d,(910,421),"VIRTUÁLIS ÉRZÉKELŐ",14,MUTED,True)
    text(d,(910,448),sensor_label,18,AMBER if not sensor["valid"] or sensor["pedestrian"] else TEAL)
    text(d,(910,478),f"Hatótáv {sensor['range_m']:.0f} m · idealizált modell",16,MUTED)
    text(d,(910,516),"ÁLLÓ MODELLIDŐ / KIJELÖLT AUTÓ",14,MUTED,True)
    labels=(("pedestrian","Gyalogos"),("sensor_failure","Érzékelő"),("communication","Életjel"))
    for i,(key,label) in enumerate(labels):
        text(d,(910,546+28*i),label,17)
        text(d,(1170,546+28*i),f"{chosen['stop_time_s'][key]:.1f} s",17,AMBER,True)
    progress=min(1,elapsed/max(total,1))
    d.rectangle((22,666,1258,670),fill="#d6e3e9")
    d.rectangle((22,666,22+1236*progress,670),fill=TEAL)
    text(d,(25,684),f"Futás: {s['run_id']}  ·  Fogadott minták  ·  Visszajátszás {elapsed:.1f} s",15,MUTED)
    text(d,(835,684),"Virtuális érzékelő · fizikai kamera nincs",15,MUTED)
    return im


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--capture",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--ffmpeg",type=Path,required=True)
    args=parser.parse_args()
    evidence=args.capture.resolve()
    receipt=json.loads((evidence/"capture.json").read_text(encoding="utf-8"))
    assert receipt["status"]=="PASS"
    for name,digest in receipt["evidence_sha256"].items():
        assert sha(evidence/name)==digest, name
    frames=[json.loads(line) for line in (evidence/"observer-frames.jsonl").read_text(encoding="utf-8").splitlines()]
    times=[f["capture_elapsed_s"] for f in frames]
    assert times==sorted(times)
    duration=times[-1]
    fps=12
    args.output.parent.mkdir(parents=True,exist_ok=True)
    command=[str(args.ffmpeg),"-hide_banner","-loglevel","error","-y","-f","rawvideo","-vcodec","rawvideo","-s","1280x720","-pix_fmt","rgb24","-r",str(fps),"-i","-","-an","-c:v","libx264","-preset","fast","-crf","20","-pix_fmt","yuv420p","-movflags","+faststart",str(args.output)]
    process=subprocess.Popen(command,stdin=subprocess.PIPE)
    count=0
    def emit(im):
        nonlocal count
        process.stdin.write(im.tobytes());count+=1
    try:
        for _ in range(fps*6):emit(title_image())
        preview=None
        for i in range(math.ceil(duration*fps)+1):
            elapsed=min(i/fps,duration)
            frame=frames[max(0,bisect.bisect_right(times,elapsed)-1)]
            image=render(frame,elapsed,duration)
            emit(image)
            if preview is None and any(c["state"]=="pedestrian_stop" for c in frame["state"]["world"]["cars"]):
                image.save(args.output.with_suffix(".png"));preview=i
        for _ in range(fps*7):emit(title_image(True))
    finally:
        process.stdin.close()
    assert process.wait()==0
    decode=subprocess.run([str(args.ffmpeg),"-hide_banner","-loglevel","error","-i",str(args.output),"-f","null","-"],capture_output=True)
    assert decode.returncode==0,decode.stderr.decode(errors="replace")
    output={"status":"PASS","scope":"Labelled graphical replay of fresh actual MQTT observer snapshots; last snapshot held, no position interpolation; not physical camera/browser recording.",
            "capture_receipt":"capture.json","capture_receipt_sha256":sha(evidence/"capture.json"),
            "video_file":args.output.name,"video_sha256":sha(args.output),"size":[1280,720],"fps":fps,"frames":count,"duration_s":count/fps,
            "full_decode_check":"PASS","render_source_sha256":sha(Path(__file__)),"capture_duration_s":duration,"run_ids":receipt["run_ids"],
            "preview_file":args.output.with_suffix(".png").name,"preview_sha256":sha(args.output.with_suffix(".png")),
            "frame_selection":"For each video tick choose the last capture_elapsed_s <= tick. Initial/closing title cards are 6/7 seconds. Captured chronology is unaltered."}
    args.output.with_suffix(".json").write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(output,ensure_ascii=False))


if __name__=="__main__":main()

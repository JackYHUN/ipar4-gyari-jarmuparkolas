# Az aktuális rendszer bemutatóvideója

A videó valódi, helyi TCP/MQTT-futás megfigyelőjének mentett állapotait jeleníti meg. A folyamat és az érzékelő szimulált. A grafika utólagos rekonstrukció; nem böngészős képernyőfelvétel és nem fizikai kamera képe. A címkék ezt a videóban is kimondják. A kirajzolás a legutóbbi megfigyelt állapotot tartja, a pozíciók között nincs interpoláció.

## Rögzítés

A referencia-konfiguráció a projekt `config.json` fájlja. A szkript a szokásos Application példányt és annak három MQTT-kliensét indítja külön brokerporttal. A kezelői műveletek az `Application.command` sorába kerülnek; a rögzítő nem módosítja közvetlenül a világot, és a képeket kizárólag az `Application.state()` MQTT-megfigyelői gyorsítótárából készíti. HTTP-kéréseket ez a rögzítő nem ellenőriz; azokat az integrációs teszt ellenőrzi.

```powershell
python tools/video_capture.py --output results/video-demonstracio --mqtt-port 19180
```

A szkript két új futást készít. Az elsőben két már parkoló autó mellett végighalad az érkező flotta, közben a gyalogos, az érzékelőhiba és az életjelkimaradás hatása látható. A másodikban a teljes parkoló miatt helyre vár az érkező autó. Egy kézzel elhelyezett autó eltávolítása után felszabadul a hely, majd arra az érkező jármű kap foglalást és beparkol. A második futás szándékosan egy beparkolt érkezőnél végződik, a többi érkezőnek nincs szabad helye.

A 6a–6b jelenet ugyanazt a CAR01 járművet követi a kis sebességű igazodástól a véghelyzet ellenőrzésén át a koordinátori nyugtáig.

A felvétel nem statisztikai kísérlet. Funkcionális szemléltetésre szolgál; nem helyettesíti a külön reprodukálható teszt- és mérési eredményeket.

A `capture.json` tartalmazza a futásazonosítókat, konfigurációt, kezelői műveleteket, a rögzített állapotokat és a források/nyers MQTT-naplók SHA-256 lenyomatait. Az `observer-frames.jsonl` a megfigyelő mintáit őrzi. A `results/<futásazonosító>/mqtt.jsonl` az eredeti MQTT-napló.

## Videó és ellenőrző állóképek

A rendereléshez külön szerkesztői függőség a Pillow és egy H.264-képes FFmpeg. Ezek a futó parkolórendszerhez nem szükségesek. Az FFmpeg binárisa nem része a forráskódtárnak; saját elérési útját kell megadni.

A 2026. november 11-i videóhoz Pillow 13.0.0 és FFmpeg 7.0.2 (libx264) futott. A renderelő a Windows Fonts mappában keresi az Arial normál és félkövér betűfájlját.

```powershell
python -m pip install Pillow
python tools/video_render.py --capture results/video-demonstracio --output results/video-demonstracio/bemutato.mp4 --ffmpeg C:/Tools/ffmpeg/bin/ffmpeg.exe
python tools/video_stills.py --capture results/video-demonstracio
```

A renderelő ellenőrzi a bemeneti nyers fájlok hash-eit, majd az elkészült MP4 teljes dekódolását is lefuttatja. A videó melletti JSON-ban szerepel a kimeneti hash, időtartam, képméret, képsebesség és az ellenőrzés eredménye. A `stills/` képei fejezetenként azonos forrásból készülnek, külön ellenőrzési jegyzékkel.

A hálózati adatút helyben fut, a kliensek ugyanabban a Python-folyamatban működnek. A watchdog hiba az életjelet és a normál mozgási engedélyt érinti; a telemetria és a külön kezelői parkolóbeállítási adatút közben működhet. Az érzékelő- és kommunikációs hibamegállás idealizált azonnali nullázás, ezért a videó fizikai jármű biztonságosságát nem igazolja.

# Gyári járműparkolás – helyi demonstrátor

A kódtár a járműmodellt, a valódi helyi MQTT-adatutat, a műszerfalat és ezek automatizált ellenőrzését tartalmazza. A kézi parkolófoglalás már ott álló autókkal is vizsgálhatóvá teszi a szabad kapacitást.

Egy szimulált autó a gyártósor kijáratától egy központilag lefoglalt parkolóhelyre jut. Az útját zebra keresztezi: virtuális érzékelés alapján megáll, majd a gyalogos távozása után továbbhalad. A járműmodell, a parkolókezelő és a megjelenítést kiszolgáló megfigyelő valódi, helyi MQTT-közvetítőn keresztül kommunikál.

Az önálló alkalmazás célja az áttekinthető mérnöki feladat és a magyarázható működés. AI segítségével készült oktatási demonstrátor; a fájlok vagy a commitok megléte önmagában nem bizonyít önálló hallgatói munkát. A tényleges saját döntéseket és méréseket a [munkanaplóban](docs/SAJAT_MUNKA_NAPLO.md) lehet rögzíteni.

## Indítás Windows alatt

A mappában indított PowerShellből:

```powershell
powershell -ExecutionPolicy Bypass -File .\inditas.ps1
```

Az alkalmazás HTTP-portja **8766**, MQTT-portja **19085**. Önálló telepítéshez a saját `.venv` környezet létrehozása szükséges.

Új gépen, Python 3.12 telepítése után:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
powershell -ExecutionPolicy Bypass -File .\inditas.ps1
```

A [műszerfal](http://127.0.0.1:8766/) a szimulációt és az MQTT-n fogadott naplót mutatja. A szüneteltetett futás az **Indítás** gombbal folytatható. Az **Új futás** külön naplókönyvtárat hoz létre, és szüneteltetett kezdőállapotot ad.

## Már parkoló autók kézi elhelyezése

A térkép mellett található kezelővel egy szabad parkolóhelyre már leparkolt autó helyezhető. A rendszer közvetlenül a parkolóhelyre teszi; nem modellez hozzá behajtást vagy megtett utat. Ez a parkoló kezdeti, illetve kézzel megadott foglaltságának vizsgálatára szolgál.

A parkolókoordinátor az MQTT-n érkező elhelyezési kérésről a hely aktuális állapota alapján dönt. Foglalt vagy lefoglalt helyet nem ír felül. Az elfogadott autó a fogadott világállapotban és a térképen is megjelenik; a kezelő megvárja a visszaigazolást. Az elhelyezések az MQTT-naplóban visszakereshetők.

Ezek a helyben álló autók a `world.parked_cars` listában találhatók. A gyártósorról érkező autók továbbra is a `world.cars` listát alkotják: a teljesítési számláló a három érkező autót méri, a szabad helyek száma viszont mindkét csoport foglalását figyelembe veszi. Új futásban a kézzel elhelyezett autók is törlődnek. Ha minden hely elfogy, az érkező autó kiosztásra vár; az alapmodell nem szabadít fel helyeket automatikusan.

## A feladat határa

Az alapbeállítás három autó, nyolc parkolóhely, egy zebra és egy rögzített úthálózat. Egyszerre legfeljebb egy autó mozog; a többjárműves kereszteződési konfliktusok kezelése nem része ennek a változatnak. A várakozási területről a kijárathoz kerülés diszkrét átadás, nem modellezett sorban haladás; a megtett utat a kijárattól számolja a program. A leparkolt autó nem távozik, a parkolóhely foglalt marad. Kevés parkolóhelynél ezért maradhat várakozó autó.

A térkép helyi, 64 × 38 méteres koordinátarendszert használ. Nem egy tényleges Audi-telephely másolata. A szenzorjelek és a járműmozgás szimuláltak; nincs képfeldolgozás, valós kamera vagy valós önvezető jármű. A mozgás ideális útvonalkövetés egyszerű gyorsulással és fékezéssel. Kommunikációs és érzékelőhiba esetén a modell azonnal nullázza a sebességet; ez tudatos egyszerűsítés, nem valós fékezési modell.

## Működés és adatút

1. A jármű parkolóhelyet kér az MQTT-közvetítőn keresztül.
2. A koordinátor az első szabad helyet lefoglalja, és a kéréshez tartozó választ küld.
3. A jármű a kapott útvonalon halad, közben állapotot és virtuális szenzoradatot publikál.
4. A megfigyelő MQTT-üzenetekből építi fel a műszerfal állapotát, és naplózza a fogadott üzeneteket.
5. A parkolás eseménye alapján a koordinátor a helyet foglaltnak jelöli.

Az állapotgép fő állapotai: várakozás, helyigénylés, haladás, gyalogos miatti megállás, kommunikáció miatti megállás, érzékelőhiba miatti megállás és parkolás. A [UML-architektúra](docs/architecture.puml) és az [állapotgép](docs/state-machine.puml) szerkeszthető szöveges forrásként tartozik a projekthez.

## Mértékegységek és idő

| Jel | Jelentés | Egység / ütem |
| --- | --- | --- |
| `x`, `y`, `distance_m` | Hely és megtett út | m |
| `speed` | Járműsebesség | m/s |
| `heading` | Irány; kelet 0°, észak −90° | fok |
| `sim_time` | Eltelt modellidő | s |
| `stop_time_s` | Okonként összegzett álló modelllépések ideje | szimulációs s |
| Telemetria | Jármű- és szenzorminta | 10 Hz modellidőben |
| Szívverés | Koordinátori kapcsolat felügyeleti jele | 5 Hz valós időben |

Az 1×, 2× és 4× futási sebesség a modellidő ütemét változtatja. A kommunikációfelügyelet monoton valós órát használ; a két időalapot a kiértékelésben külön kell kezelni. A `received_utc` a fogadás falióra szerinti időbélyege. A szimulált méréshez nem tartozik kalibrált fizikai mérési bizonytalanság; a 0,1 másodperces modelllépés és az ideális geometria lényeges korlátozás.

## Hibahelyzetek és napló

A kezelőfelületről gyalogos jelenléte, virtuális érzékelőhiba és kommunikációs hibamód kapcsolható. A kommunikációs hibamód a szívverést elnémítja és a mozgó autók normál helyfoglalási parancsait eldobja; a telemetria a megfigyeléshez továbbra is elérhető. A már parkoló autók kézi elhelyezése külön kezelői menedzsmentút, amely ebben a hibamódban is működik. Ez irányított hibainjektálás, nem teljes broker- vagy hálózatleállítás.

A teljes fogadott MQTT-forgalom JSONL-fájlba kerül; a felületen csak a legutóbbi üzenetek és események látszanak. Az aktuális napló elérési útját a `/api/state` válasz `log_file` mezője jelzi. Exportok:

- `/api/export?kind=messages`: fogadott MQTT-üzenetek;
- `/api/export?kind=telemetry`: járműminták CSV-ben;
- `/api/export?kind=events`: események.

A járművek `stop_time_s` mezője külön gyűjti a gyalogos (`pedestrian`), az érzékelőhiba (`sensor_failure`) és a felügyelt szívverés hiánya (`communication`) miatti álló modellidőt. A CSV-ben ezek a `stop_pedestrian_s`, `stop_sensor_failure_s` és `stop_communication_s` oszlopok. A számlálók az adott autó adott futásában összegzettek, ezért a CSV soraiban szereplő értékeket nem szabad összeadni: autó és futás szerint az utolsó mintát vagy két minta különbségét kell használni. Régi, mező nélküli napló exportálásakor az új oszlopok üresek.

Csak az aktív autó teljesen álló modelllépése számít bele; a fékezés, a sorban állás, a helyigénylés és a parkolás utáni idő nem. A zebra miatti megállást elérő, még elmozduló lépés kimarad, így megállásonként legfeljebb egy lépésnyi részidő hiányozhat. Szüneteltetéskor nem fut modelllépés, új futásban a számlálók nulláról indulnak. Egyidejű okoknál a meglévő megállási prioritás érvényes: szívveréshiány, érzékelőhiba, gyalogos; egy lépés nem számolódik kétszer. Ezek modellmásodpercek, nem a számítógép előtt eltelt valós idő.

## Ellenőrzés és védés

Saját virtuális környezettel:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m tests.integration
.\.venv\Scripts\python.exe -m tests.verify_frontend
```

A második parancs valódi MQTT/HTTP integrációt ellenőriz. A harmadikhoz külön Node.js szükséges a PATH-on; a felületi JavaScriptet minimális DOM-mal ellenőrzi, nem valódi böngészővel. A modellteszt és a működő hálózati adatút bizonyítása külön feladat. A részletes ellenőrzési esetek az [ellenőrzési tervben](docs/ELLENORZESI_TERV.md), a magyarázat és a háromperces bemutató a [védési útmutatóban](docs/VEDESI_UTMUTATO.md) található. A tényleges teszteredményt az adott futás kimenete, illetve – ha létrejött – a `results/verification.json` rögzíti; ez a leírás önmagában nem sikerigazolás.

Ehhez a demonstrátorhoz nincs elvégzett statisztikai mérési kampány. Az egyedi tesztek nem helyettesítik a beadandóhoz szükséges kísérleti tervet és kiértékelést. Minden mérési eredményhez az itt futtatott forrásverziót és konfigurációt kell azonosítani.

A kézi parkolófoglalás használata és a 2026. október 22-i ellenőrzés bizonyítékai a [fejlesztési jegyzetben](docs/KEZI_PARKOLOFOGLALAS.md) találhatók.

A `results/` könyvtár futásnaplói, tesztbizonylatai és képernyőképei helyben keletkeznek, nem részei a követett forráscsomagnak. A dokumentáció ezek relatív kimeneti helyeit és korábbi ellenőrzések azonosítóit is tartalmazza; saját futtatáskor új bizonylat készül.

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

Ezek a helyben álló autók a `world.parked_cars` listában találhatók. A gyártósorról érkező autók továbbra is a `world.cars` listát alkotják: a teljesítési számláló a három érkező autót méri, a szabad helyek száma viszont mindkét csoport foglalását figyelembe veszi. Új futásban a kézzel elhelyezett autók is törlődnek. Ha minden hely elfogy, az érkező autó kiosztásra vár.

A **„Kézzel elhelyezett autó”** választóból egy ilyen háttérjármű külön is eltávolítható az **„Autó eltávolítása”** gombbal. A hely felszabadításáról is az MQTT-koordinátor dönt; a rajz csak visszaigazolt változás után módosul. Futó modellben a kapacitásra váró autó a következő helyigénylésével megkaphatja a felszabadult helyet, új futás nélkül. A gyártósorról érkező flotta lefoglalt helye és már leparkolt autója ezzel nem távolítható el.

Minden kézi elhelyezés saját `placement_id` azonosítót kap. Ez megkülönbözteti az ugyanarra a helyre később betett autót a korábbitól: régi eltávolítási kérés vagy megismételt korábbi visszaigazolás nem változtathatja meg az új foglaltságot. A működés és az ellenőrzési esetek a [kapacitásfelszabadítás leírásában](docs/KAPACITAS_FELSZABADITAS.md) szerepelnek.

## A feladat határa

Az alapbeállítás három autó, nyolc parkolóhely, egy zebra és egy rögzített úthálózat. Egyszerre legfeljebb egy autó mozog; a többjárműves kereszteződési konfliktusok kezelése nem része ennek a változatnak. A várakozási területről a kijárathoz kerülés diszkrét átadás, nem modellezett sorban haladás; a megtett utat a kijárattól számolja a program. A gyártósori flotta leparkolt autója nem távozik, a parkolóhelye foglalt marad. A kézi háttérjármű eltávolítása pillanatszerű foglaltságmódosítás, nem modellezett kihajtás. Kevés parkolóhelynél továbbra is maradhat várakozó autó.

A térkép helyi, 64 × 38 méteres koordinátarendszert használ. A jármű a megközelítési út végén megáll, majd 3 m sugarú íven és egyenes szakaszon, legfeljebb 1,2 m/s sebességgel parkol be. A pozíció, az irány, a kontúr és a sebesség fél modellmásodpercig legyen megfelelő. Kommunikációs és érzékelőhiba esetén a szimulált védelmi megállás azonnali.

## Működés és adatút

1. A jármű parkolóhelyet kér az MQTT-közvetítőn keresztül.
2. A koordinátor az első szabad helyet lefoglalja, és a kéréshez tartozó választ küld.
3. A jármű a kapott útvonalon halad, közben állapotot és virtuális szenzoradatot publikál.
4. A megfigyelő MQTT-üzenetekből építi fel a műszerfal állapotát, és naplózza a fogadott üzeneteket.
5. Az érvényes véghelyzetről a jármű végjelentést küld. A koordinátor ellenőrzi a foglalást és a pózt, majd nyugtázza a befejezést. Elveszett jelentés vagy nyugta esetén az eredeti foglalás azonosítójával történik az ismétlés.

A fő állapotok a helyigénylés, a megközelítés, az íves igazodás, a lassú beállás, a véghelyzetmérés, a nyugtára várás és a nyugtázott parkolás. Tizenkét gyors jelentés után öt modellmásodpercenként folytatódik az egyeztetés ugyanazzal a foglalással. A már igazolt véghelyzet nyugtája szüneteltetett futásban is azonnal lezárja a műveletet. A [UML-architektúra](docs/architecture.puml) és az [állapotgép](docs/state-machine.puml) szerkeszthető szöveges forrás.

## Mértékegységek és idő

| Jel | Jelentés | Egység / ütem |
| --- | --- | --- |
| `x`, `y`, `distance_m` | Hely és megtett út | m |
| `speed` | Járműsebesség | m/s |
| `heading` | Irány; kelet 0°, észak −90° | fok |
| `sim_time` | Eltelt modellidő | s |
| `stop_time_s` | Okonként összegzett álló modelllépések ideje | szimulációs s |
| Telemetria | Jármű- és szenzorminta | 1 / dt Hz modellidőben, alapértéken 10 Hz |
| Szívverés | Koordinátori kapcsolat felügyeleti jele | 5 Hz valós időben |

Az 1×, 2× és 4× futási sebesség a modellidő ütemét változtatja. A kommunikációfelügyelet monoton valós órát használ; a két időalapot a kiértékelésben külön kell kezelni. A `received_utc` a fogadás falióra szerinti időbélyege. A szimulált méréshez nem tartozik kalibrált fizikai mérési bizonytalanság; a 0,1 másodperces modelllépés és az ideális geometria lényeges korlátozás.

A támogatott lépésköz 0,025–0,1 s, az életjel határideje 0,6–60 s. A konfigurált érzékelőtávnak fedeznie kell a stopvonal távolságát, a lépésnyi reakcióutat és a fékutat. Az ismeretlen mezők és a megengedett tartományon kívüli értékek indítási hibát okoznak. A zebra figyelése a jármű hátsó élének áthaladásáig tart. A túl későn érkező jel külön esemény. Az állapotválasz `clock` mezője tartalmazza a kihagyott modellidőt és a modellidő/valós idő arányát. A futás órakivonata új futás indításakor és szabályos leállításkor `clock.json` néven mentődik.

## Hibahelyzetek és napló

A kezelőfelületről gyalogos jelenléte, virtuális érzékelőhiba és kommunikációs hibamód kapcsolható. A kommunikációs hibamód szünetelteti a szívverés publikálását, a járműoldalon pedig figyelmen kívül hagyja a normál kiosztási parancsokat; a telemetria a megfigyeléshez továbbra is elérhető. A már parkoló autók kézi elhelyezése külön kezelői menedzsmentút, amely ebben a hibamódban is működik. Ez irányított hibainjektálás, nem teljes broker- vagy hálózatleállítás.

A teljes fogadott MQTT-forgalom JSONL-fájlba kerül; a felületen csak a legutóbbi üzenetek és események látszanak. Az aktuális napló elérési útját a `/api/state` válasz `log_file` mezője jelzi. Exportok:

- `/api/export?kind=messages`: fogadott MQTT-üzenetek;
- `/api/export?kind=telemetry`: járműminták CSV-ben;
- `/api/export?kind=events`: események.

Az export a naplót darabonként olvassa. A CSV képletszerű szövegmezőit szövegként jelöli, a nyers érték a JSONL-ben kereshető vissza. Befejezett flottánál ritkított világállapot és életjel marad a rendszerben. A HTTP-kiszolgáló csak a tényleges helyi portra címzett loopback kéréseket fogadja el. Elavult MQTT-adatnál a vezérlés 503-as választ kap, a felület az utolsó ismert állapotot jelzi. A helyi broker előfizetése névtelen. A futtató a szerepkörhöz és a témához kötött üzeneteket futásonkénti HMAC-kulccsal hitelesíti, és a minták korát a hitelesített küldési idő alapján vizsgálja.

A gyalogos- és érzékelőkapcsoló eseménye modelllépés nélkül is kimegy. MQTT-kieséskor az esemény sorban marad, és a megfigyelő általi naplózásig újrapróbálkozik. Ha szabályos leállításkor vagy futásváltáskor az esemény még nem érkezett meg, az előző futás naplója helyi `operator/control_event` rekordot őriz. A vezérlőkérés törzsére három másodperces határidő van, és a helyi HTTP-kiszolgáló legfeljebb 16 aktív kapcsolatot dolgoz fel egyszerre.

Nagy JSONL-naplóhoz futtatható a `python tools/log_segments.py results/<futás>/mqtt.jsonl --output results/szakaszok --max-mib 16` parancs. A lezárt sorokat eredeti sorrendben, teljes rekordokkal menti, majd visszaolvasással ellenőrzi a teljes adatsor lenyomatát.

A kézi eltávolítás `parked_removed` eseményt ad a hely, a kérés és az elhelyezés azonosítójával. A kezelőfelület az egyező nyugta és a két egyező állapotminta után tekinti késznek a műveletet. A kezelőparancsok az aktuális futáshoz kötöttek; egy régi böngészőlap vagy a nullázás előtt sorba állított művelet nem módosíthatja az új futást.

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

Playwright és Chromium mellett a `node tests/browser.cjs` valódi böngészős próbát indít. A `MINI_YARD_PYTHON` változóban megadható a virtuális környezet Pythonja. A teszt ideiglenes mappában fut saját HTTP- és MQTT-porttal.

A [mérési módszer](docs/MERESI_MODSZER.md) a kézi foglaltságkezelés MQTT-válaszidejét hasonlítja össze szüneteltetett és 4× sebességű futás mellett. A javított változat 30 alkalmazáspárjának eredménye a `results/mqtt-campaign-2026-11-12-r3/summary.json` fájlban van. A külön parkolási kampány azonos célhelyen hasonlítja össze a zavarmentes és gyalogosjel melletti modellidőt. Az átmeneti szenzorhibát és a telt parkolót külön esetként vizsgálja.

Reprodukálás és a meglévő bizonyíték újraellenőrzése:

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m experiments.latency_campaign
.\.venv\Scripts\python.exe -B -X utf8 -m experiments.verify_campaign results/mqtt-campaign-2026-11-12-r3
.\.venv\Scripts\python.exe -B -X utf8 -m experiments.parking_campaign --output results/parkolas-sajat
```

Az új kampány saját könyvtárat és bizonylatot hoz létre. Korábbi forrásverzióhoz tartozó MQTT-kampánynál a `--historical` kapcsoló engedi a mért adatok újraellenőrzését, és jelzi a forráseltérést. A [videó útmutatója](tools/VIDEO_UTMUTATO.md) a valódi MQTT-állapotokból készülő grafikus bemutató újragenerálását is leírja.

A kézi parkolófoglalás használata és a 2026. október 22-i ellenőrzés bizonyítékai a [fejlesztési jegyzetben](docs/KEZI_PARKOLOFOGLALAS.md) találhatók.

A `results/` könyvtár futásnaplói, tesztbizonylatai és képernyőképei helyben keletkeznek, nem részei a követett forráscsomagnak. A dokumentáció ezek relatív kimeneti helyeit és korábbi ellenőrzések azonosítóit is tartalmazza; saját futtatáskor új bizonylat készül.

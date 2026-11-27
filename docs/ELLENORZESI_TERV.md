# Ellenőrzési terv

A terv az elvárt viselkedést rögzíti. A végrehajtás és az eredmény a tesztkimenetből, a futásazonosítóval ellátott naplóból és a `results/verification.json` fájlból állapítható meg. Az alábbi követelménytábla önmagában nem sikerigazolás; a ténylegesen lefuttatott ellenőrzést külön bizonylat azonosítja.

## Követelmény → megvalósítás → bizonyíték

| Azonosító | Követelmény | Megvalósítási elv | Ellenőrzés / bizonyíték |
| --- | --- | --- | --- |
| K1 | Egy helyhez ne tartozzon két aktív foglalás. | Egy központi koordinátor kezeli a helyek állapotát. | Több kérés után egyedi `slot_id` értékek a foglalásokban; koordinátor-modellteszt és MQTT-napló. |
| K2 | Ismételt kérés ne foglaljon új helyet. | Kérésazonosítóhoz tartozó foglalás újbóli felhasználása. | Azonos kérés ismétlésekor azonos foglalás, változatlan foglalásszám. |
| K3 | Időben érkező gyalogosjelnél a jármű középpontja a megállási vonalon vagy előtte álljon meg. | Az érzékelőtáv fedezi a megállási vonalig tartó távolságot, egy lépésnyi reakcióutat és a fékutat. A késői jel külön eseményt kap. | Korai és késői jelzés, határon belüli lassulás, stopvonal és diagnosztikai esemény ellenőrzése. |
| K4 | A gyalogos távozásával az autó továbbhaladhasson. | A megállási feltétel megszűnése után visszatérés haladásba. | Állapotváltás és későbbi parkolás; esemény- és telemetriaexport. |
| K5 | Hiányzó szívverés megállási ok legyen. | Valós órás watchdog; alap küszöb 1,0 s. | Szívverés elnémítása, kor és `communication_stop` megfigyelése; valós idő szerinti ellenőrzés. |
| K6 | Érvénytelen érzékelőadat mellett ne folytatódjon a szokásos haladás. | Virtuális érzékelőhiba és külön megállási állapot. | `sensor.valid`, állapot és sebesség ellenőrzése hibainjektáláskor és helyreállításkor. |
| K7 | Helyhiány ne eredményezzen kettős kiosztást. | Szabad hely nélkül várakozó válasz. | Az autókénál kevesebb hely; a fennmaradó autó vár, az elfoglalt hely nem szabadul fel önmagától. |
| K8 | A műszerfal világ- és koordinátorállapota hálózati megfigyelésből származzon. | Külön MQTT-megfigyelő kliens szolgálja ki a HTTP-állapotot. | Valódi brokerrel végzett integráció, fogadási számláló és napló; az UI-adat és üzenet összevetése. |
| K9 | A kísérlet állapota utólag visszakereshető legyen. | Teljes fogadott MQTT JSONL-napló; CSV- és eseményexport. | Az export megnyitható, futás- és autóazonosítók, idő és állapot a várt mezőkben található. |
| K10 | Az álló modellidő okonként visszakereshető legyen, kettős elszámolás nélkül. | Autónként összegzett `stop_time_s`; csak teljesen álló, aktív megállási lépések. | Modellteszt eltérő lépésközzel és egyidejű hibákkal; MQTT-n fogadott számláló és CSV-export összevetése. |
| K11 | Már parkoló autó csak szabad helyre kerülhessen, és a mozgó autó ne kaphassa meg ugyanezt a helyet. | Központi MQTT-elhelyezési kérés és visszaigazolás; külön `parked_cars` lista. | Szünetben történő elhelyezés, ismételt kérés, foglalt és lefoglalt hely elutasítása; utána induló autó másik helyre kerül. |
| K12 | A háttérjármű ne növelje a feladatteljesítési mutatókat. | A `completed` és `total` csak a gyártósorról érkező `cars` listát méri. | Kézi elhelyezés után a foglaltság nő, a teljesítés nem; új futás nullázza a háttérjárműveket is. |
| K13 | Kézi háttérjármű eltávolításával új futás nélkül lehessen kapacitást adni a várakozó autónak. | Eltávolítási kérés és válasz a meglévő MQTT-menedzsmentúton; a flotta újrakéri a helyet. | Teljes kézi foglaltság mellett CAR01 vár; P03 felszabadítása után változatlan futásazonosítóval P03-ra indul. |
| K14 | Régi elhelyezési vagy eltávolítási üzenet ne módosíthasson egy új háttérjárművet. | Elhelyezésenként egyedi `placement_id`; végrehajtott kérések ismétlése mellékhatás nélkül. | Elhelyezés → eltávolítás → új elhelyezés után régi kérés és visszaigazolás ismétlése; az új autó változatlan marad. |
| K15 | Előző futás kezelőparancsa ne változtathassa meg az új futást. | `run_id` ellenőrzése fogadáskor és a sorba tett parancs alkalmazásakor. | Régi futásazonosító minden műveletnél elutasítva; reset előtt várakozó parancs nem szivárog át az új futásba. |
| K16 | A manőver legyen önálló, és csak ellenőrzött helyzetből legyen nyugtázható. | Kis sebességű ív és egyenes beállás, pozíció-, irány-, kontúr- és stabilitási feltétel. | Négy hibás póz, fázis közbeni érzékelőhiba és névleges manőver. |
| K17 | Elveszett parkolási vagy foglaltsági nyugta helyreállítható legyen. | Tizenkét gyors jelentés után ritkított egyeztetés ugyanazzal a foglalással. Kézi foglaltságnál a nyugta és a két egyező állapotminta együtt igazolja a lezárást. | Kimaradó végjelentés, tizenkét elveszett nyugta után folytatódó flotta, szünetben érkező nyugta és egymástól független kézi műveletek. |
| K18 | Elavult adat és üzemképtelen vezérlés legyen látható. | A frissesség a klienskapcsolatok és a világ, illetve a koordinátor utolsó mintája alapján dől el. | Broker leállítása, HTTP 503 és Chromiumban elavult állapotjelzés. |
| K19 | A kezelők keskeny nézetben és billentyűzettel is elérhetők legyenek. | Tördelődő fázissor, elnevezett térképgombok és futásváltáskor a táblaszűrőre helyezett fókusz. | Chromium 320, 390 és 1280 képponton, billentyűzetes kijelölés és kétlapos futásváltás. |
| K20 | Az alkalmazás állapotát hiteles futásbeli adatok vezéreljék. | Témához és futáshoz kötött HMAC, szigorú adatmezők, ellenőrzött sorszám és küldési idő. | Aláírás nélküli minta nem akadályozza a későbbi érvényes világ- vagy koordinátori adat feldolgozását. |

## Ellenőrzési szintek

**Modellteszt:** broker nélkül vizsgálja a helyfoglalás és a mozgási szabályok logikáját. Gyorsan megismételhető, de nem igazol hálózati kommunikációt.

**MQTT/HTTP integráció:** valódi helyi brokeren és klienskapcsolatokon ellenőrzi az adatút és az API együttműködését. Ennek eredménye külön rögzítendő.

**Böngészős bemutató:** az állapotok, kezelőszervek és exportok értelmezhetőségét ellenőrzi. A szemrevételezés nem helyettesíti az állítások automatizált ellenőrzését.

Saját virtuális környezettel futtatandó parancsok:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m tests.integration
```

## A hibainjektálás pontos jelentése

A kommunikációs hibamód szünetelteti a szívverés publikálását, és a járműoldalon figyelmen kívül hagyja a normál kiosztási parancsokat. A koordinátor és a megfigyelő ettől még működik; a parancs az observer naplójában megjelenhet. A telemetria és a kezelői parkolóbeállítás külön menedzsmentútja továbbra is halad. Ezért az így végrehajtott teszt a szívverés-felügyelet és a parancshiány kezeléséről ad bizonyítékot; nem állítható belőle teljes hálózatkieséssel szembeni viselkedés.

A gyalogosészlelés a szimulált környezetből képzett geometriai jel. A stop-vonal átlépésének megakadályozása a diszkrét modellben előírt korlátot is használ. Egy sikeres vizsgálat nem bizonyít valós autós fékút- vagy ütközésbiztonságot.

Kommunikációs és érzékelőhibánál a modell azonnal nullázza a sebességet. A teszt ezt a modellbeli szabályt ellenőrzi, nem a fizikai fékezés idő- vagy útszükségletét. A szívverés-felügyelet szüneteltetéskor tovább működik, de az autó állapotváltása modelllépéshez kötött; a mozgási reakciót futó modellben kell ellenőrizni.

## Eredménylap minden vizsgálathoz

| Mező | Kitöltendő tartalom |
| --- | --- |
| Teszt azonosítója és tényleges időpontja | |
| Forrásverzió és konfiguráció | |
| Futásazonosító | |
| Kezdeti állapot és beavatkozás | |
| Elvárt eredmény | |
| Megfigyelt eredmény és bizonyíték elérési útja | |
| Megfelelt / nem felelt meg / nem hajtottuk végre | |
| Korlátozás vagy eltérés | |

## Korábbi ellenőrzési eredmény

A 2026. október 29-i különálló környezetben 36 egységteszt (29 modell, 4 broker, 3 méréskiértékelési teszt), 11 valódi MQTT/HTTP-integrációs eset 27 mért feltétellel és 31 felületi szerződésellenőrzés sikeres. A runtime-, teszt- és webforrásokat a `results/verification.json` azonosítja; a nyers MQTT-naplók az ott megadott evidence_directory alatt találhatók. A korábbi dátumú vizsgálatok történeti bizonyítékok, nem a mostani kód sikerigazolásai.

A teljes állóidő, a kézi foglalás és felszabadítás, az ismételt kérések, a régi járműpéldány és az előző futás parancsainak kizárása is a tesztelt viselkedés része. A broker lezárásánál a háttérszál megszűnését és a listener bezárását is ellenőrzi az integráció. A JavaScript minimális DOM-tesztje külön Node.js futtatókörnyezetet igényel, és nem teljes böngészős teszt.

## A beadási kiadás ellenőrzése

A 2026. november 23-i ellenőrzött kiadás forrása f00f9f4c49c4a41819f22b6a9afbc88d04c25e6a. A helyi ellenőrzési eredmény 45 Python-egységteszt, 21 MQTT/HTTP-integrációs próba (38 feltétel), 32 felületi szerződésellenőrzés. A Chromium-próba három szélességet vizsgált. A beadási ZIP-ben a forras/results/verification.json és forras/results/ui/design-v2/frontend-check.json a részletes, forráslenyomattal ellátott bizonylat. Az új futás eredményét külön kell rögzíteni; a fenti adat nem új futás állítása.

## Kísérleti kiértékelés

A külön [mérési terv](MERESI_MODSZER.md) szüneteltetett és négyszeres sebességgel futó modell mellett vizsgálja a valódi MQTT-kezelőút válaszidejét. A november 26-i fő kampány 30 futáspárban 120 mért és 120 bemelegítő műveletet tartalmaz. A hatpáros kiegészítő kampány 24 mért műveletet rögzít; a november 12-i 720 mért művelet külön történeti bizonyíték. A statisztikai egység a futáspár, a különböző módszerű kampányok mintái nem vonhatók össze. A meglévő bizonyítékot az `experiments.verify_campaign` újraszámolja. Ez statisztikai időmérés a helyi futtatási környezetben, nem fizikai autó validálása.

Kapacitásvizsgálatnál a befejezett és várakozó autókat külön kell közölni. Az azonos determinisztikus mozgás ismétlése önmagában nem hoz független folyamatmintát. A funkcionális teszteket és a statisztikai mérést ezért külön bizonylatok támasztják alá.

## November 26-i regresszióellenőrzés

A QoS-szétválasztás és a megfigyelő állapotpillanatképének optimalizálása után 46/46 Python-teszt és 21/21 valódi MQTT/HTTP-integrációs próba (38 feltétel) sikerült. A nyugtázási terhelés a korábbi wildcard-feliratkozásnál időtúllépést okozott; a javítás nem változtatja meg a parkolási mozgásmodellt. A korábbi késleltetésmérés külön forrásverzióhoz tartozik, az ellenőrzés --historical módban újraszámolható. A javítás és az automatizált ellenőrzés AI-támogatással történt.

Az új mérési módszerváltozat 30 teljes párja és 120 mért művelete ellenőrzött; a részletes, előre rögzített tervet és nyers rekordokat a beadási csomag tartalmazza. A régi 720 művelet nem lett az új mintába összevonva. A 5d2e024 kiadás tiszta Windows GitHub Actions ellenőrzése sikeres: https://github.com/JackYHUN/ipar4-gyari-jarmuparkolas/actions/runs/45902454101 .

## A mérési bizonyítékok szigorított ellenőrzése

A dc92781 kiadásban két új regressziós teszt ellenőrzi a hiányos forrásjegyzék és a hiányzó vagy későn érkező végponti állapotminta elutasítását. Mind a 48 Python-teszt sikeres, a GitHub Actions ellenőrzése is sikeres (45906671291). A fő, a kiegészítő és a történeti kampány 240, 48, illetve 840 műveletének végponti állapota a nyers MQTT-mintákból rekonstruálható. A történeti kampány eltérő forrását az ellenőrző továbbra is jelzi. A szigorítás nem változtatja meg a mérési adatokat vagy a futtatható demonstrátort.

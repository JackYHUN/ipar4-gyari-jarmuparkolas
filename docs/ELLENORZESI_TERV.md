# Ellenőrzési terv

A terv az elvárt viselkedést rögzíti. A végrehajtás és az eredmény a tesztkimenetből, a futásazonosítóval ellátott naplóból és a `results/verification.json` fájlból állapítható meg. Az alábbi követelménytábla önmagában nem sikerigazolás; a ténylegesen lefuttatott ellenőrzést külön bizonylat azonosítja.

## Követelmény → megvalósítás → bizonyíték

| Azonosító | Követelmény | Megvalósítási elv | Ellenőrzés / bizonyíték |
| --- | --- | --- | --- |
| K1 | Egy helyhez ne tartozzon két aktív foglalás. | Egy központi koordinátor kezeli a helyek állapotát. | Több kérés után egyedi `slot_id` értékek a foglalásokban; koordinátor-modellteszt és MQTT-napló. |
| K2 | Ismételt kérés ne foglaljon új helyet. | Kérésazonosítóhoz tartozó foglalás újbóli felhasználása. | Azonos kérés ismétlésekor azonos foglalás, változatlan foglalásszám. |
| K3 | Aktív gyalogosnál az autó ne lépje át a stop-vonalat. | Virtuális érzékelés, fékezés és diszkrét stop-vonal-korlát. | Zebra előtti haladás közben aktivált gyalogos; pozíció és sebesség összevetése a határral. |
| K4 | A gyalogos távozásával az autó továbbhaladhasson. | A megállási feltétel megszűnése után visszatérés haladásba. | Állapotváltás és későbbi parkolás; esemény- és telemetriaexport. |
| K5 | Hiányzó szívverés megállási ok legyen. | Valós órás watchdog; alap küszöb 1,0 s. | Szívverés elnémítása, kor és `communication_stop` megfigyelése; valós idő szerinti ellenőrzés. |
| K6 | Érvénytelen érzékelőadat mellett ne folytatódjon a szokásos haladás. | Virtuális érzékelőhiba és külön megállási állapot. | `sensor.valid`, állapot és sebesség ellenőrzése hibainjektáláskor és helyreállításkor. |
| K7 | Helyhiány ne eredményezzen kettős kiosztást. | Szabad hely nélkül várakozó válasz. | Az autókénál kevesebb hely; a fennmaradó autó vár, az elfoglalt hely nem szabadul fel önmagától. |
| K8 | A műszerfal hálózaton fogadott állapotot mutasson. | Külön MQTT-megfigyelő kliens szolgálja ki a HTTP-állapotot. | Valódi brokerrel végzett integráció, fogadási számláló és napló; az UI-adat és üzenet összevetése. |
| K9 | A kísérlet állapota utólag visszakereshető legyen. | Teljes fogadott MQTT JSONL-napló; CSV- és eseményexport. | Az export megnyitható, futás- és autóazonosítók, idő és állapot a várt mezőkben található. |
| K10 | Az álló modellidő okonként visszakereshető legyen, kettős elszámolás nélkül. | Autónként összegzett `stop_time_s`; csak teljesen álló, aktív megállási lépések. | Modellteszt eltérő lépésközzel és egyidejű hibákkal; MQTT-n fogadott számláló és CSV-export összevetése. |
| K11 | Már parkoló autó csak szabad helyre kerülhessen, és a mozgó autó ne kaphassa meg ugyanezt a helyet. | Központi MQTT-elhelyezési kérés és visszaigazolás; külön `parked_cars` lista. | Szünetben történő elhelyezés, ismételt kérés, foglalt és lefoglalt hely elutasítása; utána induló autó másik helyre kerül. |
| K12 | A háttérjármű ne növelje a feladatteljesítési mutatókat. | A `completed` és `total` csak a gyártósorról érkező `cars` listát méri. | Kézi elhelyezés után a foglaltság nő, a teljesítés nem; új futás nullázza a háttérjárműveket is. |

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

A kommunikációs hibamód elnémítja a szolgáltatás szívverését és eldobja a mozgó autók normál helyfoglalási parancsait. A telemetria és a kezelői parkolóbeállítás külön menedzsmentútja továbbra is halad. Ezért az így végrehajtott teszt a szívverés-felügyelet és a parancshiány kezeléséről ad bizonyítékot; nem állítható belőle teljes hálózatkieséssel szembeni viselkedés.

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

## Még hiányzó kísérleti kiértékelés

A kis változathoz nincs elvégzett statisztikai kampány. A determinisztikus alapfutás többszöri megismétlése nem hoz létre több független folyamatmintát. A következő lépés egy előre megadott paramétervizsgálat és – ha statisztikai következtetést szeretnénk – értelmes bizonytalansági vagy véletlen modell, mintavételi egység és ismétlési terv kialakítása.

Kapacitásvizsgálatnál külön kell közölni a befejezett és várakozó autók számát. Mivel a parkoló autók nem távoznak, az autókénál kisebb kapacitásnál nem helyes csak a sikeresen leparkolt járművek átlagából az egész rendszer teljesítményére következtetni. A kiértékelés az azonosított forrásverzió saját mérési adataira vonatkozik.

## Végrehajtási bizonylat: 2026. október 19.

A megőrzött `results/integration/20261019T200424456122Z/verification.json` szerint a 20:04:24–20:04:58 UTC között végrehajtott valódi helyi MQTT/HTTP integráció öt tesztesete sikeres volt; összesen 12 mért feltételt rögzített. A sikeres teljes alapfutás megfigyelt állapotának modellideje 110,3 másodperc volt, ekkor mindhárom autó külön parkolóhelyen állt. Ez egy funkcionális integrációs futás eredménye, nem statisztikai becslés.

A bizonylat tartalmazza a tényleges futásazonosítókat, a mért adatokat, valamint az ellenőrzés előtt és után változatlan forrásfájlok SHA-256 lenyomatait. A hozzá tartozó nyers naplók és futáskonfigurációk a `results/integration/20261019T200424456122Z/runs` mappában találhatók. Későbbi forrásmódosítás után ezt az eredményt történeti ellenőrzésként kell kezelni, vagy a tesztet újra kell futtatni.

## Az okonkénti álló modellidő ellenőrzése

A mérés bevezetésekor négy új eset 14-re növelte a modelltesztek számát. Az időmérés tesztjei eltérő lépésközt, egyszerre jelentkező hibákat, a stopvonalhoz még elmozduló lépés kizárását és az új futás nullázását is vizsgálják. A mérőszám pontos definíciója és a kumulatív CSV-értékek értelmezése a README „Hibahelyzetek és napló” részében szerepel.

A mérés bevezetése utáni MQTT/HTTP-integráció megőrzött bizonylata a `results/integration/20261020T220119075637Z/verification.json`. Öt tesztesetben 15 mért feltételt rögzít: a gyalogos, az érzékelőhiba és a szívveréshiány alatti számlálónövekmény a fogadott modellidő növekményével egyezik; a gyalogos miatti idő a CSV-exportban is megjelenik. A forráslenyomatokat, tényleges időpontot és nyers naplók elérési útját a bizonylat tartalmazza. Ez funkcionális ellenőrzés, nem a még hiányzó statisztikai kampány.

## Kézi parkolófoglalás: 2026. október 22.

A kézi elhelyezés hét új modelltesztjével együtt 21 modellteszt sikeres. A valódi MQTT/HTTP-integráció hét tesztesete 20 megfigyelt feltételt rögzített; a végrehajtott források lenyomatai változatlanok maradtak. A bizonylat a `results/integration/20261022T154401511804Z/verification.json`, az aktuális másolat a `results/verification.json`.

A felület 24 automatizált szerződésellenőrzése sikeres (`results/ui/design-v2/frontend-check.json`). A külön böngészős próba a két elhelyezett autót, a változatlan 0/3 teljesítést, a P02 helyet kapó mozgó autót, a nullázást és a 390 képpont széles mobil elrendezést ellenőrizte. Bizonyítékai a `results/ui/manual-parking` mappában találhatók. Ezek funkcionális ellenőrzések; statisztikai következtetést nem alapoznak meg.

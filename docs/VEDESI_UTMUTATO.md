# Védési útmutató

Ez felkészülési segédlet. A válaszokat a futó rendszer és a forráskód alapján érdemes saját szavakkal elmondani. AI segítségével készült programnál a segítség módját a tényleges történtek és a tantárgy előírásai szerint kell bemutatni; a dokumentum nem igazol önálló hallgatói szerzőséget.

## A feladat egy mondatban

Egy gyári területen mozgó, szimulált autó MQTT-n parkolóhelyet kér, a kiosztott helyhez halad, és virtuális gyalogosészlelés vagy felügyeleti hiba esetén megáll.

## Mit érdemes megérteni először?

Kövess végig egy autót. Keresd meg a kérését, a koordinátor válaszát, az első haladási telemetriát, a megállás okát és a parkolási eseményt. Ugyanazt a `run_id`, `car_id` és kérésazonosítót kövesd a naplóban. Ezután mutasd meg a forrásban, hol születik mindegyik döntés.

### Rendszerhatár és CPS-komponensek

| Osztály | A demonstrátor eleme | Mit képvisel? |
| --- | --- | --- |
| Fizikai | Szimulált autó, úthálózat, zebra, parkoló | A mozgó és elfoglalható környezetet; tényleges hardver nélkül. |
| Jelátalakító | Virtuális gyalogos- és távolságjel | A modell környezetéből a vezérlés által értelmezhető adatot képez. |
| Logikai | Járműállapotgép, parkolókoordinátor, MQTT, megfigyelő | Döntés, kommunikáció és állapotértelmezés. |
| Emberi | A műszerfal kezelője | Elindítja, figyeli és hibákkal vizsgálja a szimulációt. |

A rendszerhatáron belül a helyi szimuláció és a helyi hálózati adatút található. Tényleges gyári eszköz, autonóm autó, gyalogosfelismerő neurális hálózat és nyilvános felhőszolgáltatás nincs a megvalósításban.

A várakozási területről a kijárathoz történő átadás diszkrét modellművelet. A mért út a kijárattól indul; ebből a modellből a sorban állás mozgástávolsága nem számítható ki.

### Egy végigvezethető mérnöki érvelés

**Követelmény:** ugyanazt a parkolóhelyet ne kaphassa meg egyszerre két autó.

**Megvalósítás:** a központi koordinátor tartja nyilván a helyeket, és kiosztáskor azonnal fenntartottra (`reserved`) jelöli a választott helyet. A kérésazonosító alapján ismételt kéréshez nem kell új helyet kiosztani. A parkolási esemény a fenntartott helyet elfoglalt (`occupied`) állapotba viszi.

**Bizonyíték:** több autó kérését és ugyanannak a kérésnek az ismétlését kell vizsgálni. A foglalásokban egy helyhez legfeljebb egy autó tartozhat; az ismétlés eredménye ugyanaz a foglalás. A konkrét teszteredményt és a hozzá tartozó naplót kell bemutatni. A megoldás egyetlen, memóriában működő koordinátorra vonatkozik; újraindítást túlélő elosztott tranzakciókezelést nem bizonyít.

## Háromperces élő bemutató

| Idő | Teendő | Mit mutass meg? |
| --- | --- | --- |
| 0:00–0:30 | Új futás, 1× sebesség; gyalogos bekapcsolása, majd indítás. | Autók és szabad helyek, MQTT-üzenetszámláló, futásazonosító. |
| 0:30–1:00 | Várd meg a zebra előtti megállást, majd kapcsold ki a gyalogost. | Állapot és megállási ok; a hely foglalása közben megmarad. |
| 1:00–1:40 | Haladás közben kapcsold be, majd vissza a kommunikációs hibamódot. | Szívverés kora és kommunikáció miatti megállás. A telemetria szándékosan megmarad. |
| 1:40–2:15 | Haladás közben röviden kapcsold be az érzékelőhibát, majd állítsd vissza. | Érvénytelen érzékelőjel, megállási ok és visszaállás. |
| 2:15–3:00 | Mutass egy foglalást, parkolási eseményt és egy exportot. | Kérés → válasz → haladás → parkolás összefüggése. |

Az új futás szüneteltetve indul: a gyalogos beállítása után az indítás gombbal folytasd. Ha az aktuális autó már leparkolt, a hibabemutatót új futással kezdd. A bemutató előtt egyszer gyakorold végig a sorrendet; a táblázat időkeret, nem garantált modellfutási idő.

## Gyakori kérdések

1. **Mi a mérnöki probléma?** A parkolóhelyek következetes kiosztása és a jármű mozgásának feltételekhez kötése. Mutasd meg, melyik döntést hozza a koordinátor és melyiket a járműmodell.
2. **Miért kell MQTT?** A jármű, a koordinátor és a megfigyelő külön kliensként cserél üzeneteket. Ebben a feladatban a működő publish/subscribe adatút is követelmény. A választást ne indokold nem mért teljesítményelőnnyel.
3. **Ez valódi kommunikáció vagy csak függvényhívás?** Valódi TCP MQTT-közvetítő és három kliens működik. A mozgást és a szenzorjeleket viszont a program szimulálja. Az integrációs teszt és a fogadott MQTT-napló segít a különbséget bemutatni.
4. **Mi történik ismételt parkolási kérésnél?** Ugyanahhoz a kéréshez ugyanazt a foglalást kell használni. Ez az idempotens feldolgozás; nem szabad új helyet lefoglalni minden ismétlésnél.
5. **Mi történik, ha nincs szabad hely?** Az autó várakozik. A kis modellben a leparkolt autók nem távoznak, ezért a kapacitáshiány nem feltétlenül oldódik meg magától.
6. **Hogyan „látja” a gyalogost az autó?** Virtuális érzékelő számítja a jelét a modell ismert geometriájából és a beállított gyalogosjelenlétből. Nincs valódi képértelmezés. Az érzékelési hatótávot és a hibajelzőt mutasd meg a telemetriában.
7. **Biztonságos lenne ez egy gyárban?** A tesztek a korlátozott modell viselkedését ellenőrzik. Valós jármű fékútját, érzékelési hibáit vagy ipari biztonsági megfelelőségét nem igazolják.
8. **Milyen időt mérünk?** A mozgás és a mintavétel modellidőben történik; a szívverés felügyelete valós monoton órához kötött. A felgyorsított futás miatt a két idő nem cserélhető fel.
9. **Mit nem tud a kis változat?** Nincs egyidejű többautós forgalomszervezés, térképfelismerés, valós járműintegráció vagy újraindítást túlélő parkoló-adatbázis. Ezek kimondott rendszerhatárok.
10. **Mi volt a saját munkád?** Kizárólag megtörtént, bemutatható döntést, módosítást vagy mérést mondj. A saját napló, a tényleges fájlváltozás és a futási eredmény együtt használható; az AI által írt kódot ne nevezd saját kézi implementációnak.

## Következő tanulási feladat

Állíts össze saját mérési kérdést a parkolókapacitásról. Előre rögzítsd a beállításokat, a futás végét és a mutatókat; a várakozó autókat is szerepeltesd az értékelésben. Ennél a determinisztikus modellnél azonos beállítás ismétlése önmagában nem hoz független statisztikai mintát. A még hiányzó statisztikai kampány tervét külön kell kidolgozni, a valódi saját értelmezést pedig a hallgatónak kell megfogalmaznia.

# Gyári járműparkolás – Ipar 4.0

Gyárudvari járműparkolás determinisztikus szimulációja központi parkolóhely-kiosztással, virtuális gyalogosészleléssel és hibakezeléssel. A járművek a gyártósor kijáratától egy lefoglalt parkolóhelyig haladnak; az útjukat keresztező zebránál a szimulált érzékelés alapján megállnak, majd továbbindulnak.

## Működés és a kódtár tartalma

A járműmodell parkolóhelyet kér, a koordinátor lefoglalja az első szabad helyet, majd visszaadja az oda vezető útvonalat. A modell követi a mozgást, a virtuális érzékelő állapotát és a parkolás visszaigazolását. A futásazonosító, a kérésazonosító és a sorszámok segítik a hibás vagy korábbi parancsok kiszűrését.

A kódtár jelenleg a **determinisztikus modellt, alapkonfigurációját és tizennégy hálózat nélküli modelltesztet** tartalmazza. A helyi alkalmazás MQTT-futtatókörnyezete, műszerfala és integrációs tesztjei még nincsenek feltöltve. A forrás ezeket említő megjegyzései a helyi alkalmazásra utalnak.

A modell `World.step()` és `Coordinator.handle()` metódusai `(topic, payload)` üzenetpárokat adnak vissza. A most közzétett tesztek közvetlen függvényhívásokkal továbbítják ezeket; ez a változat önmagában még nem bizonyít működő MQTT-adatutat.

A projekt AI-közreműködéssel fejlesztett oktatási szimuláció.

## Ellenőrzés

Python **3.9 vagy újabb** szükséges; külső csomag telepítése nem kell. Az ellenőrzés Python 3.12 alatt történt. A repository gyökérmappájában:

```sh
python -m unittest discover -s tests -v
```

A tizennégy teszt vizsgálja a külön parkolóhelyekre érkezést és a pontos úthosszt, az egyidejű mozgás korlátozását és az üzenetsorrendet, az ismételt kérés kezelését, a hibás parancsok elutasítását, a betelt parkolót, a gyalogos miatti megállást és újraindulást, az érzékelő- és kapcsolathibát, a parkolás visszaigazolását, a hibás bemeneteket és a várakozósor átadását. Négy eset külön ellenőrzi az okonkénti álló modellidőt: a változó lépésközt és hibaprioritást, a zebra megközelítése és az állás közötti határt, a kizárt állapotokat, valamint a telemetria és az új futás számlálóinak elkülönítését.

## Okonkénti álló modellidő

A járművek `stop_time_s` mezője a gyalogos (`pedestrian`), az érzékelőhiba (`sensor_failure`) és a felügyelt szívverés hiánya (`communication`) miatt állva töltött modellidőt gyűjti. Mértékegysége szimulációs másodperc; az értékek az adott autó adott futásában összegzettek. Minták kiértékelésekor az utolsó értéket vagy két minta különbségét kell használni, az összegzett mintákat nem szabad összeadni.

Csak a teljesen álló, aktív megállási modelllépések számítanak. A fékezés, a sorban várakozás, a helyigénylés és a parkolás utáni idő kimarad. A zebra miatti megállást elérő, még elmozduló lépés nem számít bele, így megállásonként legfeljebb egy lépésnyi részidő hiányozhat. Egyidejű okoknál a szívveréshiány, az érzékelőhiba, majd a gyalogos sorrendje érvényes; egy lépés csak egy számlálót növel. Új `World` példányban a számlálók nulláról indulnak.

## Modell és rendszerhatár

- Alapbeállítás: **3 jármű, 8 parkolóhely, 1 zebra**, 64 × 38 méteres helyi koordinátarendszer. Ez általános oktatási térkép, nem egy valós Audi-telephely másolata.
- Egyszerre legfeljebb egy autó mozog. A koordinátor az első szabad helyet foglalja le, és előírt töröttvonalat rendel hozzá.
- A hely és az út méterben, a sebesség m/s-ban, a modellidő másodpercben értendő. Az alaplépés 0,1 s; az irány fokban szerepel, kelet 0°, észak −90°.
- A várakozóhelyről a kijárathoz kerülés diszkrét átadás; ez a szakasz nincs benne a megtett útban. A leparkolt autók nem távoznak.
- Az érzékelés a modell állapotából származik. A gyalogos megjelenését és az érzékelőhibát a teszt állítja; nincs kamera vagy képfeldolgozás.
- A `connected` bemenet a tesztben megadott kapcsolati állapot. A `heartbeat_timeout_s` konfiguráció tényleges időzítését a most még nem publikált futtatókörnyezet kezeli.
- Érzékelő- és kapcsolathibánál a sebesség azonnal nullázódik. A zebra előtti stopvonal diszkrét korlát; a túl későn bekapcsolt gyalogosjel nem ad visszamenőleges megállási garanciát.

Ezek funkcionális modelltesztek; nem statisztikai mérési kampány és nem valós jármű biztonsági igazolása. A beadandó teljesítéséhez a modell mellett a működő publish/subscribe adatút, annak külön ellenőrzése és a dokumentáció is szükséges.

## Fájlok

| Fájl | Feladat |
| --- | --- |
| `mini_yard/model.py` | Járműállapotgép, mozgás, virtuális érzékelés és parkolókoordinátor |
| `config.json` | A helyi alkalmazás alapkonfigurációja; a tesztek a modell azonos alapértékeit használják |
| `tests/test_model.py` | Tizennégy standard library `unittest` eset |

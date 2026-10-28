# Kapacitás felszabadítása új futás nélkül

## Mérnöki cél

Teljes kézi parkolófoglaltságnál az érkező autó eddig csak egy új futás után kaphatott szabad helyet. Az új kezelőművelet egy már ott álló háttérjármű eltávolítását teszi lehetővé. Így ugyanabban a futásban megfigyelhető a kapacitáshiány és annak feloldása.

## Használat

A „Már parkoló autók” panelen a kézzel elhelyezett autók külön választóban szerepelnek. Az „Autó eltávolítása” gomb a kijelölt elhelyezés felszabadítását kéri; a világrajz a visszaigazolásig megtartja az autót. Szünetben és jármű-életjelhiba mellett is működik a kezelői menedzsmentút.

Ha fut a modell és egy autó szabad helyre vár, a következő helyigénylésével megkaphatja a felszabadított parkolót. Emiatt a központi helyállapot eltávolítás után már `reserved` is lehet: a siker feltétele az előző háttérjármű foglaltságának megszűnése, nem a hely tartós üressége.

Csak kézzel elhelyezett háttérjármű távolítható el. A gyártósorról érkező flotta lefoglalt vagy már elfoglalt helyét a művelet nem szabadítja fel. A teljesítési számláló továbbra is a flotta feladatát méri. Az eltávolítás pillanatszerű foglaltságváltozás, nem szimulált kihajtási útvonal.

## Azonosítás és adatút

A `spawn_parked` kérés azonosítója egyben az elhelyezés `placement_id` értéke. Ez a világ háttérjárművében és a parkolóhely foglaltságában is megjelenik. A `remove_parked` HTTP-kérés mezői: `slot_id`, `placement_id`, `request_id` és `run_id`. A szerver visszafelé kompatibilisen tud kérésazonosítót generálni, de a felület saját azonosítót küld.

A kérés a `requests/parking_setup` MQTT-témán `operation: remove` értékkel jut a koordinátorhoz. Elfogadáskor a `commands/parking_setup` témán `kind: parked_removed` válasz érkezik; elutasításkor `parking_setup_rejected`. A kérés és a válasz azonosítja az érintett elhelyezést. A `parked_removed` esemény és a kezelői beavatkozás az exportált naplóban követhető.

Az újonnan ugyanarra a helyre elhelyezett autó új azonosítót kap. Régi elhelyezési vagy eltávolítási kérés megismétlése nem végezhet új mellékhatást. Az azonos kérésazonosító más műveletre vagy más elhelyezésre nem használható fel.

Minden HTTP-kezelőparancs az aktuális futáshoz kötődik. A futásazonosítót fogadáskor és a sorból történő alkalmazáskor is ellenőrzi a rendszer. A böngésző a vezérlés keletkezésekor látott futásazonosítót küldi; a nullázás közben elavult parancsot nem irányítja át az új futásra.

## Ellenőrzési esetek

- Eltávolítás szünetben, változatlan modellidővel és teljesítési számlálóval; külön próba életjelhiba mellett.
- Teljes nyolchelyes foglaltság után egy hely felszabadítása; a várakozó CAR01 ugyanabban a futásban megkapja azt.
- Ismételt kérés, foglalt flottajármű, üres vagy ismeretlen hely és hibás elhelyezésazonosító kezelése.
- Elhelyezés, eltávolítás és újbóli elhelyezés után régi kérések és visszaigazolások ismétlése.
- Előző futáshoz tartozó általános kezelőparancsok elutasítása; reset közben sorban maradt parancs kihagyása.
- A felületi rajz megőrzése HTTP-elfogadás után, MQTT- és állapot-visszaigazolás nélkül; később új flottajárműhöz kiosztott hely elfogadása eltávolítási sikerként.

A `tests/test_model.py`, `tests/integration.py` és `tests/frontend.cjs` eltérő ellenőrzési szinteket ad. A futtatáskor képzett bizonylatok a figyelmen kívül hagyott `results/` könyvtárba kerülnek. A funkcionális teszt nem statisztikai kampány és nem valós járműbiztonsági igazolás.

## Kísérő hibajavítások

390 képpont széles mobilnézetben a natív választók hosszú felirata túl szélesre növelte a kezelősort; a kártya levágta a gombok szélét. A mezők most zsugoríthatók, kis kijelzőn a választó és a gomb egymás alá kerül, a státuszszöveg pedig tördelhető. A teljes parkoló hosszú üzenetével és a kitöltött eltávolítási listával is történt böngészős ellenőrzés.

Az amqtt 0.11.3 közvetítő normál leállítása megszakadhatott egy korábban törölt küldési feladat `CancelledError` kivételén. A helyi brokeradapter kizárólag ezt a normál leállítási esetet kezeli; a könyvtár így befejezheti a listener és a monitor lezárását. Külső megszakítást és egyéb hibát továbbra sem nyel el. A négy brokerregresszió és a valódi MQTT-integráció végi szál-/portellenőrzés külön igazolja ezt.

## Végrehajtott ellenőrzés – 2026. október 28.

- 33/33 egységteszt sikeres: 29 modellteszt és 4 brokeréletciklus-teszt.
- 11/11 valódi MQTT/HTTP-integrációs teszt sikeres, 27 mért feltétellel. A vizsgált források lenyomatai változatlanok maradtak.
- 31/31 automatizált felületellenőrzés sikeres.
- Böngészőből ellenőrzött: szüneteltetett eltávolítás, nyolc foglalt helyből egy felszabadítása és CAR01 ugyanarra a helyre indulása, valamint mobilról végzett eltávolítás. A teljesítési számláló az álló háttérjárművek számától független maradt.

Az aktuális integrációs bizonylat `results/verification.json`, a felületi bizonylat `results/ui/design-v2/frontend-check.json`, a böngészős bizonyítékok könyvtára `results/ui/capacity-release`. Ezek helyben keletkező kimenetek, nem követett forrásfájlok.

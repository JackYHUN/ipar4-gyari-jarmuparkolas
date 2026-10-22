# Kézi parkolófoglalás – 2026. október 22.

## Használat

1. A „Már parkoló autók” panelen válassz egy szabad parkolóhelyet.
2. Kattints az „Autó elhelyezése” gombra. A kezelőfelület megvárja az MQTT-visszaigazolást és a fogadott világ-/koordinátorállapotot.
3. A sötét autó megjelenik a kiválasztott helyen „Helyben” jelöléssel. A hely kikerül a szabad helyek listájából.
4. További szabad helyekre újabb álló autó helyezhető. Az „Új futás” az egész szimulációval együtt ezeket is nullázza.

Az elhelyezés szünetben és haladás közben is használható. Az elfoglalt vagy már mozgó autóhoz lefoglalt parkolóhelyre új autó nem kerülhet. Az elhelyezett autó nem hajt végre útvonalat: a vizsgálat kezdeti vagy kezelő által módosított foglaltságát jelenti. Egyedi eltávolítás ebben a változatban nincs.

## Adatút és számlálók

Az elhelyezési kérés a `requests/parking_setup` MQTT-témán érkezik a központi koordinátorhoz. A döntés a `commands/parking_setup` témán jut vissza. Futás-, kérés- és sorszámazonosítás védi az alkalmazást az idegen, elkésett vagy ismételt üzenetek hibás felhasználásától. Az elfogadott álló autó külön `world.parked_cars` listába kerül, a központ pedig foglaltnak jelöli a helyét.

A teljesítési számláló kizárólag a gyártósorról érkező három autót méri. Két kézzel elhelyezett autó esetén ezért 6/8 hely szabad, a teljesítés pedig továbbra is 0/3. Ha ezután egy mozgó autó helyet foglal, 5/8 hely marad szabad: két hely foglalt, egy lefoglalt. Az álló autók foglaltsága és létrehozási eseménye naplózott; mozgási telemetriát nem kapnak.

A szívveréshiba kapcsoló csak a felügyelt életjelet és a mozgó autók normál helyfoglalási válaszait némítja el. A kézi elhelyezés külön kezelői menedzsmentút, ezért ebben a teszthelyzetben is működik.

## Felület és ellenőrzés

Az oldalról eltűnt az alsó védési blokk, annak navigációs hivatkozása, a teljes lábléc és az összehasonlító oldalra mutató link. A korábbi összehasonlító HTML-fájl nem része a kiszolgált útvonalaknak.

- 21/21 modellteszt sikeres: `python -m unittest discover -s tests -v`.
- 7/7 MQTT/HTTP-integrációs teszt sikeres, 20 mért feltétellel: `python -m tests.integration`.
- 24/24 automatizált felületellenőrzés sikeres: `python -m tests.verify_frontend`.
- Böngészőben ellenőrzött: két álló autó, a foglaltság és teljesítés külön kezelése, a mozgó CAR01 P02 helyfoglalása, új futás, eltávolított elemek, mobil szélesség és üres hibakonzol.

A géppel olvasható bizonylatok és a képernyőképek a `results/ui/manual-parking` mappában vannak. Az integráció nyers naplói és forráslenyomatai a `results/integration/20261022T154401511804Z` mappában találhatók. A funkcionális tesztek nem statisztikai mérési kampány eredményei.

A 2026. október 22-i helyi ellenőrzés idején ez a módosítás még nem volt commitolva vagy pusholva; az akkori Git-alap `e769884d3d058f3ed74d67a04ed10cca33932262` volt. A forrás Gitbe rendezése külön, későbbi lépés. A `results/` alatt hivatkozott bizonylatok helyi kimenetek, nem részei a követett forráscsomagnak.

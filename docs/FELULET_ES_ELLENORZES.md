# A felület korábbi finomítása

Történeti jegyzet a kézi parkolófoglalás előtti állapotról. Az akkori 16 felületi eset helyett az aktuális ellenőrzés 24 esetet futtat; a modell és a koordinátor későbbi bővítése a [kézi parkolófoglalás leírásában](KEZI_PARKOLOFOGLALAS.md) szerepel.

A változtatás a kis demonstrátor megjelenítését és kezelői visszajelzéseit érinti. A járműmodell, a koordinátor, az MQTT-adatút és a konfiguráció változatlan marad.

## Megjelenítés

A sötét navigációhoz világos munkafelület, következetes kártyák, helyi Inter betű és Tabler ikonok tartoznak. A kiválasztott jármű azonosítója és sebessége kiemelt; három lépés mutatja a helyfoglalás, az úton levő állapot és a parkolás kapcsolatát. A megállás oka továbbra is külön állapotjelzés, nem újabb kötelező életciklusszakasz.

A parkolási folyamatjelző a fogadott befejezett parkolások és a tervezett autók számából készül. A térkép új burkolati, útvonal- és járműrajzolást kapott, de a világ koordinátái, a mozgás és a helyfoglalások adatai változatlanok. A látvány nem vezet be új fizikai akadályokat.

## Pontosabb visszajelzések

- Egy kapcsoló újabb működtetése a fogadott állapot visszaigazolásáig vár. A különböző kezelőparancsok sorrendben jutnak a szerverhez.
- HTTP-kapcsolatvesztéskor a térkép és az érzékelőpanel korábbi mintát jelez, és a vezérlők letiltódnak. Egy másik autó kiválasztása nem változtatja élővé az elavult jelzést.
- Átmenetileg üres járműlistánál az ellenőrzőpanel törli a régi sebességet és foglalást.
- A hatótávrajz a fogadott méterértékből készül. A zebra megközelítési szakaszán kívül nem jelenik meg aktív érzékelési mező.
- Az eseményexport felirata a tényleges JSON-formátumot nevezi meg. A teljes MQTT-export ettől külön JSONL-fájl.
- Mobilméretnél a térképen vagy a járműlistában kiválasztott autó részleteihez görget a felület, a rögzített fejléc alatt hagyva a kártyát.
- A lap alján a navigáció a ténylegesen elért utolsó szakaszt jelöli akkor is, ha az már nem görgethető a fejléc alá.

Az érzékelési rajz szemléltetés. Nem kamerakép, nem önálló látótér-szimuláció, és a szebb grafika nem növeli a fizikai modell részletességét.

## Ellenőrzés és bizonyíték

```powershell
.\.venv\Scripts\python.exe -X utf8 -m tests.verify_frontend
```

A `tests/frontend.cjs` a tényleges felületi JavaScriptet futtatja minimális DOM-mal. Tizenhat esetben ellenőrzi többek között az üres adatot, a fogadott koordináták rajzolását, az érzékelő hatótávját, a fókusz megőrzését, a kapcsolók visszaigazolását, a lapvégi navigációt és az elavult állapot jelzését. A nyugta az ellenőrzött HTML-, CSS- és JavaScript-források SHA-256 lenyomatát tartalmazza: `results/ui/design-v2/frontend-check.json`.

A tényleges böngészős elrendezésvizsgálat külön bizonyíték: `results/ui/design-v2/browser-check.json` és az ugyanott tárolt képernyőképek. A minimális DOM-teszt nem bizonyít vizuális megfelelőséget; a böngészős vizsgálat pedig nem teljes hozzáférhetőségi tanúsítás.

A korábbi `results/verification.json` a saját időpontjában vizsgált MQTT/HTTP-forrásokat rögzíti. Történeti bizonyíték, nem az új felület látványának igazolása. A felületi finomítás AI segítséggel készült, és önmagában nem igazol hallgatói önálló munkát.

## Helyi grafikai források

A `docs/vendor/console` könyvtár tartalmazza az Inter 4.1 betűt, a Tabler Icons 3.31.0 felhasznált forrásait, az eredeti licenceket és a `SOURCES.json` származási jegyzéket. A betű és az ikonok a CSS-be ágyazva, külső CDN nélkül töltődnek.

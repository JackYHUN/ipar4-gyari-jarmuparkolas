# Fejlesztési és ellenőrzési napló

A napló a projekt ténylegesen rögzített fejlesztési lépéseit, verzióit és mérési eredményeit foglalja össze. A dátumok a Git-előzményekből, az eredmények a megőrzött vizsgálatokból származnak. A fejlesztés, dokumentálás és az automatizált vizsgálatok AI-támogatással készültek.

## A fejlesztés menete

| Dátum | Elvégzett fejlesztés | Azonosító |
| --- | --- | --- |
| 2026.10.20. | A determinisztikus parkolási modell, az egységtesztek és az önálló projekt kialakítása. | 5d5a0ac, 71c4d2c |
| 2026.10.22. | A védelmi megállások okonkénti időmérésének bevezetése. | e769884 |
| 2026.10.23. | MQTT-megfigyelői felület és kézi háttérjármű-elhelyezés. | ccb2c1e |
| 2026.10.28. | A kézzel foglalt parkolóhelyek MQTT-n keresztüli felszabadítása. | a40e6c5 |
| 2026.11.12. | Az önálló parkolási folyamat és a kommunikáció helyreállításának továbbfejlesztése; kézi műveletek egyeztetése újrafoglalás előtt. | 8e1fed8, f00f9f4 |
| 2026.11.26. | A telemetria és vezérlés feliratkozási QoS-szintjének szétválasztása, az állapotmásolás csökkentése és reprodukálható automatikus ellenőrzés. | 8df0685, 5d2e024 |
| 2026.11.26. | A mérés rövid blokkokra alakítása, 20 ms-os lekérdezési szünet, friss állapot megkövetelése. | 6a30094 |

## Ellenőrzések és döntések

A november 26-i regresszióban 46 Python-teszt, 21 MQTT/HTTP-integrációs próba (38 feltétel), 32 felületi ellenőrzés és a Chromium-próba sikeres volt. A parkolási modellkampányban mind a 30 szabad célhelyes próba nyugtázott parkolással zárult.

A késleltetésvizsgálat két próbája megszakadt. Az elsőben a terhelési ablak a parkolás befejezése miatt érvénytelenné vált; a másodikban az adatút elavult. A részadatok nem kerültek a végleges mintába. A módosított mérési módszer 30 teljes párja és 120 elemzett művelete ellenőrzött. A korábbi, eltérő forrású és feltételű 720 műveletet külön kell értelmezni.

A friss bemutatóvideó két új MQTT-futásból készült, a végleges kódverzió GitHub Actions ellenőrzése sikeres.

A hat páros kiegészítő kampányban 24 új művelet került elemzésre. Az átlagos páros különbség 315,53 ms, a nyers adatok és az újraszámítás egyeznek. Részletek: KIEGESZITO_MERES.md.

## Személyes hozzájárulás és a felhasznált segítség

A témaválasztás oktatói jóváhagyása a hallgató közlése szerint megtörtént. A jelen napló nem igazol önállóan elvégzett hallgatói implementációt vagy mérést. A személyesen elvégzett további tevékenységeket azok megtörténte után, valós dátummal és bizonyítékkal lehet kiegészíteni.

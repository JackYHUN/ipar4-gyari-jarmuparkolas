# Kiegészítő válaszidő-vizsgálat

## A vizsgálat célja

A fő mérési kampány után ugyanazzal a programverzióval egy rövidebb, különálló ismétlő mérés készült. A cél annak ellenőrzése volt, hogy a futó és szüneteltetett modell mellett megfigyelt válaszidők különbsége egy másik mérési munkamenetben is megjelenik-e. Az eredmény nem került bele a fő, 30 páros mintába.

## Módszer

A vizsgálat hat futáspárból állt. Mindkét feltételben új alkalmazáspéldány indult; a páron belüli sorrendet a rögzített véletlenmag adta. Egy bemelegítő ciklus után egy elhelyezési és egy eltávolítási művelet következett. A futó modell névleges sebessége 4×, a kért lekérdezési szünet 20 ms volt. A kezdőpont a kérés sorba állításának kísérlete, a végpont az első olyan lekérdezés, amelyben a friss megfigyelt állapot és az alkalmazásszintű nyugta egyezett.

## Eredmények

| Mutató | Szüneteltetett | Futó (4×) |
| --- | ---: | ---: |
| Elemzett műveletek | 12 | 12 |
| Medián | 85,85 ms | 411,70 ms |
| 95. percentilis | 111,90 ms | 773,83 ms |
| Maximum | 114,26 ms | 904,06 ms |
| MQTT-kéréstől a megfigyelt nyugtáig, medián | 24,17 ms | 96,33 ms |

A blokkmediánok átlagos páros különbsége 315,53 ms, a 95%-os percentilis bootstrap-intervallum 152,77–473,59 ms. A futó modell mellett ebben a mintában is hosszabb válaszidő adódott. A hat pár miatt az intervallum tág, ezért az eredmény csak kiegészítő jellegű. Az alkalmazás ütemezése, a közvetítő terhelése és a lekérdezés együttesen hat a válaszidőre; ezek külön hatását a vizsgálat nem méri.

## Ellenőrizhetőség

- 12 külön alkalmazásindítás, 24 mért és 24 bemelegítő művelet.
- 39 fájl ellenőrzőösszege, a forráslenyomatok, a nyers MQTT-rekordok és a statisztikai újraszámítás egyeznek.
- A nyers adatok: forras/results/kiegeszito-2026-11-26/.
- Újraellenőrzés: python -m experiments.verify_campaign results/kiegeszito-2026-11-26
- A vizsgálat automatizált, AI-közreműködéssel indított és dokumentált mérés. Nem személyesen végzett hallgatói mérés igazolása.

# MQTT-kezelőút válaszidejének vizsgálata

Ez a mérési terv a jelenlegi `mini_yard` alkalmazáshoz készült. A vizsgálatot a
`python -B -X utf8 -m experiments.latency_campaign` parancs hajtja végre. Külön
alkalmazásokat és szabad loopback MQTT-portokat használ, a felhasználó futó demóját
nem vezérli. Nem módosítja az alkalmazás vezérlési algoritmusát.

## Kérdés, referencia és mérőszám

A kérdés az, hogy a normál telemetriaforgalom mellett mennyivel változik a kézi
parkolófoglalás és -felszabadítás megfigyelt válaszideje. A referencia a
szüneteltetett modell: a MQTT-kapcsolatok, az életjel és a koordinátor időszakos
publikációi ilyenkor is működnek. A másik feltétel azonos konfigurációval, négyszeres
modellidő-sebességgel fut. Ez a `dt = 0,1 s` konfigurációnál névlegesen negyven
modelllépést jelent egy valós másodperc alatt, továbbá az érkező autók telemetriáját
és a világállapotot is publikálja. A futási sebesség itt terhelési feltétel; nem
valós jármű sebességnövelése.

Az elsődleges mérőszám a kezelői kérés `Application.command` hívásának kezdete és
az első olyan lekérdezés vége közötti valós idő, amelyben egyszerre teljesül:

1. A MQTT-n megfigyelt nyugta a kért művelethez, kérésazonosítóhoz és
   járműpéldány-azonosítóhoz tartozik, eredménye sikeres.
2. A MQTT-ről megfigyelt világállapot a kívánt foglaltságot és statikus autót
   tartalmazza, illetve eltávolításkor már nem tartalmazza azt.
3. A szintén MQTT-ről megfigyelt központi foglaltsági állapot egyezik ezzel.

A mértékegység milliszekundum, az óra `time.perf_counter_ns`. A mérés magában foglalja
a kezelői sorba állítást, az alkalmazás ütemezését, a valódi TCP/MQTT-kérést és
választ, a járműmodell nyugtakezelését, a megfigyelő frissülését és a lekérdezési
késést. A mérési program közvetlen Python-kezelőhívást végez: **HTTP-átvitel,
böngészői lekérdezés és képkirajzolás nincs a mérőszámban**. Ez nem tisztán
hálózati késleltetés.

Másodlagos leíró adat a kérés publikációjától a koordinátori nyugta megfigyelőhöz
érkezéséig eltelt valós idő. A két azonosított MQTT-rekord monoton óraadataiból
számítható; az ACK érkezése önmagában nem bizonyítaná a teljes állapot-egyezést.

## Előre rögzített eljárás

Az alapértelmezett kampány harminc alkalmazáspárból áll. Minden párban a két
feltétel sorrendjét rögzített magú pszeudovéletlen generátor választja ki. Mindkét
feltétel teljesen új közvetítővel, MQTT-kliensekkel, futásazonosítóval és
alapállapottal indul; a hálózati előfizetések és az életjel létrejöttét kivárja.
A futó feltételben a modellidő indulását is ellenőrzi. Ezután egy beállítási és
eltávolítási bemelegítő ciklus történik, amely nem szerepel a becslésben.

Feltételenként hat mért ciklus következik, ciklusonként a P08 helyre elhelyezéssel
és ugyanannak a statikus járműpéldánynak az eltávolításával. Az eredeti flotta
első autója másik helyet kap, így a vizsgált helyet nem foglalja le. A program
ellenőrzi, hogy a futó terhelési ablak végéig még nem parkolt le érkező autó.
A műveletek egymás után futnak, ezért az előző befejezését követi a következő kérés.

A harminc pár egy rövid, korlátozott feltáró mérés: nem előzetes erővizsgálattal
garantált mintaszám, és semmilyen ipari megbízhatósági szintet nem igazol. Az
előírt minta helyett nem ismétlünk azonos determinisztikus modellidő-adatot:
itt minden megfigyeléshez tényleges hálózati kommunikáció és valós ütemezés tartozik.

A módszer és a sorrend `plan.json` fájlja **a futás előtt** íródik ki. A teljes
futás során változatlan forrásfájlokat SHA-256 lenyomatok ellenőrzik. Egy művelet
öt másodperces időtúllépése, alkalmazáshiba, hiányos MQTT-bizonyíték vagy
forrásváltozás az egész kampányt sikertelennek jelöli; az ilyen eset nem dobható
el csendben a statisztikából.

## Statisztikai egység és bizonytalanság

Egy feltételen belül a tizenkét művelet mediánja adja a blokk jellemző értékét.
Az elsődleges becslés a harminc páros különbség átlaga, mindig a futó feltétel
mediánjából kivonva a szüneteltetett feltétel mediánját. Így egy alkalmazáspár
egyforma súlyt kap; a több száz MQTT-üzenetet nem tekintjük több száz független
kísérletnek.

A 95%-os percentilis bootstrap-intervallum húszezer újramintázással a **teljes
páros különbségeket** mintázza visszatevéssel. Ez a rövid munkamenetben és a
választott terhelések mellett megfigyelt átlagos különbség bizonytalanságát
szemlélteti. A blokkfüggetlenség/felcserélhetőség feltevése csak közelítő: az
újraindított alkalmazások ugyanazon a gépen, időben egymás után futnak, a gép
terhelése és a környezet lassú változása függést hagyhat. A randomizált sorrend
csökkentheti a sorrendhatást, de ezt nem bizonyítja teljesen megszűntnek.

A kívánt állapotot két milliszekundumos kért várakozásokkal kérdezzük le, de az
operációs rendszer tovább késleltetheti az ébredést. A program minden műveletnél
menti a tényleges legnagyobb lekérdezési időközt. A mért végpont az első sikeres
lekérdezés, nem a belső állapotváltozás pontos pillanata. A nanomásodperces
számláló ezért nem jelent nanomásodperces mérési pontosságot.

Az összes műveletből számított medián, 95. percentilis és maximum kizárólag leíró
adat. A maximum nem legrosszabb eseti korlát. Nem állítható az eredményekből
valódi gyári hálózat, különálló ECU, rádiós kapcsolat vagy fizikai fékezés
teljesítménye, és az sem, hogy a watchdog időkorlát minden helyzetben tartható.

## Ellenőrizhető kimenetek

A `results/campaign/<UTC-azonosító>/` mappában:

- `plan.json`: futás előtt rögzített terv, konfiguráció, sorrend, óra, környezet
  és forráslenyomatok.
- `operations.jsonl`: minden bemelegítő és mért művelet időadata, azonosítója,
  végállapot-ellenőrzése és a hozzá tartozó MQTT kérés/nyugta.
- `raw/pair-*/<feltétel>/results/<run_id>/`: a módosítatlan alkalmazás teljes
  MQTT-naplója és futási metaadatai.
- `summary.json`: páronkénti blokkmediánok, becslés, intervallum és leíró adatok.
- `manifest.json`: a kimeneti fájlok SHA-256 lenyomatai. Ez sértetlenségi
  ellenőrzés, önmagában nem külső hitelesítés.

Sikertelen futás esetén `failure.json` is készül; ezt nem szabad sikeres
kampányként bemutatni. A `--pairs` és `--cycles` kapcsolók rövid technikai próbát
tesznek lehetővé. Az eredményben szereplő tényleges mintaszámot mindig ellenőrizni
kell, a rövid próba nem az alapértelmezett teljes kampány.

A blokkos elemzés súlyozását, a hiányos párok elutasítását és a puszta nyugta
elégtelenségét a `tests/test_campaign.py` regressziótesztjei ellenőrzik.

A mentett kampány külön, hálózat nélkül újraellenőrizhető:

```powershell
python -B -X utf8 -m experiments.verify_campaign results/campaign/<UTC-azonosító>
```

Ez ellenőrzi a fájllenyomatokat, a jelenlegi forrásegyezést, a teljes
műveletszámot, az egyedi kérésazonosítókat, a műveletekhez csatolt kérés/nyugta
egyezését a nyers MQTT-naplókkal, majd újraszámítja a páros becslést és a
bootstrap-intervallumot. Ez a mérési nyugta belső konzisztenciájának ellenőrzése,
nem független külső tanúsítás.

## A helyenkénti egyeztetést tartalmazó kiadás mérése

A `results/mqtt-campaign-2026-11-12-r5` kampány 30 alkalmazáspárt, 720 mért
és 120 bemelegítő műveletet tartalmaz. Az összesített medián szünetben
65,06 ms, négyszeres futásnál 133,85 ms. A páronkénti blokkmediánok
átlagos különbsége 77,93 ms, a páros percentilis bootstrap 95%-os
intervalluma 69,16–88,22 ms. A megfigyelt maximum 363,75 ms.
Az offline ellenőrzés igazolta a 183 fájllenyomatot, a 840 művelet nyers
rekordjait, a kiadás forráslenyomatait és a statisztika reprodukálhatóságát.

## A teljes HTTP-fejléc-határidőt tartalmazó korábbi kiadás mérése

A `results/mqtt-campaign-2026-11-12-r4` kampány a kiadás forrásával futott.
A 30 alkalmazáspár 720 mért és 120 bemelegítő műveletet tartalmaz. Az
összesített medián szünetben 64,49 ms, négyszeres futásnál 129,66 ms.
A páronkénti blokkmediánok átlagos különbsége 66,12 ms, a páros percentilis
bootstrap 95%-os intervalluma 62,66–69,91 ms. A megfigyelt maximum 241,98 ms.
Az offline ellenőrzés mind a 183 fájllenyomatot, a 840 művelet nyers rekordjait,
az aktuális forráslenyomatokat és a statisztika egyezését ellenőrizte.

## A november 12-i korábbi kiadás mérése

A `results/mqtt-campaign-2026-11-12-r3` kampány a kapcsolóesemények
újrapróbálását és a HTTP-korlátokat tartalmazó forrásverzión futott. A 30
alkalmazáspár 720 mért és 120 bemelegítő műveletet tartalmaz. Az összesített
medián 67,84 ms szünetben és 127,76 ms négyszeres futásnál. A páronkénti
blokkmediánok átlagos különbsége 59,18 ms, a páros percentilis bootstrap
95%-os intervalluma 55,95–62,77 ms. A legnagyobb megfigyelt érték 179,25 ms.

Az offline ellenőrzésben egyezett mind a 183 fájllenyomat, mind a 840
művelet nyers kérés- és nyugtarekordja, az aktuális forráslenyomatok és az
újraszámolt statisztika.

## A november 11-i korábbi kiadás mérése

A november 11-én újramért, `results/mqtt-campaign-2026-11-11-r2` alatti kampány
30 alkalmazáspárt, 60 futást, 720 mért és 120 bemelegítő műveletet tartalmaz.
A megfigyelt állapot egyezéséig mért összesített medián 80,29 ms szüneteltetett
és 129,61 ms négyszeres futás mellett. A páros blokkmediánok átlagos különbsége
49,39 ms, a páros percentilis bootstrap 95%-os intervalluma 44,46–54,52 ms.
A legnagyobb megfigyelt érték 216,71 ms volt.

Az offline ellenőrző 183 fájllenyomatot és mind a 840 művelet eredeti
kérés- és nyugtarekordját ellenőrizte. A forráslenyomatok egyeznek és a
statisztikák újraszámíthatók. A nyers futáskönyvtárakban a rendezett
leállításkor rögzített `clock.json` is megtalálható. A különböző munkamenetek
eredményei az adott körülményeket írják le.

## A 2026. október 29-i kampány

A teljes sikeres kampány azonosítója `20261029T114309676297Z`. Harminc pár,
hatvan újonnan indított alkalmazás és hétszázhúsz mért művelet szerepel benne;
a százhúsz bemelegítő műveletet a becslés kizárja. A mintaszám az előzetes
tervvel egyezik. Az alkalmazások nem jeleztek hibát vagy érvénytelen MQTT-üzenetet.

| Feltétel | Mért műveletek | Összesített medián | Leíró 95. percentilis | Megfigyelt maximum |
| --- | ---: | ---: | ---: | ---: |
| Szüneteltetett referencia | 360 | 103,36 ms | 163,20 ms | 221,61 ms |
| Futó modell, négyszeres modellidő | 360 | 264,75 ms | 547,08 ms | 1190,56 ms |

Az elsődleges, páronkénti blokkmediánokon alapuló átlagos különbség **166,71 ms**,
a páros percentilis bootstrap 95%-os intervalluma **[144,74; 189,50] ms**. Ez nem
az összesített két medián kivonása. Előbb minden alkalmazás mediánját, majd az
összetartozó párok különbségét számítjuk, és ezeket átlagoljuk. A húszezer
újramintázás a rögzített `20261030` maggal reprodukálható.

A helyi kísérletben a futó telemetria mellett nagyobb kezelési válaszidő
figyelhető meg. A leglassabb művelet meghaladta az egy másodpercet, ezért ebből
a mérésből szubszekundumos kezelési garancia nem adható. A függőben lévő
kézi műveletet a kezelőfelület helyesen csak MQTT-nyugta és megfigyelt
állapotelemzés után tekintheti késznek. Ez másik adatút, mint a jármű
életjel-felügyelete; a kezelési maximumot nem szabad a watchdog reakcióidejének
nevezni.

A másodlagos, publikált kérés és megfigyelt nyugta közötti medián a két
feltételben 33,19 ms és 70,52 ms. Ez alacsonyabb a teljes megfigyelt konzisztencia
idejénél, ami alátámasztja, hogy az ACK beérkezése és a teljes állapot
megfigyelhetősége külön mérendő esemény. A legnagyobb tényleges lekérdezési
időköz 66,81 ms volt; a kért két milliszekundumos várakozás nem mérési
pontossági állítás.

Az offline újraellenőrzés 123 lenyomattal védett kimeneti fájlt, a 840 művelet
kérés/nyugta rekordjának nyers naplóegyezését, a jelenlegi forráslenyomatokat
és az újraszámított intervallum egyezését igazolta. A `manifest.json` saját
magát nem tartalmazza, ezért a mappa teljes fájlszáma eggyel több.

### A mérőeszköz ellenőrzésekor talált hiba

A `20261029T114033573735Z` azonosítójú rövid technikai próba után az első teljes
próba (`20261029T114153811444Z`) megszakadt. A mérőeszköz a felület korlátozott,
százhúsz rekordos élő üzenetablakából kereste vissza a kérés/nyugta párost; egy
lassú művelet és a telemetriaforgalom mellett a kérés már kikerült ebből az
ablakból. A teljes MQTT-napló közben rendelkezésre állt. Ez a mérőeszköz
bizonyítékkeresési hibája volt, nem igazolt alkalmazásoldali naplóvesztés.

A javított mérőeszköz az időmérés lezárása után a teljes, tartós MQTT-naplóból
keres. A megszakadt próbát `failure.json` és nyers naplói jelölik; egyetlen
részmérését sem egyesítettük a sikeres kampánnyal. A sikeres kampányhoz új,
előre kiírt terv és a javított mérőeszköz külön forráslenyomata tartozik.

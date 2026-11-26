# Beadási követelmények és ellenőrzési nyomvonal

Az alkalmazás K1–K20 műszaki követelményei az ELLENORZESI_TERV.md fájlban találhatók. Ez a melléklet a konkrét tesztesetek és bizonyítékok visszakeresését segíti. A teszt neve önmagában nem sikerigazolás: a futtatás eredménye a külön mentett ellenőrzési bizonylatban ellenőrizhető.

## Műszaki megfeleltetés

Az M jelölés a tests/test_model.py, az I a tests/integration.py, a B a tests/browser.cjs fájlra utal. Az integrációs esetszám a test_XX előtaggal egyezik.

| Követelmény | Konkrét ellenőrzés | Bizonyíték |
|---|---|---|
| K1 Egyedi foglalás | M: test_three_cars_finish_distinct_places_with_exact_path_length; I01 | Foglalási azonosítók és elfoglalt helyek |
| K2 Ismételt kérés | M: test_duplicate_request_returns_same_reservation_without_double_booking; I01 | Változatlan foglalás és darabszám |
| K3 Zebra | M: test_bounded_zebra_braking_and_late_detection; I02 | Pozíció, sebesség, késői jel |
| K4 Továbbhaladás | M: test_pedestrian_stop_clamps_at_stop_line_and_resume_finishes; I02 | Gyalogos távozása és állapotváltás |
| K5 Életjel | M: test_sensor_failure_and_heartbeat_loss_stop_without_drift_then_resume; I04 | Valós órán mért életjelkor, megállás |
| K6 Szenzorhiba | M: test_maneuver_is_slow_and_sensor_failure_interrupts_pose_check; I03 | Szenzorérvényesség, nulla sebesség, folytatás |
| K7 Telt parkoló | M: test_capacity_exhaustion_keeps_other_cars_waiting; I09 | Kiosztásra váró jármű |
| K8 MQTT-műszerfal | I01, I12; B | Megfigyelőből származó világ- és koordinátorállapot |
| K9 Export | I01, I05, I17 | Nyers JSONL, CSV és eseményexport |
| K10 Állásidő | M: test_stop_times_use_model_seconds_and_do_not_double_count_faults; I02–I04 | Okkódos összegzők és modellidő |
| K11 Kézi foglalás | M: test_setup_rejects_occupied_reserved_and_unknown_slots_without_reassignment; I06 | Nyugta, foglaltság, foglalt hely elutasítása |
| K12 Háttérjármű | M: test_preparked_car_is_separate_from_moving_fleet_and_preserves_counts; I06–I07 | Flotta teljesítésének és háttérfoglaltságának külön mutatója |
| K13 Kapacitásfelszabadítás | M: test_removing_a_preparked_car_unblocks_capacity_wait_in_the_same_run; I09 | Azonos futásban új foglalás |
| K14 Régi üzenet | M: test_old_spawn_and_removal_replays_cannot_resurrect_or_delete_replacement; I08 | Elhelyezéspéldány azonosítója |
| K15 Régi futás | I10–I11 | Elutasított régi run_id, új futás változatlan |
| K16 Parkolás | M: test_invalid_parked_report_cannot_occupy_slot; I13, I15 | Póz, kontúr, stabilitás, nyugta |
| K17 Nyugtavesztés | I13–I15, I20 | Újraküldés, egyeztetés, folytatás |
| K18 Adatfrissesség | I18, I21; B | Valódi brokerkiesés és a böngésző hibajelzése |
| K19 Felület | B; tests/frontend.cjs | 320, 390, 1280 px; fókusz, billentyűzet, vezérlés |
| K20 Hitelesítés | I12, I16 | Hibás, illetve aláírás nélküli rekord nem írja felül az állapotot |

## Tantárgyi ellenőrzés (rendelkezésre álló anyagok)

A helyi kéziratsablon és a publikáló–közvetítő–feliratkozó adatútról készült előadásdia alapján:

| Szempont | Hol ellenőrizhető? |
|---|---|
| Érzékelő vagy szimulált folyamat | Jelentés 1–4. fejezet; mini_yard/model.py |
| Publikáló, közvetítő, feliratkozó | Jelentés 2. fejezet; mqtt_bus.py, broker.py |
| Feldolgozás és megjelenítés vagy döntés | Jelentés 2., 5., 6. fejezet; runtime.py, web/ |
| Tervezési ábrák és rendszerhatár | Jelentés 1., 2., 4., 5. ábrája; szerkeszthető UML-források |
| Mérési módszer és értelmezés | Jelentés 7–9. fejezet; nyers kampánymappák és újraszámoló |
| Működési videó | A beadási csomag MP4-fájlja és a video/ bizonyítékai |
| Formai követelmények | Word- és PDF-fájl, ábrafeliratok, natív táblázatok, alternatív szövegek, forrásjegyzék |
| Hozzáférhetőség | GitHub, beadási ZIP, telepítési útmutató |
| Saját hallgatói munka | A hallgató tényleges munkanaplója és védése; automatizált teszt nem bizonyítja |
| Témaválasztás és AI-segítség elfogadása | Oktatói visszaigazolás szükséges; a teljes aktuális pontozási útmutató nem áll rendelkezésre |

## Reprodukálás

A beadási ZIP forras/ mappája a teljes programot, a forras/results/ mappa a korábbi mérési bizonyítékot tartalmazza. A GitHub szándékosan nem követi a nagy futásnaplókat. A csomag lenyomatjegyzéke és a történeti mérés saját forráslenyomatai két külön ellenőrzési szintet jelentenek.

Windows alatt a README telepítési parancsai után szüneteltetve indítható a műszerfal; a teljes böngészős teszthez npm ci és npx playwright install chromium szükséges. A teszteket egyenként, nem más intenzív feladattal párhuzamosan célszerű futtatni. A 4× gyorsítás névleges modellidő-szorzó, nem garantált ütemezési határidő. Elavult adat esetén a vezérlés letiltása helyes védelmi reakció, de egy el nem végzett névleges parkolási próba nem számíthat sikeresnek.

A tényleges, aktuális futási eredmények és esetleges eltérések a beadási csomag külön ellenőrzési naplójában szerepelnek. 

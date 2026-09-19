# data_agregate

Два выгрузчика космической погоды. Общее: Python 3.10+, lookback от текущего UTC, спутник по году (2023–24 → GOES-18, 2025–26 → GOES-19), выход 30 мин (кроме `--step` у CSV).

```bash
cd data_agregate
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

NCEI запаздывает примерно на сутки. Для «сегодня» используйте `fetch_space_weather.py`. Для архива SEISS+Kp — `fetch_ncei_gfz.py`.

---

## 1. `fetch_space_weather.py` — оперативный набор

Источники: SWPC JSON, NCEI (если файлы уже есть), GFZ, RTSW.

```bash
.venv/bin/python fetch_space_weather.py              # 1 сутки
.venv/bin/python fetch_space_weather.py --days 2
.venv/bin/python fetch_space_weather.py --hours 12 --mpsl
.venv/bin/python to_csv.py --step 30
.venv/bin/python to_csv.py --step 5 --no-ffill
```

Выход: `output/space_weather.json`, `output/space_weather.csv`.  
`--mpsl` — ещё NCEI L1b MPS-LO (~220 МБ/сутки).  
`to_csv.py`: `--step` минуты (0 = `interval_minutes` из JSON), `--no-ffill` не протягивает редкие индексы.

### Поля CSV (`swpc_*`, `gfz_*`, `rtsw_*`, …)

В бине числа усредняются. Префиксы:

| Префикс | Источник |
|---|---|
| `time` | начало бина UTC |
| `gfz_` | GFZ, см. §3 |
| `swpc_mpsh_` | SWPC, электроны MPS-HI GOES-19 |
| `swpc_sgps_` | SWPC, протоны/альфа SGPS |
| `swpc_mag_` | SWPC, магнитометр GOES |
| `swpc_xrays_` | SWPC EXIS, рентген |
| `swpc_euvs_` | SWPC EXIS, EUV |
| `swpc_kp_` | оценка Kp SWPC, не GFZ |
| `dst_` | Dst модели SWPC |
| `f107_` | F10.7 SWPC |
| `rtsw_mag_{ACE\|IMAP\|SOLAR1}_` | IMF на L1 |
| `rtsw_wind_{ACE\|IMAP\|SOLAR1}_` | плазма на L1 |
| `ncei_*` / `l1b_*` | NCEI, если файлы были (см. §2) |

**MPS-HI электроны** (`частиц / (см² с ср кэВ)`, интеграл без `/кэВ`):  
`swpc_mpsh_differential_electrons_g19_{79,134,186,271,378,548,865,1509,2205,2894}_keV`,  
`swpc_mpsh_integral_electrons_g19_ge2_MeV` (≥2 МэВ).

**SGPS протоны** (дифференциал — те же единицы; интеграл — без `/кэВ`):  
P1 1.02–1.86, P2A 1.90–2.30, P2B 2.31–3.34, P3 3.40–6.48, P4 5.84–11, P5 11.6–23.3, P6 25.9–38.1, P7 40.3–73.4, P8A 83.7–98.5, P8B 99.9–118, P8C 115–143, P9 160–242, P10 276–404 МэВ.  
Интегралы: `ge1/5/10/30/50/60/100/500_MeV`.

**SGPS альфа:** A1 3.79–6.78, A2A 7.60–8.90, A2B 9.18–12.8, A3 15.2–27.1, A4 30.1–49.2, A5 55.8–99.5, A6 104–158, A7 164–286, A9 318–380, A10 394–485, A11 573–894 МэВ.

**MAG, нТл:** `Hp` (север), `He` (к Земле), `Hn` (восток), `total`, `arcjet_flag` (двигатели).

**X-rays, Вт/м²:** `{g18|g19}_{0_05_0_4nm|0_1_0_8nm}_{flux|observed_flux|electron_correction|electron_contaminaton}`.  
`flux` — исправленный; `0.1–0.8 нм` — канал класса вспышки.

**EUV:** `{g18|g19}_{256|284|304|1175|1216|1335|1405|mgii_index}_{value|au_factor|flags_eclipse|flags_lunar_transit|flags_geocorona}`.  
Линии в Å; `value` — поток (Вт/м²) или индекс Mg II; `au_factor` — к 1 а.е.

**Kp SWPC:** `estimated_kp` (дробный), `kp_index` (целый), `kp` (текст, `0Z`).

**Dst:** `dst_dst`, нТл.

**F10.7:** `flux` (sfu), `frequency` (МГц), `reporting_schedule`, остальные служебные.

**RTSW MAG, нТл / °:** `bt`; `bx/by/bz` и `theta/phi` в GSE и GSM; `sample_size`; `active`; `overall_quality`; флаги `max_*`.

**RTSW wind:** `proton_speed` (км/с), `proton_density` (см⁻³), `proton_temperature` (К), `proton_sample_size`, флаги. У SOLAR1 ещё `proton_v{x,y,z}_{gse,gsm}`.

**GFZ** — §3.

---

## 2. `fetch_ncei_gfz.py` — только NCEI SEISS + GFZ

Строго:

- [NCEI GOES-R SEISS](https://www.ncei.noaa.gov/products/goes-r-space-environment-in-situ)
- [GFZ Kp / Hp](https://kp.gfz.de/en/data)

Нет SWPC, EXIS, MAG, RTSW, Dst.

```bash
.venv/bin/python fetch_ncei_gfz.py                 # 3 суток (архив чаще есть)
.venv/bin/python fetch_ncei_gfz.py --days 7
.venv/bin/python fetch_ncei_gfz.py --hours 36 --mpsl --step 30
.venv/bin/python fetch_ncei_gfz.py --no-csv
```

Выход: `output/ncei_gfz.json`, `output/ncei_gfz.csv`.  
Берёт **все** timed-переменные из NetCDF (потоки, ошибки, DQF, дозы, кватернионы…). Массивы в CSV разворачиваются индексами `_0_1_…`.

L2 — официальные 5-min average, затем среднее в шаг CSV. L1b — сырой калиброванный поток, сразу бин в 30 мин.

### Префиксы CSV

| Префикс | Файл NCEI |
|---|---|
| `ncei_mpsh_` | L2 `sci_mpsh-l2-avg5m_*` |
| `ncei_sgps_` | L2 `sci_sgps-l2-avg5m_*` |
| `l1b_mpsh_` | L1b `ops_seis-l1b-mpsh_*` |
| `l1b_sgps_` | L1b `ops_seis-l1b-sgps_*` |
| `l1b_ehis_` | L1b `ops_seis-l1b-ehis_*` |
| `l1b_mpsl_` | L1b MPS-LO, только `--mpsl` |
| `gfz_` | §3 |

Оси после имени переменной: MPS-HI `direction` 0–4 = 5 телескопов, затем канал энергии. SGPS `sensor_units` 0–1 = два блока (восток/запад), затем канал. EHIS: энергия 0–4, у Be–Cu ещё элемент 0–25.

### L2 MPS-HI (`ncei_mpsh_`)

Единицы потоков: электроны/протоны `(см² ср с кэВ)⁻¹`, интеграл без `/кэВ`.

| Переменная | Суть |
|---|---|
| `AvgDiffElectronFlux` | дифф. электроны, 5-min |
| `AvgIntElectronFlux` | интеграл электронов ≥2 МэВ |
| `AvgDiffProtonFlux` | дифф. протоны MPS-HI |
| `*Observed` / `*Uncert` | без коррекции / погрешность |
| `*ValidL1bSamplesInAvg` | сколько 1-с точек в среднем |
| `*DQFdtcSum` `*DQFoobSum` `*DQFerrSum` `*DQFlosSum` | суммы DQF |
| `yaw_flip_flag` | разворот КА |
| `L1bRecordsInAvg` | число L1b в среднем |

Энергии каналов — в JSON `seiss.mpsh.ncei.static` (`*Energy*`), в CSV не дублируются каждый шаг.

### L2 SGPS (`ncei_sgps_`)

| Переменная | Суть |
|---|---|
| `AvgDiffProtonFlux` | P1…P10, `(см² ср кэВ с)⁻¹` |
| `AvgIntProtonFlux` | P11 ≥500 МэВ, `(см² ср с)⁻¹` |
| `AvgDiffAlphaFlux` | A1…A11 |
| `*Observed` / `*Uncert` | сырой / ошибка |
| `DiffValidL1bSamplesInAvg` `IntValidL1bSamplesInAvg` | число валидных 1-с |
| `DiffDQF*` `IntDQF*` | суммы DQF (dtc, oob, err, los) |
| `*IgnoredL1bDQFs` | битовая маска отброшенных DQF |
| `yaw_flip_flag` `L1bRecordsInAvg` | как у MPS-HI |

Границы энергий: `DiffProtonLower/Upper/EffectiveEnergy`, `DiffAlpha*`, `IntegralProtonEffectiveEnergy` — в `static`.

### L1b MPS-HI (`l1b_mpsh_`)

Натив ~1 с, в CSV среднее за шаг. Форма `(телескоп, энергия)`.

| Переменная | Суть |
|---|---|
| `DiffElectronFluxes` | дифф. e⁻, 10 каналов |
| `IntgElectronFluxes` | интеграл e⁻ >2 МэВ |
| `DiffProtonFluxes` | дифф. p, 11 каналов |
| `*Uncertainties` | ошибка потока |
| `*DQFs` | качество |
| `Dos{1,2}_{Hi,Lo}LetDose` | доза, сГр/с |
| `Dos*_Dqf` | качество дозы |
| `yaw_flip_flag` `eclipse_flag` | ориентация / затмение |
| `quaternion_Q0…Q3` | ориентация |
| `ECEF_X/Y/Z` | положение, м |

### L1b SGPS (`l1b_sgps_`)

Те же идеи, 2 сенсорных блока: дифф./интегральные протоны и альфа, uncertainties, DQF, служебные флаги/ориентация.

### L1b EHIS (`l1b_ehis_`)

Потоки **см⁻² ср⁻¹ с⁻¹ (МэВ/нуклон)⁻¹**, 5 лог. полос ~10–200 МэВ/н.

| Переменная | Суть |
|---|---|
| `H5MinuteDifferentialFluxes` | водород |
| `He5MinuteDifferentialFluxes` | гелий |
| `CNO5MinuteDifferentialFluxes` | C+N+O |
| `NeS5MinuteDifferentialFluxes` | Ne–S |
| `ClNi5MinuteDifferentialFluxes` | Cl–Ni |
| `BeCu5MinuteDifferentialFluxes` | Be…Cu, 26 элементов |
| `*StatErrorsBounds` | стат. ошибка [low, high] |
| `*EnergyBounds` | границы полосы, МэВ |
| `*FluxInstErrors` | инструмент. ошибка |
| `*FluxDQFs` | качество |
| `Overall_Validity_Flag` `HFR_Flag` `IFC_Flag` | валидность / высокий поток / калибровка |
| `yaw_flip_flag` `eclipse_flag` `ECEF_*` `quaternion_*` | как у MPS-HI |

### L1b MPS-LO (`l1b_mpsl_`, `--mpsl`)

Низкие энергии ионов/электронов, полный набор переменных файла NCEI.

Пропуски дней — `missing_files` в JSON.

---

## 3. GFZ (`gfz_*`) — оба скрипта

API: `https://kp.gfz.de/app/json/`. Время — **начало** интервала индекса.

| Колонка | Интервал | Суть |
|---|---|---|
| `gfz_Hp30` | 30 мин | Hpo, шкала как Kp (0…9) |
| `gfz_ap30` | 30 мин | линейный эквивалент, нТл |
| `gfz_Hp60` | 60 мин | часовой Hpo |
| `gfz_ap60` | 60 мин | линейный, нТл |
| `gfz_Kp` | 3 ч | классический Kp |
| `gfz_Kp_status` | 3 ч | статус (nowcast / def) |
| `gfz_ap` | 3 ч | ap, нТл |
| `gfz_ap_status` | 3 ч | статус |
| `gfz_Ap` | сутки | суточный Ap |
| `gfz_Cp` | сутки | Cp |
| `gfz_C9` | сутки | C9 |
| `gfz_SN` | сутки | число Вольфа |
| `gfz_Fobs` | сутки | F10.7 observed, sfu |
| `gfz_Fadj` | сутки | F10.7 1 а.е., sfu |

Суточные индексы за короткое окно часто пустые.

---

## Кэш

NetCDF: `cache/goes{18,19}/…`. Повторный запуск не качает существующие файлы. SWPC JSON кэшируется ~4 мин.

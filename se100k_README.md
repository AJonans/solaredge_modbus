````markdown
# SE100K Site Control

`se100k_site_control.py` is a command-line tool for monitoring and controlling a SolarEdge SE100K multi-inverter site over local Modbus TCP.

It is intended for sites where one SolarEdge inverter is configured as the communication leader, one SolarEdge meter is connected to the leader, and one or more follower inverters are connected behind it. The script reads inverter production, site import/export, estimated building consumption, phase-level meter values, lifetime PV production, and SolarEdge export-control state. It can also change the site export limit or enable SolarEdge Negative Site Limit / minimum-import mode.

The script imports the local Modbus helper library as:

```python
import SE100K_modbus as solaredge_modbus
````

The current script includes JSON output, watch mode, export-limit control, minimum-import mode, lifetime energy aggregation, and leader power-control setup. 

## Features

* Reads all selected inverter units through the leader inverter.
* Reads PV production power per inverter and total site PV production.
* Reads inverter lifetime AC energy and calculates total site lifetime production.
* Reads the SolarEdge meter connected to the leader inverter.
* Calculates:

  * PV production total
  * Grid import
  * Grid export
  * Estimated building consumption
  * Phase-level grid import/export
  * Estimated phase-level building consumption
* Supports JSON output for integration with other systems.
* Supports continuous watch mode.
* Controls SolarEdge site export limitation:

  * Positive `--set-export-limit`: normal export limit
  * Zero `--set-export-limit`: zero export
  * Negative `--set-export-limit`: Negative Site Limit / minimum import mode
* Optional one-time setup of Advanced Power Control + RRCR mode on the leader.

## Requirements

* Python 3.8 or newer recommended.
* `pymodbus` compatible with your `SE100K_modbus` library.
* SolarEdge Modbus TCP enabled on the leader inverter.
* Network access to the leader inverter.
* Only one active Modbus TCP client should communicate with the SolarEdge leader at a time.

Typical SolarEdge Modbus TCP port:

```text
1502
```

## Site topology

Example topology:

```text
Site
├── Leader inverter, Modbus unit 1, IP xxx.xxx.xxx.xxx
├── Follower inverter, Modbus unit 2
├── Follower inverter, Modbus unit 3
├── Follower inverter, Modbus unit 4
├── Follower inverter, Modbus unit 5
└── SolarEdge meter connected to leader
```

Default script values:

```python
DEFAULT_HOST = "xxx.xxx.xxx.xxx"
DEFAULT_PORT = 1502
DEFAULT_UNITS = "1,2,3,4,5"
DEFAULT_LEADER_UNIT = 1
DEFAULT_FULL_EXPORT_LIMIT_W = 499000.0
```

Edit these defaults in the script, or pass values through command-line parameters.

## Basic usage

Read one site:

```bash
python se100k_site_control.py \
  --host 172.16.2.85 \
  --units 1,2,3,4,5 \
  --leader-unit 1
```

Read with debug output:

```bash
python se100k_site_control.py \
  --host 172.16.2.85 \
  --units 1,2,3,4,5 \
  --leader-unit 1 \
  --debug
```

Read as JSON:

```bash
python se100k_site_control.py \
  --host 172.16.2.85 \
  --units 1,2,3,4,5 \
  --leader-unit 1 \
  --json
```

Watch every 30 seconds:

```bash
python se100k_site_control.py \
  --host 172.16.2.85 \
  --units 1,2,3,4,5 \
  --leader-unit 1 \
  --watch 30
```

## Two-site examples

Site with five inverters:

```bash
python se100k_site_control.py \
  --host 172.16.2.85 \
  --units 1,2,3,4,5 \
  --leader-unit 1
```

Site with two inverters:

```bash
python se100k_site_control.py \
  --host 172.16.2.81 \
  --units 1,2 \
  --leader-unit 1
```

## Meter sign

Use `--meter-sign` to tell the script how to interpret meter power.

Default:

```bash
--meter-sign positive_import
```

This means:

```text
+W = importing from grid
-W = exporting to grid
```

Alternative:

```bash
--meter-sign positive_export
```

This means:

```text
+W = exporting to grid
-W = importing from grid
```

Check this carefully during commissioning. If the site is clearly exporting but the script reports import, switch the meter sign option.

## Export control

The script no longer uses direct inverter `active_power_limit` for control, because SolarEdge leader/export-control logic can overwrite it. Instead, all site control is done through SolarEdge Export Control on the leader inverter.

### Restore normal export

```bash
python se100k_site_control.py \
  --host 172.16.2.85 \
  --units 1,2,3,4,5 \
  --leader-unit 1 \
  --set-export-limit 499000
```

This sets:

```text
Export Control Mode = 1
Negative Site Limit = disabled
Export Limit = 499000 W
Minimum Import = 0 W
```

### Block export

```bash
python se100k_site_control.py \
  --host 172.16.2.85 \
  --units 1,2,3,4,5 \
  --leader-unit 1 \
  --set-export-limit 0
```

This sets:

```text
Export Control Mode = 1
Negative Site Limit = disabled
Export Limit = 0 W
Minimum Import = 0 W
```

### Minimum import mode

Use a negative value to enable SolarEdge Negative Site Limit mode:

```bash
python se100k_site_control.py \
  --host 172.16.2.85 \
  --units 1,2,3,4,5 \
  --leader-unit 1 \
  --set-export-limit -10000
```

This sets:

```text
Export Control Mode = 2049
Negative Site Limit = enabled
Export Limit = 0 W
Minimum Import = 10000 W
```

Use this mode when import electricity price is negative and you want the site to keep importing at least a defined amount from the grid instead of producing PV that reduces import.

## Control logic for negative prices

Recommended external logic:

```text
If export sell price < 0:
    --set-export-limit 0

If import buy price < 0:
    --set-export-limit -10000
    # or another required minimum import value

If prices are normal:
    --set-export-limit 499000
```

The script itself does not fetch Nord Pool prices. It is designed to be called by another scheduler, energy-price script, cron job, Home Assistant automation, or SCADA/EMS process.

## JSON output

Use:

```bash
python se100k_site_control.py \
  --host 172.16.2.85 \
  --units 1,2,3,4,5 \
  --leader-unit 1 \
  --json
```

Important JSON fields:

```json
{
  "ok": true,
  "connection": {
    "host": "172.16.2.85",
    "port": 1502,
    "units": [1, 2, 3, 4, 5],
    "leader_unit": 1,
    "connected": true
  },
  "snapshot": {
    "total_pv_w": 135726.0,
    "site_total_energy_wh": 1234567890.0,
    "meter_power_w": -206410.0,
    "import_w": 0.0,
    "export_w": 206410.0,
    "building_w": 0.0
  }
}
```

### `site_total_energy_wh`

`site_total_energy_wh` is the summed lifetime AC energy production from all selected inverter units.

It is calculated from each inverter:

```text
energy_total × 10^energy_total_scale
```

The value is in Wh.

## Output explanation

### Inverters section

Example:

```text
Unit 1: PV=27.085 kW, Lifetime=123,456.789 kWh, APL=100%, status=4, RRCR=0
```

Fields:

```text
PV        Current AC production power
Lifetime  Lifetime AC energy production
APL       Active Power Limit register value
status    SolarEdge inverter status
RRCR      RRCR state feedback
```

Common inverter statuses:

```text
4 = Producing
5 = Producing (Throttled)
```

`Producing (Throttled)` may appear when export limitation or minimum-import mode is curtailing PV production.

### Site energy section

Example:

```text
Site lifetime production: 1,234,567.890 kWh (1,234,567,890.0 Wh)
JSON key: site_total_energy_wh contains Wh
```

This is summed across all selected inverter units.

### Site power flow section

Example:

```text
PV production total:  135.726 kW
Grid meter net power: -206.410 kW
Grid import:          0.000 kW
Grid export:          206.410 kW
Building consumption: 342.136 kW
```

Building consumption is calculated as:

```text
building = PV production + grid import - grid export
```

### Phase values

The script prints meter phase values:

```text
L1, L2, L3 grid net/import/export
```

PV per phase is estimated as:

```text
total PV / 3
```

This is an estimate because the script reads total inverter AC power, not real per-phase PV power from each inverter.

## One-time power-control setup

Run this only if needed:

```bash
python se100k_site_control.py \
  --host 172.16.2.85 \
  --units 1,2,3,4,5 \
  --leader-unit 1 \
  --setup-power-control
```

This writes on the leader inverter:

```text
Advanced Power Control = 1
Reactive Power Config = 4
Commit Power Control Settings
```

Use carefully. Normally this is configured by the installer.

## Docker usage

For old Ubuntu servers, Docker is often easier than using the system Python.

Example `Dockerfile`:

```dockerfile
FROM python:3.10-slim

WORKDIR /app

COPY . /app

RUN pip install --no-cache-dir --progress-bar off "pymodbus==2.5.3"

CMD ["python", "se100k_site_control.py", "--help"]
```

Build:

```bash
docker build -t se-site-control .
```

Run:

```bash
docker run --rm --network host se-site-control \
  python se100k_site_control.py \
  --host 172.16.2.85 \
  --port 1502 \
  --units 1,2,3,4,5 \
  --leader-unit 1 \
  --timeout 15 \
  --debug
```

## Important safety notes

Changing SolarEdge export-control settings can affect grid-code compliance and your utility connection agreement.

Use this script only if you are authorized to control the site. For commercial sites, confirm settings with the installer or grid operator.

Do not run multiple Modbus TCP clients against the same SolarEdge leader at the same time. Stop Home Assistant, other scripts, Modbus scanners, or EMS clients while testing.

## Troubleshooting

### Script connects but no values appear

Possible causes:

* Another Modbus client is already connected.
* Wrong Modbus unit IDs.
* Wrong leader IP address.
* Modbus TCP not enabled on the leader inverter.
* Incompatible `pymodbus` version.

### Commit command times out

SolarEdge commit can take several seconds. Use:

```bash
--timeout 15
```

The script waits and reads settings back after commit.

### Import/export are reversed

Switch:

```bash
--meter-sign positive_import
```

to:

```bash
--meter-sign positive_export
```

or the opposite.

### Active Power Limit returns to 100%

This is expected on sites where the SolarEdge leader/export-control loop manages follower inverter limits. Use `--set-export-limit` instead of writing inverter active power limits directly.

## Command reference

```text
--host HOST
    Modbus TCP host/IP of leader inverter.

--port PORT
    Modbus TCP port. Usually 1502.

--timeout SECONDS
    Modbus timeout. Recommended: 15.

--units 1,2,3
    Comma-separated inverter unit IDs.

--leader-unit N
    Modbus unit ID of leader inverter.

--meter-index 1
    Which detected meter to read from leader.

--meter-sign positive_import|positive_export
    Defines meter power sign convention.

--set-export-limit W
    Positive: normal export limit.
    Zero: zero export.
    Negative: minimum import / Negative Site Limit mode.

--setup-power-control
    One-time setup of Advanced Power Control + RRCR on leader.

--watch SECONDS
    Repeat reading every N seconds.

--debug
    Print raw register values.

--json
    Output machine-readable JSON.
```

## Examples

Read site:

```bash
python se100k_site_control.py --host 172.16.2.85 --units 1,2,3,4,5 --leader-unit 1
```

JSON read:

```bash
python se100k_site_control.py --host 172.16.2.85 --units 1,2,3,4,5 --leader-unit 1 --json
```

Zero export:

```bash
python se100k_site_control.py --host 172.16.2.85 --units 1,2,3,4,5 --leader-unit 1 --set-export-limit 0
```

Restore 499 kW export:

```bash
python se100k_site_control.py --host 172.16.2.85 --units 1,2,3,4,5 --leader-unit 1 --set-export-limit 499000
```

Minimum import 10 kW:

```bash
python se100k_site_control.py --host 172.16.2.85 --units 1,2,3,4,5 --leader-unit 1 --set-export-limit -10000
```

```
```

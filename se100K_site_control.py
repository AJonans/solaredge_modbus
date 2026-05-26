#!/usr/bin/env python3

import argparse
import contextlib
import io
import json
import sys
import time
from typing import Optional, Dict, Any, List, Tuple

import SE100K_modbus as solaredge_modbus


DEFAULT_HOST = "172.16.2.85"
DEFAULT_PORT = 1502

# Your topology defaults
DEFAULT_UNITS = "1,2,3,4,5"
DEFAULT_LEADER_UNIT = 1

# SolarEdge power-control setup values
ADVANCED_POWER_CONTROL_ENABLED = 1
REACTIVE_POWER_CONFIG_RRCR = 4

# Export Control Mode bit values
EXPORT_CONTROL_MODE_DISABLED = 0
EXPORT_CONTROL_MODE_DIRECT = 1
EXPORT_CONTROL_MODE_NEGATIVE_SITE_LIMIT_BIT = 2048
EXPORT_CONTROL_MODE_DIRECT_WITH_NEGATIVE_SITE_LIMIT = (
    EXPORT_CONTROL_MODE_DIRECT + EXPORT_CONTROL_MODE_NEGATIVE_SITE_LIMIT_BIT
)

# Export Control Limit Mode
EXPORT_CONTROL_LIMIT_MODE_TOTAL = 0

# Your normal site export limit
DEFAULT_FULL_EXPORT_LIMIT_W = 499000.0


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Read SolarEdge multi-inverter site power flow, site lifetime energy, "
            "and control export / minimum import using the leader inverter."
        )
    )

    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="Modbus TCP host/IP of leader inverter",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="Modbus TCP port, usually 1502",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15,
        help="Connection timeout in seconds",
    )

    parser.add_argument(
        "--units",
        default=DEFAULT_UNITS,
        help="Comma-separated Modbus unit IDs for all inverters, example: 1,2,3,4,5",
    )
    parser.add_argument(
        "--leader-unit",
        type=int,
        default=DEFAULT_LEADER_UNIT,
        help="Leader inverter Modbus unit ID. Your setup uses 1.",
    )

    parser.add_argument(
        "--meter-index",
        type=int,
        default=1,
        choices=[1, 2, 3],
        help="Which detected meter to use from leader. Usually 1.",
    )

    parser.add_argument(
        "--meter-sign",
        choices=["positive_import", "positive_export"],
        default="positive_import",
        help=(
            "How to interpret meter power. "
            "positive_import means +W = importing from grid, -W = exporting to grid. "
            "positive_export means +W = exporting to grid, -W = importing from grid."
        ),
    )

    parser.add_argument(
        "--set-export-limit",
        type=float,
        metavar="W",
        help=(
            "Set leader site control target in watts. "
            "Positive value: Direct Export Limitation mode, Export Limit = value W, "
            "Minimum Import = 0. "
            "Zero: Direct Export Limitation mode, Export Limit = 0 W, Minimum Import = 0. "
            "Negative value: Direct Export Limitation + Negative Site Limit mode "
            "(mode 2049), Export Limit = 0 W, Minimum Import = abs(value) W. "
            "Examples: "
            "--set-export-limit 499000 restores normal export limit; "
            "--set-export-limit 0 blocks export; "
            "--set-export-limit -10000 requires at least 10 kW grid import."
        ),
    )

    parser.add_argument(
        "--setup-power-control",
        action="store_true",
        help=(
            "Enable Advanced Power Control and ReactivePwrConfig=RRCR on leader, then commit. "
            "Use carefully; normally one-time setup."
        ),
    )

    parser.add_argument(
        "--watch",
        type=int,
        metavar="SECONDS",
        help="Repeat reading every N seconds.",
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print extra raw values.",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Output all results as JSON.",
    )

    return parser.parse_args()


def parse_units(value: str) -> List[int]:
    units = []

    for item in value.split(","):
        item = item.strip()
        if not item:
            continue

        unit = int(item)

        if unit < 1 or unit > 247:
            raise ValueError(f"Invalid Modbus unit ID: {unit}")

        if unit not in units:
            units.append(unit)

    if not units:
        raise ValueError("At least one unit is required")

    return units


def check_write(result, description: str):
    if result is None:
        raise RuntimeError(f"{description}: no Modbus response")

    if hasattr(result, "isError") and result.isError():
        raise RuntimeError(f"{description}: Modbus error: {result}")

    return result


def safe_read(device, key: str) -> Optional[Any]:
    try:
        value = device.read(key)[key]

        if value is False:
            return None

        return value

    except Exception:
        return None


def safe_read_all(device) -> Dict[str, Any]:
    try:
        return device.read_all()

    except Exception:
        return {}


def scaled_value(values: Dict[str, Any], key: str, scale_key: str) -> Optional[float]:
    value = values.get(key)
    scale = values.get(scale_key)

    if value is None or scale is None or value is False or scale is False:
        return None

    return float(value) * (10 ** int(scale))


def fmt_w(value: Optional[float]) -> str:
    if value is None:
        return "unavailable"

    return f"{value:,.1f} W"


def fmt_kw(value: Optional[float]) -> str:
    if value is None:
        return "unavailable"

    return f"{value / 1000:,.3f} kW"


def fmt_wh(value: Optional[float]) -> str:
    if value is None:
        return "unavailable"

    return f"{value:,.1f} Wh"


def fmt_kwh(value: Optional[float]) -> str:
    if value is None:
        return "unavailable"

    return f"{value / 1000:,.3f} kWh"


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value

    return str(value)


def print_json(payload: Dict[str, Any]):
    print(json.dumps(json_safe(payload), indent=2, sort_keys=True))


@contextlib.contextmanager
def capture_output(enabled: bool, payload: Dict[str, Any]):
    if not enabled:
        yield
        return

    stdout = io.StringIO()
    stderr = io.StringIO()

    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            yield
    finally:
        captured = {}
        stdout_value = stdout.getvalue().strip()
        stderr_value = stderr.getvalue().strip()

        if stdout_value:
            captured["stdout"] = stdout_value.splitlines()

        if stderr_value:
            captured["stderr"] = stderr_value.splitlines()

        if captured:
            payload.setdefault("captured_output", []).append(captured)


def build_inverters(
    args,
    units: List[int],
) -> Tuple[solaredge_modbus.Inverter, Dict[int, solaredge_modbus.Inverter]]:
    leader = solaredge_modbus.Inverter(
        host=args.host,
        port=args.port,
        timeout=args.timeout,
        unit=args.leader_unit,
    )

    inverters = {}

    for unit in units:
        if unit == args.leader_unit:
            inverters[unit] = leader
        else:
            inverters[unit] = solaredge_modbus.Inverter(parent=leader, unit=unit)

    return leader, inverters


def get_meter_from_leader(leader, meter_index: int):
    meters = leader.meters()

    if not meters:
        return None, None

    wanted_name = f"Meter{meter_index}"

    if wanted_name in meters:
        return wanted_name, meters[wanted_name]

    return next(iter(meters.items()))


def phase_powers_from_meter(
    meter_values: Dict[str, Any],
    meter_sign: str,
) -> Dict[str, Dict[str, Optional[float]]]:
    phases = {}

    for phase, key in {
        "l1": "l1_power",
        "l2": "l2_power",
        "l3": "l3_power",
    }.items():
        net_w = scaled_value(meter_values, key, "power_scale")

        if net_w is None:
            phases[phase] = {
                "net_w": None,
                "import_w": None,
                "export_w": None,
            }
            continue

        if meter_sign == "positive_import":
            import_w = max(net_w, 0.0)
            export_w = max(-net_w, 0.0)
        else:
            export_w = max(net_w, 0.0)
            import_w = max(-net_w, 0.0)

        phases[phase] = {
            "net_w": net_w,
            "import_w": import_w,
            "export_w": export_w,
        }

    return phases


def estimate_pv_phases_balanced(
    total_pv_w: Optional[float],
) -> Dict[str, Optional[float]]:
    if total_pv_w is None:
        return {
            "l1": None,
            "l2": None,
            "l3": None,
        }

    phase_w = total_pv_w / 3.0

    return {
        "l1": phase_w,
        "l2": phase_w,
        "l3": phase_w,
    }


def calculate_building_phases(
    pv_phases: Dict[str, Optional[float]],
    grid_phases: Dict[str, Dict[str, Optional[float]]],
) -> Dict[str, Optional[float]]:
    building = {}

    for phase in ["l1", "l2", "l3"]:
        pv_w = pv_phases.get(phase)
        import_w = grid_phases.get(phase, {}).get("import_w")
        export_w = grid_phases.get(phase, {}).get("export_w")

        if pv_w is None or import_w is None or export_w is None:
            building[phase] = None
        else:
            building[phase] = pv_w + import_w - export_w

    return building


def read_inverter_power(unit: int, inverter) -> Dict[str, Any]:
    values = safe_read_all(inverter)
    values = dict(values)

    advanced_power_control_enable = safe_read(
        inverter,
        "advanced_power_control_enable",
    )

    if advanced_power_control_enable is not None:
        values["advanced_power_control_enable"] = advanced_power_control_enable

    power_w = scaled_value(values, "power_ac", "power_ac_scale")
    energy_total_wh = scaled_value(values, "energy_total", "energy_total_scale")

    return {
        "unit": unit,
        "values": values,

        "power_w": power_w,
        "raw_power": values.get("power_ac"),
        "raw_power_scale": values.get("power_ac_scale"),

        "energy_total_wh": energy_total_wh,
        "raw_energy_total": values.get("energy_total"),
        "raw_energy_total_scale": values.get("energy_total_scale"),

        "status": values.get("status"),
        "active_power_limit": safe_read(inverter, "active_power_limit"),
        "advanced_power_control_enable": advanced_power_control_enable,
        "rrcr_state": safe_read(inverter, "rrcr_state"),
    }


def read_site_snapshot(args, inverters: Dict[int, Any], leader) -> Dict[str, Any]:
    inverter_rows = []

    for unit, inverter in sorted(inverters.items()):
        inverter_rows.append(read_inverter_power(unit, inverter))

    available_pv_values = [
        row["power_w"]
        for row in inverter_rows
        if row["power_w"] is not None
    ]

    available_energy_values = [
        row["energy_total_wh"]
        for row in inverter_rows
        if row["energy_total_wh"] is not None
    ]

    total_pv_w = sum(available_pv_values) if available_pv_values else None

    # Requested key name. Value is Wh, not W.
    site_total_energy_wh = (
        sum(available_energy_values)
        if available_energy_values
        else None
    )

    meter_name, meter = get_meter_from_leader(leader, args.meter_index)

    meter_values = {}
    meter_power_w = None

    if meter is not None:
        meter_values = safe_read_all(meter)
        meter_power_w = scaled_value(meter_values, "power", "power_scale")

    if meter_power_w is None:
        import_w = None
        export_w = None
    else:
        if args.meter_sign == "positive_import":
            import_w = max(meter_power_w, 0.0)
            export_w = max(-meter_power_w, 0.0)
        else:
            export_w = max(meter_power_w, 0.0)
            import_w = max(-meter_power_w, 0.0)

    if total_pv_w is None or import_w is None or export_w is None:
        building_w = None
    else:
        building_w = total_pv_w + import_w - export_w

    pv_phases_w = estimate_pv_phases_balanced(total_pv_w)

    if meter_values:
        grid_phases = phase_powers_from_meter(meter_values, args.meter_sign)
    else:
        grid_phases = {
            "l1": {"net_w": None, "import_w": None, "export_w": None},
            "l2": {"net_w": None, "import_w": None, "export_w": None},
            "l3": {"net_w": None, "import_w": None, "export_w": None},
        }

    building_phases_w = calculate_building_phases(pv_phases_w, grid_phases)

    return {
        "inverters": inverter_rows,

        "total_pv_w": total_pv_w,
        "site_total_energy_wh": site_total_energy_wh,

        "meter_name": meter_name,
        "meter_values": meter_values,
        "meter_power_w": meter_power_w,
        "import_w": import_w,
        "export_w": export_w,
        "building_w": building_w,

        "pv_phases_w": pv_phases_w,
        "grid_phases": grid_phases,
        "building_phases_w": building_phases_w,

        "leader_active_power_limit": safe_read(leader, "active_power_limit"),
        "leader_export_control_mode": safe_read(leader, "export_control_mode"),
        "leader_export_control_limit_mode": safe_read(
            leader,
            "export_control_limit_mode",
        ),
        "leader_export_control_site_limit": safe_read(
            leader,
            "export_control_site_limit",
        ),
        "leader_advanced_power_control_enable": safe_read(
            leader,
            "advanced_power_control_enable",
        ),
        "leader_reactive_power_config": safe_read(
            leader,
            "reactive_power_config",
        ),
    }


def print_snapshot(snapshot: Dict[str, Any], debug: bool = False):
    print()
    print("=== Inverters ===")

    for row in snapshot["inverters"]:
        print(
            f"Unit {row['unit']}: "
            f"PV={fmt_kw(row['power_w'])}, "
            f"Lifetime={fmt_kwh(row['energy_total_wh'])}, "
            f"APL={row['active_power_limit']}%, "
            f"status={row['status']}, "
            f"RRCR={row['rrcr_state']}"
        )

        if debug:
            print(
                f"  raw power={row['raw_power']}, "
                f"power scale={row['raw_power_scale']}, "
                f"raw energy={row['raw_energy_total']}, "
                f"energy scale={row['raw_energy_total_scale']}"
            )

    print()
    print("=== Site energy ===")
    print(
        "Site lifetime production: "
        f"{fmt_kwh(snapshot['site_total_energy_wh'])} "
        f"({fmt_wh(snapshot['site_total_energy_wh'])})"
    )
    print("JSON key: site_total_energy_wh contains Wh")

    print()
    print("=== Site power flow ===")
    print(f"PV production total:  {fmt_kw(snapshot['total_pv_w'])}")
    print(f"Grid meter:           {snapshot['meter_name'] or 'not found'}")
    print(f"Grid meter net power: {fmt_kw(snapshot['meter_power_w'])}")
    print(f"Grid import:          {fmt_kw(snapshot['import_w'])}")
    print(f"Grid export:          {fmt_kw(snapshot['export_w'])}")
    print(f"Building consumption: {fmt_kw(snapshot['building_w'])}")

    print()
    print("=== Site power flow by phase ===")
    print("Note: PV phase values are estimated as balanced total PV / 3.")
    print(
        f"{'Phase':<6}"
        f"{'PV':>14}"
        f"{'Grid net':>14}"
        f"{'Import':>14}"
        f"{'Export':>14}"
        f"{'Building':>14}"
    )

    for phase in ["l1", "l2", "l3"]:
        pv_w = snapshot["pv_phases_w"].get(phase)
        grid_net_w = snapshot["grid_phases"].get(phase, {}).get("net_w")
        import_w = snapshot["grid_phases"].get(phase, {}).get("import_w")
        export_w = snapshot["grid_phases"].get(phase, {}).get("export_w")
        building_w = snapshot["building_phases_w"].get(phase)

        print(
            f"{phase.upper():<6}"
            f"{fmt_kw(pv_w):>14}"
            f"{fmt_kw(grid_net_w):>14}"
            f"{fmt_kw(import_w):>14}"
            f"{fmt_kw(export_w):>14}"
            f"{fmt_kw(building_w):>14}"
        )

    if debug:
        print()
        print("=== Raw meter phase values ===")
        meter_values = snapshot.get("meter_values") or {}

        for key in [
            "power",
            "l1_power",
            "l2_power",
            "l3_power",
            "power_scale",
        ]:
            print(f"{key}: {meter_values.get(key)}")

    print()
    print("=== Leader control state ===")
    print(f"Advanced Power Control: {snapshot['leader_advanced_power_control_enable']}")
    print(f"Reactive Power Config:  {snapshot['leader_reactive_power_config']}")
    print(f"Active Power Limit:     {snapshot['leader_active_power_limit']}%")
    print(f"Export Control Mode:    {snapshot['leader_export_control_mode']}")
    print(f"Export Limit Mode:      {snapshot['leader_export_control_limit_mode']}")

    export_mode = snapshot["leader_export_control_mode"]
    site_limit = snapshot["leader_export_control_site_limit"]

    if export_mode is not None and (
        export_mode & EXPORT_CONTROL_MODE_NEGATIVE_SITE_LIMIT_BIT
    ):
        print("Negative Site Limit:    enabled")
        print(f"Minimum Import:         {fmt_kw(site_limit)}")
        print("Export Site Limit:      0.000 kW")
    else:
        print("Negative Site Limit:    disabled")
        print("Minimum Import:         0.000 kW")
        print(f"Export Site Limit:      {fmt_kw(site_limit)}")


def setup_power_control(leader, leader_unit: int, quiet: bool = False) -> Dict[str, Any]:
    if not quiet:
        print()
        print("Setting up leader power control...")

    check_write(
        leader.write("advanced_power_control_enable", ADVANCED_POWER_CONTROL_ENABLED),
        f"Unit {leader_unit} write Advanced Power Control Enable",
    )

    check_write(
        leader.write("reactive_power_config", REACTIVE_POWER_CONFIG_RRCR),
        f"Unit {leader_unit} write Reactive Power Config RRCR",
    )

    time.sleep(0.5)

    check_write(
        leader.write("commit_power_control_settings", 1),
        f"Unit {leader_unit} commit power-control settings",
    )

    operation = {
        "operation": "setup_power_control",
        "leader_unit": leader_unit,
        "advanced_power_control_enable": ADVANCED_POWER_CONTROL_ENABLED,
        "reactive_power_config": REACTIVE_POWER_CONFIG_RRCR,
        "committed": True,
    }

    if not quiet:
        print(f"Unit {leader_unit}: committed Advanced Power Control + RRCR mode")

    return operation


def set_export_control_target(
    leader,
    leader_unit: int,
    requested_limit_w: float,
    quiet: bool = False,
) -> Dict[str, Any]:
    """
    requested_limit_w > 0:
        Export Control Mode = 1
        Export Control Site Limit = requested_limit_w

    requested_limit_w == 0:
        Export Control Mode = 1
        Export Control Site Limit = 0

    requested_limit_w < 0:
        Export Control Mode = 2049
        Export Control Site Limit = abs(requested_limit_w)

    Note:
        In SolarEdge Negative Site Limit mode, this value is used as the
        required minimum import target. We print it as Minimum Import.
    """
    if not quiet:
        print()

    if requested_limit_w < 0:
        mode = EXPORT_CONTROL_MODE_DIRECT_WITH_NEGATIVE_SITE_LIMIT
        site_limit_w = abs(float(requested_limit_w))
        action = (
            f"Minimum Import mode: minimum import = {site_limit_w:,.1f} W, "
            f"export limit = 0 W"
        )
    else:
        mode = EXPORT_CONTROL_MODE_DIRECT
        site_limit_w = float(requested_limit_w)
        action = (
            f"Direct Export Limitation mode: export limit = {site_limit_w:,.1f} W, "
            f"minimum import = 0 W"
        )

    if not quiet:
        print(f"Setting leader/site control: {action}")

    check_write(
        leader.write("export_control_mode", mode),
        f"Unit {leader_unit} write Export Control Mode",
    )

    time.sleep(0.2)

    check_write(
        leader.write("export_control_limit_mode", EXPORT_CONTROL_LIMIT_MODE_TOTAL),
        f"Unit {leader_unit} write Export Control Limit Mode = Total",
    )

    time.sleep(0.2)

    check_write(
        leader.write("export_control_site_limit", site_limit_w),
        f"Unit {leader_unit} write Export Control Site Limit",
    )

    time.sleep(0.5)

    try:
        check_write(
            leader.write("commit_power_control_settings", 1),
            f"Unit {leader_unit} commit Export Control settings",
        )
        warnings = []
    except RuntimeError as exc:
        warnings = [
            f"commit command did not return a normal response: {exc}",
            "Waiting 10 seconds and reading back settings...",
        ]

        if not quiet:
            print(f"Warning: {warnings[0]}")
            print(warnings[1])

    time.sleep(10)

    mode_readback = safe_read(leader, "export_control_mode")
    limit_mode_readback = safe_read(leader, "export_control_limit_mode")
    site_limit_readback = safe_read(leader, "export_control_site_limit")

    operation = {
        "operation": "set_export_limit",
        "leader_unit": leader_unit,
        "requested_limit_w": requested_limit_w,
        "mode": mode,
        "limit_mode": EXPORT_CONTROL_LIMIT_MODE_TOTAL,
        "site_limit_w": site_limit_w,
        "negative_site_limit": requested_limit_w < 0,
        "description": action,
        "warnings": warnings,
        "readback": {
            "mode": mode_readback,
            "limit_mode": limit_mode_readback,
            "site_limit_w": site_limit_readback,
        },
    }

    if not quiet:
        print(
            f"Readback after commit: mode={mode_readback}, "
            f"limit_mode={limit_mode_readback}, "
            f"site_limit={site_limit_readback} W"
        )

    return operation


def run_once(args, leader, inverters, print_output: bool = True):
    snapshot = read_site_snapshot(args, inverters, leader)

    if print_output:
        print_snapshot(snapshot, debug=args.debug)

    return snapshot


def main():
    try:
        args = parse_args()
        units = parse_units(args.units)

        if args.leader_unit not in units:
            raise ValueError("--leader-unit must be included in --units")

    except Exception as exc:
        print(f"Argument error: {exc}", file=sys.stderr)
        return 2

    leader, inverters = build_inverters(args, units)
    payload = {
        "ok": False,
        "connection": {
            "host": args.host,
            "port": args.port,
            "units": units,
            "leader_unit": args.leader_unit,
            "connected": False,
        },
        "operations": [],
    }

    try:
        with capture_output(args.json, payload):
            if not leader.connect():
                raise RuntimeError(f"Could not connect to {args.host}:{args.port}")

        payload["connection"]["connected"] = True

        if not args.json:
            print(
                f"Connected to {args.host}:{args.port}; "
                f"units={units}; leader={args.leader_unit}"
            )

        if args.setup_power_control:
            with capture_output(args.json, payload):
                payload["operations"].append(
                    setup_power_control(leader, args.leader_unit, quiet=args.json)
                )

        if args.set_export_limit is not None:
            with capture_output(args.json, payload):
                payload["operations"].append(
                    set_export_control_target(
                        leader,
                        args.leader_unit,
                        args.set_export_limit,
                        quiet=args.json,
                    )
                )

        if args.watch:
            if args.json:
                payload["watch_interval_seconds"] = args.watch

            while True:
                with capture_output(args.json, payload):
                    snapshot = run_once(
                        args,
                        leader,
                        inverters,
                        print_output=not args.json,
                    )

                if args.json:
                    watch_payload = dict(payload)
                    watch_payload["ok"] = True
                    watch_payload["snapshot"] = snapshot
                    print_json(watch_payload)

                time.sleep(args.watch)
        else:
            with capture_output(args.json, payload):
                payload["snapshot"] = run_once(
                    args,
                    leader,
                    inverters,
                    print_output=not args.json,
                )

            if args.json:
                payload["ok"] = True
                print_json(payload)

        return 0

    except KeyboardInterrupt:
        if args.json:
            payload["ok"] = False
            payload["error"] = "Stopped."
            print_json(payload)
        else:
            print("Stopped.")

        return 130

    except Exception as exc:
        if args.json:
            payload["ok"] = False
            payload["error"] = str(exc)
            print_json(payload)
        else:
            print(f"Error: {exc}", file=sys.stderr)

        return 1

    finally:
        leader.disconnect()


if __name__ == "__main__":
    sys.exit(main())

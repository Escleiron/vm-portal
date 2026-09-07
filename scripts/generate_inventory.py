#!/usr/bin/env python3

import sys
from pathlib import Path

import yaml


VM_FILE = Path("/var/lib/vm-portal/vms.yml")
INVENTORY_FILE = Path(
    "/opt/vm-portal/ansible/inventories/inventory.yml"
)


def load_vms():
    if not VM_FILE.exists():
        raise FileNotFoundError(
            f"VM file not found: {VM_FILE}"
        )

    with VM_FILE.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}

    return data.get("vms", [])


def generate_inventory(vms):
    hosts = {}

    for vm in vms:

        name = vm.get("name")
        ip = vm.get("ip")
        status = vm.get("status", "").upper()

        if not name or not ip:
            continue

        if status == "DELETED":
            continue

        hosts[name] = {
            "ansible_host": ip
        }

    inventory = {
        "all": {
            "children": {
                "lab": {
                    "hosts": hosts
                }
            }
        }
    }

    return inventory


def save_inventory(inventory):
    INVENTORY_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temporary_file = INVENTORY_FILE.with_suffix(".tmp")

    with temporary_file.open("w", encoding="utf-8") as file:
        yaml.safe_dump(
            inventory,
            file,
            default_flow_style=False,
            sort_keys=False
        )

    temporary_file.replace(INVENTORY_FILE)


def main():
    try:
        vms = load_vms()
        inventory = generate_inventory(vms)
        save_inventory(inventory)

        active_vms = inventory["all"]["children"]["lab"]["hosts"]

        print(
            f"Inventory generated successfully: "
            f"{len(active_vms)} VM(s)"
        )

        for name, data in active_vms.items():
            print(
                f"  {name} -> {data['ansible_host']}"
            )

        return 0

    except Exception as exc:
        print(
            f"ERROR: {exc}",
            file=sys.stderr
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())

from flask import Flask, render_template, request, redirect, url_for
from datetime import datetime
from pathlib import Path
import subprocess
import threading
import yaml
import ipaddress
import re
import os
import hmac


app = Flask(__name__)


# ============================================================
# PATHS
# ============================================================

BASE_DIR = Path("/opt/vm-portal")

LOG_DIR = Path("/var/log/vm-portal")
VM_FILE = Path("/var/lib/vm-portal/vms.yml")

PROVISION_PLAYBOOK = (
    BASE_DIR / "ansible/playbooks/provision_vm.yml"
)

DELETE_PLAYBOOK = (
    BASE_DIR / "ansible/playbooks/provision_vm.yml"
)

LIFECYCLE_PLAYBOOK = (
    BASE_DIR / "ansible/playbooks/vm_lifecycle.yml"
)

BASELINE_PLAYBOOK = (
    BASE_DIR / "ansible/playbooks/apply_linux_baseline.yml"
)

INVENTORY_FILE = (
    BASE_DIR / "ansible/inventories/inventory.yml"
)

INVENTORY_GENERATOR = (
    BASE_DIR / "scripts/generate_inventory.py"
)


LOG_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# DELETE KEY
# ============================================================

DELETE_KEY = os.environ.get("VM_DELETE_KEY")


# ============================================================
# VM DATABASE
# ============================================================

def load_vms():

    if not VM_FILE.exists():
        return []

    try:

        with open(VM_FILE, "r") as f:
            data = yaml.safe_load(f)

        if not data:
            return []

        if isinstance(data, dict):
            return data.get("vms", [])

        if isinstance(data, list):
            return data

    except Exception as exc:

        print(
            f"ERROR loading VM database: {exc}"
        )

    return []


def save_vms(vms):

    tmp_file = VM_FILE.with_suffix(".tmp")

    with open(tmp_file, "w") as f:

        yaml.safe_dump(
            {"vms": vms},
            f,
            default_flow_style=False,
            sort_keys=False
        )

    tmp_file.replace(VM_FILE)


def find_vm(name):

    for vm in load_vms():

        if vm.get("name") == name:
            return vm

    return None


def update_vm_status(
    name,
    status,
    log_filename=None
):

    vms = load_vms()

    for vm in vms:

        if vm.get("name") == name:

            vm["status"] = status

            if log_filename:
                vm["log"] = log_filename

            vm["updated"] = datetime.now().isoformat()

            break

    save_vms(vms)


def generate_inventory(log_path=None):

    try:

        result = subprocess.run(
            [str(INVENTORY_GENERATOR)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False
        )

        if log_path:

            append_log(
                log_path,
                "\n"
                "============================================================\n"
                "INVENTORY GENERATION\n"
                "============================================================\n"
                + result.stdout
            )

        return result.returncode

    except Exception as exc:

        if log_path:

            append_log(
                log_path,
                "\n"
                "============================================================\n"
                "INVENTORY GENERATION ERROR\n"
                "============================================================\n"
                f"{exc}\n"
            )

        return 1


# ============================================================
# VALIDATION
# ============================================================

def validate_vm_name(name):

    return bool(
        re.fullmatch(
            r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,62}",
            name
        )
    )


def validate_ip(ip):

    try:
        ipaddress.ip_address(ip)
        return True

    except ValueError:
        return False


# ============================================================
# LOGGING
# ============================================================

def create_log_file(
    operation,
    name,
    install_method=None
):

    timestamp = datetime.now().strftime(
        "%Y%m%d-%H%M%S"
    )

    filename = (
        f"vm-{operation.lower()}-"
        f"{name}-{timestamp}.log"
    )

    path = LOG_DIR / filename

    with open(path, "w") as f:

        f.write(
            "============================================================\n"
        )

        f.write(
            "Linux Lab Provisioner\n"
        )

        f.write(
            "============================================================\n"
        )

        f.write(
            f"Operation : {operation}\n"
        )

        f.write(
            f"VM        : {name}\n"
        )

        if install_method:

            f.write(
                f"Installation: {install_method}\n"
            )

        f.write(
            f"Start     : {datetime.now().isoformat()}\n"
        )

        f.write(
            "============================================================\n\n"
        )

    return filename, path


def append_log(path, text):

    with open(path, "a") as f:

        f.write(text)
        f.flush()


# ============================================================
# LIBVIRT
# ============================================================

def run_virsh(command):

    try:

        result = subprocess.run(
            [
                "virsh",
                "-c",
                "qemu:///system"
            ] + command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15
        )

        return (
            result.stdout.strip(),
            result.returncode
        )

    except Exception as exc:

        print(
            f"ERROR running virsh: {exc}"
        )

        return "", 1


def get_libvirt_vm_info(name):

    output, rc = run_virsh(
        [
            "dominfo",
            name
        ]
    )

    if rc != 0:
        return {}

    info = {}

    for line in output.splitlines():

        if ":" not in line:
            continue

        key, value = line.split(
            ":",
            1
        )

        info[key.strip()] = value.strip()

    return info


def get_libvirt_disks(name):

    output, rc = run_virsh(
        [
            "domblklist",
            name
        ]
    )

    if rc != 0:
        return []

    disks = []

    for line in output.splitlines():

        line = line.strip()

        if not line:
            continue

        if line.startswith("Target"):
            continue

        if line.startswith("-"):
            continue

        parts = line.split(
            None,
            1
        )

        if len(parts) != 2:
            continue

        disks.append(
            {
                "target": parts[0],
                "source": parts[1].strip()
            }
        )

    return disks


def get_libvirt_interfaces(name):

    output, rc = run_virsh(
        [
            "domiflist",
            name
        ]
    )

    if rc != 0:
        return []

    interfaces = []

    for line in output.splitlines():

        line = line.strip()

        if not line:
            continue

        if line.startswith("Interface"):
            continue

        if line.startswith("-"):
            continue

        parts = line.split()

        if len(parts) < 5:
            continue

        interfaces.append(
            {
                "interface": parts[0],
                "type": parts[1],
                "source": parts[2]
            }
        )

    return interfaces


# ============================================================
# JOB DEFINITIONS
# ============================================================

ANSIBLE_JOBS = {

    "baseline": {
        "label": "Apply Linux baseline",
        "playbook": BASELINE_PLAYBOOK,
        "tags": []
    },

    "epel": {
        "label": "Enable EPEL",
        "playbook": BASELINE_PLAYBOOK,
        "tags": [
            "epel"
        ]
    }

}


# ============================================================
# GENERIC ANSIBLE BACKGROUND EXECUTION
# ============================================================

def execute_ansible(
    command,
    log_path
):

    start_time = datetime.now()

    append_log(
        log_path,
        "\n"
        "============================================================\n"
        "ANSIBLE COMMAND\n"
        "============================================================\n"
        + " ".join(command)
        + "\n\n"
    )

    try:

        env = {
            **os.environ,
            "ANSIBLE_FORCE_COLOR": "false",
            "PYTHONUNBUFFERED": "1"
        }

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
            env=env
        )

        for line in process.stdout:

            append_log(
                log_path,
                line
            )

        process.stdout.close()

        return_code = process.wait()

        duration = (
            datetime.now() - start_time
        )

        append_log(
            log_path,
            "\n\n"
            "============================================================\n"
            "ANSIBLE FINISHED\n"
            "============================================================\n"
            f"End        : {datetime.now().isoformat()}\n"
            f"Duration   : {duration}\n"
            f"Return code: {return_code}\n"
            "============================================================\n"
        )

        return return_code

    except Exception as exc:

        append_log(
            log_path,
            "\n\n"
            "============================================================\n"
            "EXCEPTION\n"
            "============================================================\n"
            f"{exc}\n"
        )

        return 1


# ============================================================
# LIFECYCLE
# ============================================================

def run_vm_lifecycle(
    name,
    action
):

    log_filename, log_path = create_log_file(
        action.upper(),
        name
    )

    command = [
        "/usr/bin/ansible-playbook",
        str(LIFECYCLE_PLAYBOOK),
        "-i",
        "localhost,",
        "-e",
        f"vm_name={name}",
        "-e",
        f"vm_action={action}",
        "-vv"
    ]

    def worker():

        return_code = execute_ansible(
            command,
            log_path
        )

        if return_code == 0:

            if action == "start":

                update_vm_status(
                    name,
                    "RUNNING",
                    log_filename
                )

            elif action == "stop":

                update_vm_status(
                    name,
                    "STOPPED",
                    log_filename
                )

            elif action == "restart":

                update_vm_status(
                    name,
                    "RUNNING",
                    log_filename
                )

        else:

            update_vm_status(
                name,
                "ERROR",
                log_filename
            )

    thread = threading.Thread(
        target=worker,
        daemon=True
    )

    thread.start()


# ============================================================
# LIFECYCLE ACTION
# ============================================================

@app.route(
    "/vm/<name>/<action>",
    methods=["POST"]
)
def lifecycle_action(
    name,
    action
):

    if action not in (
        "start",
        "stop",
        "restart"
    ):

        return (
            "Invalid lifecycle action",
            400
        )

    vm = find_vm(name)

    if not vm:

        return (
            "VM not found",
            404
        )

    status = vm.get("status")

    # --------------------------------------------------------
    # BLOCK TRANSITION STATES
    # --------------------------------------------------------

    if status in (
        "PROVISIONING",
        "STARTING",
        "STOPPING",
        "RESTARTING",
        "DELETING",
        "DELETED"
    ):

        return redirect(
            url_for("index")
        )

    # --------------------------------------------------------
    # VALIDATE ACTION AGAINST CURRENT STATE
    # --------------------------------------------------------

    if action == "start" and status == "RUNNING":

        return redirect(
            url_for("index")
        )

    if action == "stop" and status == "STOPPED":

        return redirect(
            url_for("index")
        )

    if action == "restart" and status != "RUNNING":

        return redirect(
            url_for("index")
        )

    # --------------------------------------------------------
    # TRANSITION STATE
    # --------------------------------------------------------

    transition_state = {
        "start": "STARTING",
        "stop": "STOPPING",
        "restart": "RESTARTING"
    }

    update_vm_status(
        name,
        transition_state[action]
    )

    run_vm_lifecycle(
        name,
        action
    )

    return redirect(
        url_for("index")
    )


# ============================================================
# JOB EXECUTION
# ============================================================

def run_vm_job(
    name,
    job_name
):

    job = ANSIBLE_JOBS.get(
        job_name
    )

    if not job:
        return False

    log_filename, log_path = create_log_file(
        job_name.upper(),
        name
    )

    command = [
        "/usr/bin/ansible-playbook",
        str(job["playbook"]),
        "-i",
        str(INVENTORY_FILE),
        "--limit",
        name
    ]

    if job["tags"]:

        command.extend(
            [
                "--tags",
                ",".join(job["tags"])
            ]
        )

    command.append("-vv")

    append_log(
        log_path,
        "Job:\n"
        f"Name       : {job_name}\n"
        f"Description: {job['label']}\n"
        f"VM         : {name}\n"
        f"Inventory  : {INVENTORY_FILE}\n\n"
    )

    def worker():

        execute_ansible(
            command,
            log_path
        )

    thread = threading.Thread(
        target=worker,
        daemon=True
    )

    thread.start()

    return True


# ============================================================
# JOB ROUTE
# ============================================================

@app.route(
    "/vm/<name>/job/<job_name>",
    methods=["POST"]
)
def execute_vm_job(
    name,
    job_name
):

    if job_name not in ANSIBLE_JOBS:

        return (
            "Invalid job",
            400
        )

    vm = find_vm(name)

    if not vm:

        return (
            "VM not found",
            404
        )

    # Configuration jobs require a live VM.

    if vm.get("status") != "RUNNING":

        return redirect(
            url_for("index")
        )

    run_vm_job(
        name,
        job_name
    )

    return redirect(
        url_for("index")
    )


# ============================================================
# CREATE VM
# ============================================================

@app.route(
    "/create",
    methods=["GET", "POST"]
)
def create_vm():

    if request.method == "GET":

        return render_template(
            "create.html"
        )

    name = request.form.get(
        "name",
        ""
    ).strip()

    ip = request.form.get(
        "ip",
        ""
    ).strip()

    install_method = request.form.get(
        "install_method",
        ""
    ).strip().lower()

    cpu = request.form.get(
        "cpu",
        "4"
    ).strip()

    ram = request.form.get(
        "ram",
        "4096"
    ).strip()

    if not validate_vm_name(name):

        return render_template(
            "create.html",
            error=f"Invalid hostname '{name}'"
        )

    if not validate_ip(ip):

        return render_template(
            "create.html",
            error=f"Invalid IP address '{ip}'"
        )

    if install_method not in (
        "kickstart",
        "gold"
    ):

        return render_template(
            "create.html",
            error=(
                "Installation method must be "
                "Kickstart or Gold"
            )
        )

    try:

        cpu = int(cpu)

        if cpu < 1 or cpu > 32:
            raise ValueError

    except ValueError:

        return render_template(
            "create.html",
            error="Invalid CPU value."
        )

    try:

        ram = int(ram)

        if ram < 1024 or ram > 65536:
            raise ValueError

    except ValueError:

        return render_template(
            "create.html",
            error="Invalid RAM value."
        )

    if find_vm(name):

        return render_template(
            "create.html",
            error=(
                f"VM '{name}' already exists "
                "in the database."
            )
        )

    log_filename, log_path = create_log_file(
        "CREATE",
        name,
        install_method
    )

    vms = load_vms()

    vms.append(
        {
            "name": name,
            "ip": ip,
            "cpu": cpu,
            "ram": ram,
            "install_method": install_method.upper(),
            "date": datetime.now().isoformat(),
            "status": "PROVISIONING",
            "log": log_filename
        }
    )

    save_vms(vms)

    command = [
        "/usr/bin/ansible-playbook",
        str(PROVISION_PLAYBOOK),
        "-i",
        "localhost,",
        "-e",
        f"vm_name={name}",
        "-e",
        f"ip={ip}",
        "-e",
        f"vcpus={cpu}",
        "-e",
        f"memory_mb={ram}",
        "-e",
        f"install_method={install_method}",
        "-vv"
    ]

    append_log(
        log_path,
        "Provisioning parameters:\n"
        f"VM name      : {name}\n"
        f"IP           : {ip}\n"
        f"CPU          : {cpu}\n"
        f"RAM          : {ram}\n"
        f"Installation : {install_method}\n\n"
    )

    def worker():

        return_code = execute_ansible(
            command,
            log_path
        )

        if return_code == 0:

            update_vm_status(
                name,
                "RUNNING",
                log_filename
            )

            generate_inventory(
                log_path
            )

        else:

            update_vm_status(
                name,
                "ERROR",
                log_filename
            )

    thread = threading.Thread(
        target=worker,
        daemon=True
    )

    thread.start()

    return redirect(
        url_for("index")
    )


# ============================================================
# INDEX
# ============================================================

@app.route("/")
def index():

    vms = load_vms()

    vm_display_data = []

    for vm in vms:

        vm_data = dict(vm)

        name = vm.get("name")

        libvirt_info = get_libvirt_vm_info(
            name
        )

        vm_data["uuid"] = (
            libvirt_info.get("UUID")
        )

        vm_data["libvirt_state"] = (
            libvirt_info.get("State")
        )

        vm_data["libvirt_cpu"] = (
            libvirt_info.get("CPU(s)")
        )

        vm_data["libvirt_memory"] = (
            libvirt_info.get("Max memory")
        )

        vm_data["persistent"] = (
            libvirt_info.get("Persistent")
        )

        vm_data["disks"] = (
            get_libvirt_disks(name)
        )

        vm_data["interfaces"] = (
            get_libvirt_interfaces(name)
        )

        vm_display_data.append(
            vm_data
        )

    return render_template(
        "index.html",
        vms=vm_display_data
    )


# ============================================================
# VM DETAIL
# ============================================================

@app.route(
    "/vm/<name>"
)
def vm_detail(name):

    vm = find_vm(name)

    if not vm:

        return (
            "VM not found",
            404
        )

    libvirt_info = get_libvirt_vm_info(
        name
    )

    vm_detail_data = dict(vm)

    vm_detail_data["uuid"] = (
        libvirt_info.get("UUID")
    )

    vm_detail_data["libvirt_state"] = (
        libvirt_info.get("State")
    )

    vm_detail_data["libvirt_cpu"] = (
        libvirt_info.get("CPU(s)")
    )

    vm_detail_data["libvirt_memory"] = (
        libvirt_info.get("Max memory")
    )

    vm_detail_data["persistent"] = (
        libvirt_info.get("Persistent")
    )

    return render_template(
        "vm_detail.html",
        vm=vm_detail_data,
        disks=get_libvirt_disks(name),
        interfaces=get_libvirt_interfaces(name)
    )


# ============================================================
# LOGS
# ============================================================

@app.route(
    "/logs/<name>"
)
def logs(name):

    vm = find_vm(name)

    if not vm:

        return (
            "VM not found",
            404
        )

    log_files = []

    pattern = f"vm-*-{name}-*.log"

    for log_path in LOG_DIR.glob(pattern):

        if not log_path.is_file():
            continue

        match = re.match(
            rf"^vm-([a-zA-Z]+)-{re.escape(name)}-(\d{{8}}-\d{{6}})\.log$",
            log_path.name
        )

        if not match:
            continue

        operation = match.group(1).upper()

        timestamp_string = match.group(2)

        try:

            log_datetime = datetime.strptime(
                timestamp_string,
                "%Y%m%d-%H%M%S"
            )

        except ValueError:

            log_datetime = datetime.fromtimestamp(
                log_path.stat().st_mtime
            )

        log_files.append(
            {
                "filename": log_path.name,
                "operation": operation,
                "datetime": log_datetime,
                "datetime_display": log_datetime.strftime(
                    "%d/%m/%Y %H:%M:%S"
                ),
                "size": log_path.stat().st_size
            }
        )

    log_files.sort(
        key=lambda item: item["datetime"],
        reverse=True
    )

    selected_log = request.args.get(
        "log",
        ""
    ).strip()

    selected_log_data = None
    log_content = ""

    if selected_log:

        if not re.fullmatch(
            rf"vm-[a-zA-Z]+-{re.escape(name)}-\d{{8}}-\d{{6}}\.log",
            selected_log
        ):

            return (
                "Invalid log file",
                400
            )

        log_path = LOG_DIR / selected_log

        try:

            resolved_log = log_path.resolve()
            resolved_log_dir = LOG_DIR.resolve()

            resolved_log.relative_to(
                resolved_log_dir
            )

        except ValueError:

            return (
                "Invalid log path",
                400
            )

        if not resolved_log.exists():

            return (
                "Log file not found",
                404
            )

        with open(
            resolved_log,
            "r",
            errors="replace"
        ) as f:

            log_content = f.read()

        for item in log_files:

            if item["filename"] == selected_log:

                selected_log_data = item

                break

    elif log_files:

        selected_log_data = log_files[0]

        log_path = (
            LOG_DIR /
            selected_log_data["filename"]
        )

        with open(
            log_path,
            "r",
            errors="replace"
        ) as f:

            log_content = f.read()

    else:

        log_content = (
            "No logs found for this VM yet."
        )

    return render_template(
        "logs.html",
        vm=vm,
        log_files=log_files,
        selected_log=selected_log_data,
        log_content=log_content
    )


# ============================================================
# DELETE
# ============================================================

@app.route(
    "/delete/<name>",
    methods=["GET", "POST"]
)
def delete_vm(name):

    vm = find_vm(name)

    if not vm:

        return (
            "VM not found",
            404
        )

    if vm.get("status") in (
        "DELETING",
        "DELETED"
    ):

        return redirect(
            url_for("index")
        )

    if request.method == "GET":

        return render_template(
            "delete.html",
            vm=vm
        )

    delete_key = request.form.get(
        "delete_key",
        ""
    )

    if not DELETE_KEY:

        return render_template(
            "delete.html",
            vm=vm,
            error=(
                "VM deletion is not configured. "
                "VM_DELETE_KEY is not available."
            )
        )

    if not hmac.compare_digest(
        delete_key,
        DELETE_KEY
    ):

        return render_template(
            "delete.html",
            vm=vm,
            error="Invalid deletion key."
        )

    log_filename, log_path = create_log_file(
        "DELETE",
        name
    )

    update_vm_status(
        name,
        "DELETING",
        log_filename
    )

    command = [
        "/usr/bin/ansible-playbook",
        str(DELETE_PLAYBOOK),
        "-i",
        "localhost,",
        "-e",
        f"vm_name={name}",
        "-e",
        "operation=delete",
        "-vv"
    ]

    def worker():

        return_code = execute_ansible(
            command,
            log_path
        )

        if return_code == 0:

            update_vm_status(
                name,
                "DELETED",
                log_filename
            )

            generate_inventory(
                log_path
            )

        else:

            update_vm_status(
                name,
                "ERROR",
                log_filename
            )

    thread = threading.Thread(
        target=worker,
        daemon=True
    )

    thread.start()

    return redirect(
        url_for("index")
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=8087,
        debug=False
    )


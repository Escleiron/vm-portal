# VM Portal

Linux virtual machine provisioning and management portal built with Flask, Ansible and libvirt.

VM Portal provides a simple web interface to provision, manage and inspect RHEL 9 virtual machines in a KVM/libvirt environment.

## Overview

The project combines:

- Flask for the web interface and application logic.
- Ansible for VM provisioning, lifecycle management and Linux configuration.
- libvirt/KVM for virtualization.
- RHEL 9 as the guest operating system.
- YAML for persistent VM state.
- A generated Ansible inventory based on the VM database.

The web application acts as a lightweight control plane while infrastructure operations are delegated to Ansible.

## Features

- RHEL 9 VM provisioning.
- Provisioning using a Gold image or Kickstart.
- VM deletion.
- VM Start, Stop and Restart.
- Linux baseline configuration.
- Optional EPEL configuration.
- Dynamic Ansible inventory generation.
- VM detail information from libvirt.
- Operation logs.

## Architecture

```text
                         VM Portal
                            |
                            v
                         Flask
                            |
             +--------------+--------------+
             |              |              |
             v              v              v
        Provisioning     Lifecycle       Jobs
             |              |              |
             v              v              v
          Ansible        Ansible        Ansible
             |              |              |
             +--------------+--------------+
                            |
                            v
                       libvirt / KVM
```

Dynamic inventory:

```text
/var/lib/vm-portal/vms.yml
            |
            v
scripts/generate_inventory.py
            |
            v
ansible/inventories/inventory.yml
```

`vms.yml` is the source of truth for the VMs managed by the portal.

`inventory.yml` is generated automatically and should not be edited manually.

## Project Structure

```text
vm-portal/
|
+-- ansible/
|   +-- inventories/
|   |   +-- inventory.yml
|   +-- playbooks/
|   |   +-- provision_vm.yml
|   |   +-- test_baseline.yml
|   |   +-- vm_lifecycle.yml
|   +-- roles/
|       +-- linux_baseline/
|       +-- linux_lab_provisioning/
|       +-- vm_lifecycle/
|
+-- scripts/
|   +-- generate_inventory.py
|
+-- web/
|   +-- app.py
|   +-- templates/
|       +-- create.html
|       +-- delete.html
|       +-- index.html
|       +-- logs.html
|       +-- vm_detail.html
|
+-- .gitignore
+-- LICENSE
+-- README.md
+-- requirements.txt
```

## Requirements

The project is designed for a RHEL 9 virtualization host.

Required components:

- RHEL 9.
- KVM.
- libvirt.
- `virsh`.
- Python 3.
- Ansible.
- A configured RHEL 9 installation source.
- Network connectivity to the guest VMs.

Python dependencies are listed in `requirements.txt`.

## Runtime Directories

Application source:

```text
/opt/vm-portal/
```

VM database:

```text
/var/lib/vm-portal/vms.yml
```

Logs:

```text
/var/log/vm-portal/
```

Configuration:

```text
/etc/vm-portal/
```

Systemd service:

```text
/etc/systemd/system/vm-portal.service
```

Runtime data and environment-specific configuration are intentionally kept outside the Git repository.

## Configuration

Sensitive configuration is supplied through environment variables.

The application currently uses:

```text
VM_DELETE_KEY
```

The deletion key must never be committed to Git.

Other environment-specific values, credentials and secrets should also remain outside the repository.

## VM Provisioning

VMs can be provisioned using:

- Gold image.
- Kickstart.

The provisioning playbook is:

```text
ansible/playbooks/provision_vm.yml
```

The main provisioning role is:

```text
ansible/roles/linux_lab_provisioning/
```

The Kickstart template is:

```text
ansible/roles/linux_lab_provisioning/templates/rhel9.ks.j2
```

## VM Lifecycle

Lifecycle operations are handled by:

```text
ansible/playbooks/vm_lifecycle.yml
```

The available operations are:

```text
start
stop
restart
```

The corresponding role is:

```text
ansible/roles/vm_lifecycle/
```

## Linux Baseline

The Linux baseline is implemented in:

```text
ansible/roles/linux_baseline/
```

It includes tasks for:

- Red Hat subscription.
- Common Linux administration packages.
- Security configuration.
- Optional EPEL configuration.

The baseline test playbook is:

```text
ansible/playbooks/test_baseline.yml
```

EPEL is optional and is executed using the `epel` Ansible tag.

## Dynamic Inventory

The inventory generator is:

```text
scripts/generate_inventory.py
```

The source data is:

```text
/var/lib/vm-portal/vms.yml
```

The generated inventory is:

```text
ansible/inventories/inventory.yml
```

After a successful VM creation, the portal updates `vms.yml` and regenerates the inventory.

After a successful VM deletion, the portal marks the VM as `DELETED` and regenerates the inventory.

Deleted VMs remain in `vms.yml` for historical purposes but are excluded from the generated inventory.

The inventory generator can also be executed manually:

```bash
/opt/vm-portal/scripts/generate_inventory.py
```

## Web Application

The Flask application is:

```text
web/app.py
```

The application listens on:

```text
0.0.0.0:8087
```

The portal can therefore be accessed at:

```text
http://<HOST>:8087/
```

In the target environment, the application is intended to run as a systemd service.

## Logs

Operation logs are stored outside the application source tree:

```text
/var/log/vm-portal/
```

Logs are generated for operations such as:

- Create.
- Delete.
- Start.
- Stop.
- Restart.
- Linux baseline jobs.
- EPEL jobs.

The web interface provides access to the logs associated with each VM.

## Manual Ansible Execution

Provisioning:

```bash
ansible-playbook   ansible/playbooks/provision_vm.yml   -i localhost,   -e vm_name=<VM_NAME>   -e ip=<VM_IP>   -e vcpus=<VCPU_COUNT>   -e memory_mb=<MEMORY_MB>   -e install_method=<kickstart|gold>
```

Lifecycle:

```bash
ansible-playbook   ansible/playbooks/vm_lifecycle.yml   -i localhost,   -e vm_name=<VM_NAME>   -e vm_action=<start|stop|restart>
```

## Development

Create a Python virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

Install Python dependencies:

```bash
pip install -r requirements.txt
```

Run the Flask application manually:

```bash
python web/app.py
```

For the normal server deployment, use the systemd service instead of running Flask manually.

## Security Considerations

The following must never be committed to the repository:

- Private SSH keys.
- Red Hat activation keys.
- Passwords.
- API tokens.
- VM deletion keys.
- Other secrets.
- VM runtime state.
- Application logs.
- VM disk images.
- Installation ISO images.

Public SSH keys are not secret. However, environment-specific public keys should preferably be supplied through configuration rather than hard-coded into reusable project files.

The `.gitignore` file excludes local runtime data, virtual environments, logs and local backup files.

## Current Scope

The current project focuses on:

1. RHEL 9 VM provisioning.
2. Gold image provisioning.
3. Kickstart provisioning.
4. VM deletion.
5. Start / Stop / Restart.
6. Linux baseline configuration.
7. Optional EPEL configuration.
8. Dynamic Ansible inventory generation.
9. VM details.
10. Operation logs.

The project deliberately keeps the web application lightweight and delegates infrastructure operations to Ansible.

## Future Improvements

Possible future improvements include:

- Automated installation script.
- First-install configuration handling.
- Improved secret management.
- Additional Linux baseline configuration.
- Additional VM configuration options.
- More complete automated testing.

## License

This project is licensed under the MIT License.

See the `LICENSE` file for the complete license text.


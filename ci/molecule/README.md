# molecule

Molecule test scenarios for validating individual Ansible roles in
isolation.

Expected contents:

- One Molecule scenario per role under `ansible/roles/` (or shared
  scenarios where appropriate)
- Driver configuration (e.g. Docker/Vagrant) for role-level testing
  without requiring full hardware

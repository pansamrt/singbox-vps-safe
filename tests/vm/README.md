# Disposable Debian full virtual machine lifecycle tests

The automated test defined in `.github/workflows/debian-qemu-vm.yml`
boots an official Debian 12 genericcloud **QEMU virtual machine**. It is
not a Docker container and is not the user's VPS.

## Security boundaries

- Download Debian 12 cloud QCOW2 and verify the SHA512 listed on the
  same official Debian HTTPS endpoint. A future release should pin an
  immutable Debian image build and digest.
- Obtain the official sing-box v1.14.4 amd64 archive; check its known SHA256
  `699a1b510aa4396b2176d32d1181920399ebf1e267835da6f3b598d0f7c24968`.
- Build two read-only ISOs: one with cloud-init test metadata, the other with
  a test-only lifecycle shell script and verified sing-box binary.
- Run QEMU with `-nic none`: **the guest has no network device**.
- Run with `-snapshot`: guest disk writes are disposable and do not change
  the downloaded base image.
- Never mount the host root, user SSH directories, network credentials, Docker
  socket, or other user resources into the VM.
- Do **not** execute the archived `upstream/fscarmen/sing-box.sh`.
  It has not passed the security audit gates needed to execute it.

## What the guest test does

The test script is `tests/vm/guest-smoke.sh`. It creates ONLY a disposable
dedicated test service, `singbox-vps-safe-ci.service`, under CI-specific paths:

- `/opt/singbox-vps-safe-ci/sing-box`
- `/etc/singbox-vps-safe-ci/config.json`
- `/etc/systemd/system/singbox-vps-safe-ci.service`

It verifies the native core version and config, installs/enables the
CI-only service, checks it is active and schedules a real VM reboot.

After the reboot it checks the service survived, confirms an invalid new
config fails a *read-only* preflight without stopping the service, then
stops/disables/removes ONLY its CI-specific resources and compares the SSH
service, original sing-box service, Nginx, DNS, UFW configuration and
available firewall rules against the pre-install snapshot.

Success is asserted from the QEMU serial console marker:

`SBVM_INSTALL_REBOOT_FAILURE_UNINSTALL_PASS`

A runner job cannot pass merely because the VM process terminates; it
must find **all** first-install, reboot, failed-preflight and cleanup markers.

## Important limitations

- This is an **adapted test-only** install path, not the original
  fscarmen all-in-one installation script or a released installer.
- The fixture covers a local SOCKS inbound, not the full fscarmen
  VLESS-Reality, Hysteria2, TUIC, AnyTLS, WARP, Argo or certificate workflow.
- It does not test real client connections over a public network: the VM has
  no NIC by design.
- Firewall comparison is best effort for the tools installed in the image.
  A test system without UFW or nftables cannot prove compatibility with a
  system where those are active.
- No irreversible failure-injection into the bootloader or kernel is
  performed. Update/rollback is still pending a safe production adapter.
- Use a disposable CI environment only. **Do not run this guest test script
  on any owner VPS.**

# v4.2.3 Installer Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore opt-in finish-page choices in the Windows installer and publish the v4.2.3 setup executable.

**Architecture:** Inno Setup consumes `AutoRewarder.iss` after PyInstaller creates `dist/AutoRewarder`. The runtime update checker uses `CURRENT_VERSION` in `src/config.py`; both sources must use the same v4.2.3 version. The GitHub release attaches the installer generated from that matching source state.

**Tech Stack:** Python 3.12, PyInstaller, Inno Setup 6, GitHub Releases, `gh` CLI.

## Global Constraints

- Target repository: `EnderMagician/AutoRewarder---My-version`.
- Original upstream links remain `https://github.com/safarsin/AutoRewarder`.
- Every post-install action is unchecked by default.
- Do not stage unrelated pre-existing working-tree edits.
- Copy the completed setup executable to `D:/AutoRewarder-Setup-v4.2.3.exe`.

---

### Task 1: Lock down the installer manifest contract

**Files:**
- Create: `tests/test_installer_manifest.py`
- Modify: `AutoRewarder.iss`
- Modify: `src/config.py`

- [ ] Write and run a failing test that expects v4.2.3, four opt-in actions,
  and the original upstream URL.
- [ ] Update the version fields and add `unchecked` to launch and User Guide.
- [ ] Run `python3 -m unittest tests.test_installer_manifest -v` and confirm
  the contract passes.

### Task 2: Build and independently verify the Windows installer

**Files:**
- Generate: `dist/AutoRewarder-Setup-v4.2.3.exe`
- Copy: `D:/AutoRewarder-Setup-v4.2.3.exe`

- [ ] Run `pyinstaller AutoRewarder.spec --noconfirm` in a Windows-capable
  environment.
- [ ] Run `C:\Program Files (x86)\Inno Setup 6\iscc.exe AutoRewarder.iss`.
- [ ] Verify the artifact checksum and copy it to D:.

### Task 3: Commit, publish, and verify the GitHub release

**Files:**
- Stage only: `AutoRewarder.iss`, `src/config.py`,
  `tests/test_installer_manifest.py`, and the two release documents.
- Upload: `dist/AutoRewarder-Setup-v4.2.3.exe`.

- [ ] Confirm `gh` authentication and repository access.
- [ ] Commit and push only the listed source files.
- [ ] Create tag/release `v4.2.3`, upload the setup executable, and read it
  back with `gh release view`.

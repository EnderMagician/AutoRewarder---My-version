# v4.2.3 Installer and Release Design

## Goal

Publish AutoRewarder v4.2.3 with an installer finish page that retains its
post-install actions but leaves every action unchecked by default.

## Scope

- Keep the finish-page actions for launching AutoRewarder, opening the User
  Guide, opening the original `safarsin/AutoRewarder` repository, and opening
  the support link.
- Mark every post-install action as opt-in with Inno Setup's `unchecked` flag.
- Bump the application and installer version from v4.2.2 to v4.2.3.
- Produce an Inno Setup executable named `AutoRewarder-Setup-v4.2.3.exe`.
- Publish that executable as the `v4.2.3` release asset on
  `EnderMagician/AutoRewarder---My-version` and copy it to `D:/`.

## Non-goals

- Do not replace upstream links with the fork URL.
- Do not change desktop or Start Menu task defaults.
- Do not include unrelated existing working-tree changes in a release commit.

## Verification

- A manifest regression test confirms every `[Run]` action contains
  `postinstall` and `unchecked`, retains the upstream repository URL, and
  matches v4.2.3 in both installer and runtime configuration.
- Build the PyInstaller distribution before compiling the Inno Setup installer.
- Verify the release asset name and checksum after publishing.

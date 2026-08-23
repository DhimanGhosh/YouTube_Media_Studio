; Native Windows installer for YouTube Media Studio.
; Build-time paths and VERSION are supplied by tools/release.py.

Unicode true
!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"

!ifndef VERSION
  !error "VERSION is required"
!endif
!ifndef GUI_PAYLOAD_DIR
  !error "GUI_PAYLOAD_DIR is required"
!endif
!ifndef GUI_BUNDLE_EXE
  !error "GUI_BUNDLE_EXE is required"
!endif
!ifndef CLI_PAYLOAD
  !error "CLI_PAYLOAD is required"
!endif
!ifndef OUTPUT_FILE
  !error "OUTPUT_FILE is required"
!endif
!ifndef APP_ICON
  !error "APP_ICON is required"
!endif

!define APP_NAME "YouTube Media Studio"
!define APP_EXE "${APP_NAME}.exe"
!define CLI_EXE "youtube-media-studio.exe"
!define COMPANY "Dhiman Ghosh"
!define PRODUCT_ID "YouTubeMediaStudio"
!define APP_REG_KEY "Software\${PRODUCT_ID}"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_ID}"
!define UNINSTALL_EXE "Uninstall.exe"

Name "${APP_NAME}"
Caption "${APP_NAME} ${VERSION} Setup"
OutFile "${OUTPUT_FILE}"
InstallDir "$LOCALAPPDATA\Programs\${APP_NAME}"
InstallDirRegKey HKCU "${APP_REG_KEY}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUninstDetails show
BrandingText "${APP_NAME}"

VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${APP_NAME}"
VIAddVersionKey "CompanyName" "${COMPANY}"
VIAddVersionKey "LegalCopyright" "Copyright (c) ${COMPANY}"
VIAddVersionKey "FileDescription" "${APP_NAME} native Windows installer"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"

!define MUI_ABORTWARNING
!define MUI_ICON "${APP_ICON}"
!define MUI_UNICON "${APP_ICON}"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_RIGHT
!define MUI_HEADERIMAGE_BITMAP "assets\installer-header.bmp"
!define MUI_WELCOMEFINISHPAGE_BITMAP "assets\installer-welcome.bmp"
!define MUI_WELCOMEPAGE_TITLE "Welcome to ${APP_NAME}"
!define MUI_WELCOMEPAGE_TEXT "Setup will install ${APP_NAME} ${VERSION} on this computer.$\r$\n$\r$\nClose any running copy of the application before continuing. Your settings, playlists, history, and media files are kept during upgrades.$\r$\n$\r$\nClick Next to continue."
!define MUI_FINISHPAGE_TITLE "${APP_NAME} is ready"
!define MUI_FINISHPAGE_TEXT "Installation completed successfully.$\r$\n$\r$\nClick Finish to close Setup. You can optionally launch ${APP_NAME} now."
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Launch ${APP_NAME}"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\..\LICENSE"
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "English"

Section "${APP_NAME} (required)" SecMain
  SectionIn RO
  SetShellVarContext current
  ; Extract the complete new onedir payload away from the live installation.
  ; Copying over an existing _internal directory can retain old dist-info and
  ; make the upgraded app report (or even import) the previous version.
  RMDir /r "$INSTDIR\.update"
  SetOutPath "$INSTDIR\.update"

  ; Close only the app installed at this exact path. Using an inherited
  ; environment variable avoids interpolating the path into PowerShell code.
  System::Call 'Kernel32::SetEnvironmentVariable(t, t)i("YMS_UPGRADE_TARGET", "$INSTDIR\${APP_EXE}")'
  nsExec::ExecToLog 'powershell.exe -NoProfile -NonInteractive -Command "Get-CimInstance Win32_Process | Where-Object { $$_.ExecutablePath -and [StringComparer]::OrdinalIgnoreCase.Equals($$_.ExecutablePath, $$env:YMS_UPGRADE_TARGET) } | ForEach-Object { Stop-Process -Id $$_.ProcessId -Force }"'
  System::Call 'Kernel32::SetEnvironmentVariable(t, i)i("YMS_UPGRADE_TARGET", 0)'
  File /r "${GUI_PAYLOAD_DIR}\*"
  ; Stage both the launcher and its complete runtime, then swap the new
  ; payload into place. Restore both if either rename fails.
  Delete "$INSTDIR\${APP_EXE}.previous"
  RMDir /r "$INSTDIR\_internal.previous"
  IfFileExists "$INSTDIR\${APP_EXE}" 0 legacy_launcher_staged
    ClearErrors
    Rename "$INSTDIR\${APP_EXE}" "$INSTDIR\${APP_EXE}.previous"
    IfErrors 0 legacy_launcher_staged
      Abort "Could not prepare ${APP_EXE} for upgrade. Close the application and run Setup again."
  legacy_launcher_staged:
  IfFileExists "$INSTDIR\_internal\*" 0 old_runtime_staged
    ClearErrors
    Rename "$INSTDIR\_internal" "$INSTDIR\_internal.previous"
    IfErrors 0 old_runtime_staged
      ClearErrors
      Rename "$INSTDIR\${APP_EXE}.previous" "$INSTDIR\${APP_EXE}"
      Abort "Could not prepare the application runtime for upgrade. Close the application and run Setup again."
  old_runtime_staged:
  ClearErrors
  Rename "$INSTDIR\.update\_internal" "$INSTDIR\_internal"
  IfErrors 0 new_runtime_ready
    ClearErrors
    Rename "$INSTDIR\_internal.previous" "$INSTDIR\_internal"
    Rename "$INSTDIR\${APP_EXE}.previous" "$INSTDIR\${APP_EXE}"
    Abort "Could not install the new application runtime. Run Setup again."
  new_runtime_ready:
  ClearErrors
  Rename "$INSTDIR\.update\${GUI_BUNDLE_EXE}" "$INSTDIR\${APP_EXE}"
  IfErrors 0 gui_executable_ready
    RMDir /r "$INSTDIR\_internal"
    ClearErrors
    Rename "$INSTDIR\_internal.previous" "$INSTDIR\_internal"
    Rename "$INSTDIR\${APP_EXE}.previous" "$INSTDIR\${APP_EXE}"
    Abort "Could not replace ${APP_EXE}. Close the application and run Setup again."
  gui_executable_ready:
  Delete "$INSTDIR\${APP_EXE}.previous"
  RMDir /r "$INSTDIR\_internal.previous"
  RMDir /r "$INSTDIR\.update"
  WriteUninstaller "$INSTDIR\${UNINSTALL_EXE}"

  WriteRegStr HKCU "${APP_REG_KEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "${COMPANY}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\${APP_EXE}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\${UNINSTALL_EXE}"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '"$INSTDIR\${UNINSTALL_EXE}" /S'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1

  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "EstimatedSize" "$0"

  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\Uninstall.lnk" "$INSTDIR\${UNINSTALL_EXE}"
SectionEnd

Section /o "Command-line tools" SecCli
  SetOutPath "$INSTDIR"
  File "/oname=${CLI_EXE}" "${CLI_PAYLOAD}"
SectionEnd

Section /o "Desktop shortcut" SecDesktop
  SetShellVarContext current
  CreateShortcut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
SectionEnd

LangString DESC_SecMain ${LANG_ENGLISH} "Install the desktop application and Start menu shortcuts."
LangString DESC_SecCli ${LANG_ENGLISH} "Install the optional ${CLI_EXE} command-line executable."
LangString DESC_SecDesktop ${LANG_ENGLISH} "Create a shortcut on your desktop."

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecMain} $(DESC_SecMain)
  !insertmacro MUI_DESCRIPTION_TEXT ${SecCli} $(DESC_SecCli)
  !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} $(DESC_SecDesktop)
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Section "Uninstall"
  SetShellVarContext current
  System::Call 'Kernel32::SetEnvironmentVariable(t, t)i("YMS_UPGRADE_TARGET", "$INSTDIR\${APP_EXE}")'
  nsExec::ExecToLog 'powershell.exe -NoProfile -NonInteractive -Command "Get-CimInstance Win32_Process | Where-Object { $$_.ExecutablePath -and [StringComparer]::OrdinalIgnoreCase.Equals($$_.ExecutablePath, $$env:YMS_UPGRADE_TARGET) } | ForEach-Object { Stop-Process -Id $$_.ProcessId -Force }"'
  System::Call 'Kernel32::SetEnvironmentVariable(t, i)i("YMS_UPGRADE_TARGET", 0)'
  Delete "$DESKTOP\${APP_NAME}.lnk"
  RMDir /r "$SMPROGRAMS\${APP_NAME}"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
  DeleteRegKey HKCU "${APP_REG_KEY}"
  RMDir /r "$INSTDIR"
  ; User playlists, settings, and media remain in application data by design.
SectionEnd

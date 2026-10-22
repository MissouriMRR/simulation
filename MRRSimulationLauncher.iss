; Missouri Multirotor Design Team's Drone Simulator
; Switches while experimenting (set them in the IDE under Run > Parameters):
;   /REPORT      show what the checks detected (memory, disk, WSL, graphics), even when everything passes
;   /SKIPCHECKS  bypass all hardware/software checks (for testing the rest of the wizard)
;   /LOG         write a setup log to %TEMP%
; Settings
; DO NOT CHANGE "AppId" DO NOT CHANGE EVER
#define AppGuid "99C26AA2-5C27-4D7E-897F-35C5B653CDF7"
#define AppName "MRRSimulator"
#define AppVersion "0.2.0"
#define AppPublisher "S&T Multirotor Design Team"
#define LauncherExe "MRRSimulator.exe"
; The window (built without a console); it runs LauncherExe in the background
#define GuiExe "MRRSimulatorGUI.exe"
; The WSL distribution that runs ArduPilot and the student scripts (podman + the simulator images).
; It is built by tools\build_distro.py into payload\mrr-rootfs.tar.gz and imported when setup finishes.
; For testing without touching a real "mrr": setup /DISTRO=mrr-test (the config must name the same distro).
#define DefaultDistro "mrr"
#define DistroPayload "payload\mrr-rootfs.tar.gz"
; tools\build_distro.py writes the version of what is inside the payload (see /etc/mrrsim-distro-version in the distro).
; Setup compares it with the installed distribution and replaces that one when they differ.
#if FileExists("payload\distro_version.iss")
  #include "payload\distro_version.iss"
#else
  #define DistroVersion ""
#endif
#define LauncherDir "build\MRRSimulator"

; Minimum Requirements
#define MinRamMB  7000
#define WarnRamMB 15000
; Space needed for the files the launcher downloads
#define MinFreeGB 20
; WSL is only checked, never enabled by this installer. Mirrored networking (Windows and WSL share
; 127.0.0.1) needs Windows 11 22H2 and WSL 2.0 or newer.
#define MinWindowsBuild 22621

[Setup]
AppId={{{#AppGuid}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}

; Per-user install vs system install
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
DefaultDirName={autopf}\{#AppName}

; Architecture
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os

; Minimum Windows version
MinVersion=10.0.19041

; What pages are displayed in the installer
DisableWelcomePage=no
DisableDirPage=no
DisableProgramGroupPage=yes
DisableReadyPage=no

; Setup/uninstall won't continue while launcher is running
AppMutex=MRRSimulatorMutex,Global\MRRSimulatorMutex

; Installer Settings
OutputDir=Output
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#LauncherExe}

[Messages]
WelcomeLabel2=This will install the S&T Multirotor Design Team Launcher on your computer.%n%nWSL 2 must already be set up on this computer (Windows 11 22H2 or newer).%n%nThe simulator and its Linux environment (several GB) are included, so nothing is downloaded. Setup adds one WSL distribution, named mrr, for your account.

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "{#LauncherDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "config\*"; DestDir: "{app}\config"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "config\mrrsim.toml"; DestName: "shipped-mrrsim.toml"; Flags: dontcopy
Source: "scripts\*"; DestDir: "{app}\scripts"; Excludes: "__pycache__"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "sim_config\*"; DestDir: "{app}\sim_config"; \
  Excludes: "scene_ardupilot_*quadrotors*.jsonc,robot_ardu_quadrotor_*.jsonc,generate_scene.py"; \
  Flags: ignoreversion recursesubdirs createallsubdirs
Source: "containers\*"; DestDir: "{app}\containers"; Excludes: "__pycache__,tests"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "Windows\*"; DestDir: "{app}\Windows"; Excludes: "*.log"; Flags: ignoreversion recursesubdirs createallsubdirs
; The simulator's Linux environment (WSL distribution with podman and the simulator images). Setup
; imports it into WSL when the install finishes and then deletes it from the temp folder. It is
; already gzip-compressed, so Setup stores it as it is.
Source: "{#DistroPayload}"; DestDir: "{tmp}"; DestName: "mrr-rootfs.tar.gz"; \
  Flags: deleteafterinstall nocompression skipifsourcedoesntexist

[Icons]
; The main shortcuts open the window. WorkingDir is where ArduPilot's logs\ folder is written, so it must be
; somewhere the student can write (and it must exist: see [Dirs]).
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#GuiExe}"; WorkingDir: "{userdocs}\{#AppName}"
Name: "{autoprograms}\{#AppName} Check"; Filename: "{cmd}"; Parameters: "/K """"{app}\{#LauncherExe}"" doctor"""; WorkingDir: "{userdocs}\{#AppName}"
Name: "{autoprograms}\{#AppName} Scripts"; Filename: "{userdocs}\{#AppName}\scripts"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#GuiExe}"; WorkingDir: "{userdocs}\{#AppName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#GuiExe}"; WorkingDir: "{userdocs}\{#AppName}"; \
  Description: "Open {#AppName}"; \
  Flags: nowait postinstall skipifsilent; Check: LauncherExists

[Dirs]
; Where the shortcuts run from; ArduPilot flight logs end up in its logs\ folder
Name: "{userdocs}\{#AppName}"
; The student's own scripts. The window lists these first; it is never deleted on uninstall
Name: "{userdocs}\{#AppName}\scripts"; Flags: uninsneveruninstall
; Shared download folder for all-users installs, writable by every user
Name: "{commonappdata}\{#AppName}"; Permissions: users-modify; Check: IsAdminInstallMode
Name: "{app}\Windows"; Permissions: users-modify; Check: IsAdminInstallMode
Name: "{app}\sim_config"; Permissions: users-modify; Check: IsAdminInstallMode

[INI]
; Tells launcher where its downloads live and how it was installed
Filename: "{app}\install.ini"; Section: "Install"; Key: "DataDir"; String: "{code:GetDataDir}"
Filename: "{app}\install.ini"; Section: "Install"; Key: "AllUsers"; String: "{code:GetAllUsers}"

[UninstallDelete]
; Remove install.ini so the install folder doesn't get left behind
Type: files; Name: "{app}\install.ini"
Type: files; Name: "{app}\config\mrrsim.toml.previous"

[Code]
const
  MB = 1048576; // 1024 * 1024 bytes (a megabyte)
  NL = #13#10; // Newline character
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\';

type
  TMemoryStatusEx = record
    // dw = DWORD is a 32-bit whole number
    // ull = unsigned long long
    dwLength: DWORD; // Size of TMemoryStatusEx object
    dwMemoryLoad: DWORD; // Percentage of memory in use (0-100)
    ullTotalPhys: Int64; // Total physical RAM
    ullAvailPhys: Int64; // RAM currently free
    ullTotalPageFile: Int64; // RAM plus page file (disk space Windows uses as overflow memory)
    ullAvailPageFile: Int64; // Above but available
    ullTotalVirtual: Int64; // Address space for the installer proc itself
    ullAvailVirtual: Int64; // Above but available
    ullAvailExtendedVirtual: Int64; // Unused by Windows, always 0
  end;

// Helper Functions //

// InnoSetup uses a Windows function to get ullTotalPhys in TMemoryStatusEx
function GlobalMemoryStatusEx(var lpBuffer: TMemoryStatusEx): BOOL;
  external 'GlobalMemoryStatusEx@kernel32.dll stdcall';

// True if the given switch (e.g. '/REPORT') was passed on the command line.
function HasSwitch(const Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Name) = 0 then
    begin
      Result := True;
      Exit;
    end;
end;

// Total physical memory visible to Windows, in MB (-1 if it can't be read).
function TotalRamMB: Int64;
var
  Status: TMemoryStatusEx;
begin
  Result := -1;
  Status.dwLength := SizeOf(Status);
  if GlobalMemoryStatusEx(Status) then
    Result := Status.ullTotalPhys div MB;
end;

// Free space on the drive holding Path, in GB (-1 if it can't be read).
function FreeSpaceGB(const Path: String): Int64;
var
  Free, Total: Int64;
begin
  Result := -1;
  if GetSpaceOnDisk64(Path, Free, Total) then
    Result := Free div (1024 * MB);
end;

// One line per display adapter: "Name (driver x.y.z)". Empty if detection fails
function DescribeGpus: String;
var
  Locator, Service, Items, Item: Variant;
  I: Integer;
  Line: String;
begin
  Result := '';
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Service := Locator.ConnectServer('.', 'root\CIMV2');
    Items := Service.ExecQuery('SELECT Name, DriverVersion FROM Win32_VideoController');
    for I := 0 to Items.Count - 1 do
    begin
      Item := Items.ItemIndex(I);
      Line := '';
      try
        Line := Item.Name;
        Line := Line + ' (driver ' + Item.DriverVersion + ')';
      except
        // Some adapters report no driver version; keep whatever we got
      end;
      if Line <> '' then
        Result := Result + '  ' + Line + NL;
    end;
  except
    Result := '';
  end;
end;

// The Visual C++ 2015-2022 x64 runtime, which Unreal's packaged builds need
function VCRedistInstalled: Boolean;
var
  Installed, Minor: Cardinal;
begin
  Result := RegQueryDWordValue(HKLM64,
      'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', Installed)
    and (Installed = 1)
    and RegQueryDWordValue(HKLM64,
      'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Minor', Minor)
    and (Minor >= 30);
end;

// Used by [Run] to hide "Launch" when the launcher exe isn't there yet
function LauncherExists: Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\{#LauncherExe}'));
end;

// Where the launcher stores its downloads, depending on install mode
function GetDataDir(Param: String): String;
begin
  if IsAdminInstallMode then
    Result := ExpandConstant('{commonappdata}\{#AppName}')
  else
    Result := ExpandConstant('{localappdata}\{#AppName}');
end;

// '1' for an all-users install, '0' for per-user. Written to install.ini
function GetAllUsers(Param: String): String;
begin
  if IsAdminInstallMode then
    Result := '1'
  else
    Result := '0';
end;

// True if the app is already installed in the *other* mode
function InstalledInOtherMode: Boolean;
var
  Key: String;
begin
  Key := UninstallKey + '{' + '{#AppGuid}' + '}_is1';
  if IsAdminInstallMode then
    Result := RegKeyExists(HKCU, Key)
  else
    Result := RegKeyExists(HKLM, Key);
end;

// WSL //

// Full path of the real (64-bit) wsl.exe, or '' if there is none. Setup is a 32-bit program, so on
// 64-bit Windows "System32" quietly means the 32-bit SysWOW64 folder, which has no wsl.exe. {sysnative}
// is the way a 32-bit program reaches the real System32 (it does not exist for a 64-bit one).
function WslExePath: String;
var
  Candidate: String;
begin
  Result := '';
  try
    Candidate := ExpandConstant('{sysnative}\wsl.exe');
    if FileExists(Candidate) then
      Result := Candidate;
  except
    // {sysnative} is not valid in a 64-bit process; {sys} is the real System32 there
  end;
  if Result = '' then
  begin
    Candidate := ExpandConstant('{sys}\wsl.exe');
    if FileExists(Candidate) then
      Result := Candidate;
  end;
end;

// Runs "wsl.exe <Args>" and writes everything it prints to OutFile. Returns its exit code, or -1 if
// it could not be started at all. (Exec cannot capture output, so cmd does the redirect.)
function RunProgram(const Exe, Args, OutFile: String): Integer;
var
  ResultCode: Integer;
begin
  Result := -1;
  DeleteFile(OutFile);
  if Exec(ExpandConstant('{cmd}'),
      '/C ""' + Exe + '" ' + Args + ' > "' + OutFile + '" 2>&1"',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    Result := ResultCode;
end;

function RunWsl(const Args, OutFile: String): Integer;
begin
  Result := RunProgram(WslExePath, Args, OutFile);
end;

// The text of a file wsl.exe wrote. It writes UTF-16, so every ASCII character is followed by a NUL;
// drop those (and carriage returns) and what is left is readable.
function ReadWslText(const FileName: String): String;
var
  Raw: AnsiString;
  I: Integer;
begin
  Result := '';
  if LoadStringFromFile(FileName, Raw) then
    for I := 1 to Length(Raw) do
      if (Raw[I] <> #0) and (Raw[I] <> #13) then
        Result := Result + Chr(Ord(Raw[I]));
end;

// "WSL version: 2.1.5.0" -> 2. -1 if no number follows the first colon (other language, odd output).
function WslMajorVersion(const Text: String): Integer;
var
  P: Integer;
  Digits: String;
begin
  Result := -1;
  P := Pos(':', Text);
  if P = 0 then
    Exit;
  P := P + 1;
  while (P <= Length(Text)) and (Text[P] = ' ') do
    P := P + 1;
  Digits := '';
  while (P <= Length(Text)) and (Text[P] >= '0') and (Text[P] <= '9') do
  begin
    Digits := Digits + Text[P];
    P := P + 1;
  end;
  if Digits <> '' then
    Result := StrToIntDef(Digits, -1);
end;

// 1 if Windows is running under its hypervisor (what WSL2 needs: Virtual Machine Platform on,
// virtualization on in the BIOS, and a restart since), 0 if not, -1 if WMI could not say.
function HypervisorState: Integer;
var
  Locator, Service, Items, Item: Variant;
begin
  Result := -1;
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Service := Locator.ConnectServer('.', 'root\CIMV2');
    Items := Service.ExecQuery('SELECT HypervisorPresent FROM Win32_ComputerSystem');
    if Items.Count > 0 then
    begin
      Item := Items.ItemIndex(0);
      if Item.HypervisorPresent = True then
        Result := 1
      else
        Result := 0;
    end;
  except
    Result := -1;
  end;
end;

// Adds to Blocks when WSL is not set up well enough to run the simulator, and to Info what it found.
// This installer never enables WSL: it only checks, and tells the user what to do.
procedure CheckWsl(var Blocks, Warnings, Info: String);
var
  Build, Code, StatusCode, Major, Hv: Integer;
  Version: TWindowsVersion;
  OutFile, Text: String;
begin
  // Windows and WSL share 127.0.0.1 through mirrored networking, which needs Windows 11 22H2 or newer
  GetWindowsVersionEx(Version);
  Build := Version.Build;
  Info := Info + 'Windows build: ' + IntToStr(Build) + NL;
  if Build < {#MinWindowsBuild} then
  begin
    Blocks := Blocks + '- Windows 11 22H2 (build {#MinWindowsBuild}) or newer is required (found build ' +
      IntToStr(Build) + '). The simulator links Windows and WSL through mirrored networking, ' +
      'which older versions do not have.' + NL;
    Exit;
  end;

  if WslExePath = '' then
  begin
    Info := Info + 'WSL: wsl.exe not found' + NL;
    Blocks := Blocks + '- WSL is not installed (wsl.exe is missing). Open PowerShell as administrator, ' +
      'run "wsl --install", restart the computer, then run setup again.' + NL;
    Exit;
  end;

  OutFile := ExpandConstant('{tmp}\wsl-check.txt');
  Code := RunWsl('--version', OutFile);
  Text := ReadWslText(OutFile);
  if Code <> 0 then
  begin
    // --version only exists in current WSL. Does an older one at least answer --status?
    StatusCode := RunWsl('--status', OutFile);
    if StatusCode = 0 then
    begin
      Info := Info + 'WSL: installed, but too old to report a version' + NL;
      Blocks := Blocks + '- WSL is out of date. Open PowerShell and run "wsl --update", then run setup again.' + NL;
    end
    else
    begin
      Info := Info + 'WSL: not set up (wsl.exe exit code ' + IntToStr(StatusCode) + ')' + NL;
      Blocks := Blocks + '- WSL is not set up on this computer. Open PowerShell as administrator, ' +
        'run "wsl --install", restart the computer, then run setup again.' + NL;
    end;
    Exit;
  end;

  Major := WslMajorVersion(Text);
  if Major < 0 then
  begin
    Info := Info + 'WSL: version could not be read' + NL;
    Warnings := Warnings + '- Could not read the WSL version. The simulator needs WSL 2.0 or newer.' + NL;
  end
  else
  begin
    Info := Info + 'WSL: version ' + IntToStr(Major) + '.x' + NL;
    if Major < 2 then
      Blocks := Blocks + '- WSL ' + IntToStr(Major) + '.x is too old: mirrored networking needs WSL 2.0 or newer. ' +
        'Open PowerShell and run "wsl --update", then run setup again.' + NL;
  end;

  // WSL2 runs in a lightweight virtual machine, so Windows must be running under its hypervisor
  Hv := HypervisorState;
  if Hv = 0 then
  begin
    Info := Info + 'Hypervisor: not running' + NL;
    Blocks := Blocks + '- Virtualization is not active, so WSL 2 cannot start. Check that "Virtual Machine ' +
      'Platform" is turned on in "Turn Windows features on or off", that virtualization is enabled in the ' +
      'BIOS/UEFI, and that you have restarted since enabling WSL.' + NL;
  end
  else if Hv = 1 then
    Info := Info + 'Hypervisor: running' + NL
  else
  begin
    Info := Info + 'Hypervisor: unknown' + NL;
    Warnings := Warnings + '- Could not tell whether virtualization is active (WSL 2 needs it).' + NL;
  end;
end;

// The simulator's Linux environment //

var
  DistroToRemove: String; // while uninstalling: the distribution setup created, if it did

// The distribution setup creates: "mrr", or what /DISTRO= says (for testing without touching a real "mrr")
function DistroName: String;
var
  I: Integer;
begin
  Result := ExpandConstant('{param:DISTRO|{#DefaultDistro}}');
  for I := 1 to Length(Result) do
    if not (((Result[I] >= 'a') and (Result[I] <= 'z')) or ((Result[I] >= 'A') and (Result[I] <= 'Z')) or
            ((Result[I] >= '0') and (Result[I] <= '9')) or (Result[I] = '-') or (Result[I] = '_') or
            (Result[I] = '.')) then
    begin
      Result := '{#DefaultDistro}'; // anything odd in the name: ignore it
      Exit;
    end;
end;

// True if WSL already has a distribution with this name
function DistroExists(const Name: String): Boolean;
var
  OutFile, List: String;
begin
  OutFile := ExpandConstant('{tmp}\wsl-list.txt');
  RunWsl('--list --quiet', OutFile);
  List := ReadWslText(OutFile);
  Result := Pos(#10 + Lowercase(Name) + #10, #10 + Lowercase(List) + #10) > 0;
end;

// Imports the bundled Linux environment into WSL as a distribution called Name. On failure returns False
// with WSL's own message in Problem, and a half-made distribution is removed again.
function ImportDistro(const Name: String; var Problem: String): Boolean;
var
  Payload, Target, OutFile: String;
begin
  Result := False;
  Payload := ExpandConstant('{tmp}\mrr-rootfs.tar.gz');
  OutFile := ExpandConstant('{tmp}\wsl-import.txt');
  if not FileExists(Payload) then
  begin
    Problem := 'The Linux environment is not part of this installer.';
    Exit;
  end;
  Target := GetDataDir('') + '\wsl\' + Name;
  ForceDirectories(Target);
  if RunWsl('--import ' + Name + ' "' + Target + '" "' + Payload + '" --version 2', OutFile) <> 0 then
  begin
    Problem := Trim(ReadWslText(OutFile));
    Exit;
  end;
  // start it once now, so a broken image shows up here and not on the first run
  if RunWsl('-d ' + Name + ' --exec true', OutFile) <> 0 then
  begin
    Problem := Trim(ReadWslText(OutFile));
    RunWsl('--unregister ' + Name, OutFile); // we created it a moment ago: safe to remove
    Exit;
  end;
  Result := True;
end;

function RemoveDistro(const Name: String): Boolean;
var
  OutFile: String;
begin
  OutFile := ExpandConstant('{tmp}\wsl-remove.txt');
  RunWsl('--terminate ' + Name, OutFile);
  Result := RunWsl('--unregister ' + Name, OutFile) = 0;
end;

// Deletes everything in Dir except the entry called Keep (the WSL disk, while its distribution is still registered)
procedure DeleteAllExcept(const Dir, Keep: String);
var
  FindRec: TFindRec;
begin
  if FindFirst(Dir + '\*', FindRec) then
  try
    repeat
      if (FindRec.Name <> '.') and (FindRec.Name <> '..') and (CompareText(FindRec.Name, Keep) <> 0) then
      begin
        if (FindRec.Attributes and $10) <> 0 then
          DelTree(Dir + '\' + FindRec.Name, True, True, True)
        else
          DeleteFile(Dir + '\' + FindRec.Name);
      end;
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;
end;

// The version of the Linux environment inside an installed distribution ('' if it has none: made by an older
// setup, or not ours). See STAMP_FILE in tools\build_distro.py.
function InstalledDistroVersion(const Name: String): String;
var
  OutFile: String;
begin
  Result := '';
  OutFile := ExpandConstant('{tmp}\wsl-version.txt');
  if RunWsl('-d ' + Name + ' --exec cat /etc/mrrsim-distro-version', OutFile) = 0 then
    Result := Trim(ReadWslText(OutFile));
end;

// Adds the Linux environment to WSL, unless a distribution with that name is already there. A distribution
// this setup made earlier is replaced when this setup carries a newer Linux environment (an upgrade brings new
// container images; leaving the old ones would break the new launcher).
procedure SetUpLinuxEnvironment(const Name: String);
var
  Problem, Ini, Installed: String;
  MadeBySetup: Boolean;
begin
  Ini := ExpandConstant('{app}\install.ini');
  // an earlier run of this setup (upgrade, repair) may have created this distribution: it is still ours
  MadeBySetup := (GetIniString('Install', 'DistroCreated', '0', Ini) = '1') and
    (CompareText(GetIniString('Install', 'DistroName', '', Ini), Name) = 0);
  SetIniString('Install', 'DistroName', Name, Ini);

  if DistroExists(Name) and MadeBySetup then
  begin
    Installed := InstalledDistroVersion(Name);
    if ('{#DistroVersion}' = '') or (Installed = '{#DistroVersion}') or
       (not FileExists(ExpandConstant('{tmp}\mrr-rootfs.tar.gz'))) then
    begin
      Log('WSL distribution ' + Name + ' was created by an earlier setup and is up to date (' + Installed + '): left as it is');
      Exit;
    end;
    Log('WSL distribution ' + Name + ' is out of date (installed "' + Installed + '", this setup carries "{#DistroVersion}")');
    if SuppressibleMsgBox('This version of {#AppName} comes with a newer Linux environment for the simulator ' +
         '(the WSL distribution "' + Name + '").' + NL + NL +
         'Replace the old one now? This takes a few minutes. Your scripts and projects are not affected; ' +
         'anything you added inside the old distribution is lost.' + NL + NL +
         'If you choose No, the simulator may not work until you run this setup again and choose Yes.',
         mbConfirmation, MB_YESNO, IDYES) <> IDYES then
    begin
      Log('The student kept the old distribution');
      Exit;
    end;
    WizardForm.StatusLabel.Caption := 'Removing the old Linux environment...';
    if not RemoveDistro(Name) then
    begin
      Log('Could not remove the old distribution ' + Name);
      SuppressibleMsgBox('The old Linux environment ("' + Name + '") could not be removed, so it was kept.' + NL + NL +
        'Close anything that uses WSL, run "wsl --shutdown" in a command prompt, then run this setup again.',
        mbError, MB_OK, IDOK);
      Exit;
    end;
    // it is gone: continue below as for a new installation
  end;

  if DistroExists(Name) then
  begin
    // never replace a distribution that is already there: it may hold someone's work
    Log('WSL distribution ' + Name + ' already exists: kept as it is');
    SetIniString('Install', 'DistroCreated', '0', Ini);
    SuppressibleMsgBox('A WSL distribution named "' + Name + '" already exists on this computer, so it was ' +
      'kept as it is and not replaced.' + NL + NL +
      'If the simulator reports missing container images, run "{#AppName} Check" from the Start menu.',
      mbInformation, MB_OK, IDOK);
    Exit;
  end;

  WizardForm.StatusLabel.Caption := 'Setting up the simulator''s Linux environment (this takes a few minutes)...';
  WizardForm.FilenameLabel.Caption := '';
  if ImportDistro(Name, Problem) then
  begin
    Log('WSL distribution ' + Name + ' created');
    SetIniString('Install', 'DistroCreated', '1', Ini);
  end
  else
  begin
    Log('Could not create WSL distribution ' + Name + ': ' + Problem);
    SetIniString('Install', 'DistroCreated', '0', Ini);
    SuppressibleMsgBox('Setup could not create the simulator''s Linux environment (WSL distribution "' + Name +
      '"):' + NL + NL + Problem + NL + NL +
      'The rest of {#AppName} is installed. Free some disk space or fix the problem above, then run setup again.',
      mbError, MB_OK, IDOK);
  end;
end;

// Mirrored networking //
// The simulator needs it: Windows (Unreal) and the Linux environment (ArduPilot) talk over 127.0.0.1.
// It is a setting in %USERPROFILE%\.wslconfig, it affects every WSL distribution the user has, and it
// only takes effect after WSL restarts, which stops everything running in WSL. So setup asks first.

// The WSL settings file. MRRSIM_WSLCONFIG points elsewhere (testing against a copy).
function WslConfigPath: String;
begin
  Result := GetEnv('MRRSIM_WSLCONFIG');
  if Result = '' then
    Result := ExpandConstant('{%USERPROFILE}\.wslconfig');
end;

function MirroredConfigured: Boolean;
begin
  Result := CompareText(Trim(GetIniString('wsl2', 'networkingMode', '', WslConfigPath)), 'mirrored') = 0;
end;

// setup /MIRRORED=yes or /MIRRORED=no answers for an unattended install; otherwise the user is asked
function WantMirroredNow: Boolean;
var
  Answer: String;
begin
  Answer := Lowercase(ExpandConstant('{param:MIRRORED|ask}'));
  if Answer = 'yes' then
    Result := True
  else if Answer = 'no' then
    Result := False
  else
    Result := SuppressibleMsgBox(
      '{#AppName} needs WSL''s "mirrored" networking, so Windows and the Linux environment share one network ' +
      'address (127.0.0.1). It is not turned on yet.' + NL + NL +
      'Setup can turn it on by adding networkingMode=mirrored to your WSL settings file:' + NL +
      WslConfigPath + NL +
      '(The old file is saved next to it as .wslconfig.mrrsim.bak.)' + NL + NL +
      'WSL has to restart for this to take effect. That STOPS EVERYTHING RUNNING IN WSL, including Docker ' +
      'Desktop''s engine, so save your work in any WSL window first.' + NL + NL +
      'Turn on mirrored networking and restart WSL now?',
      mbConfirmation, MB_YESNO, IDNO) = IDYES;  // an unattended install without /MIRRORED changes nothing
end;

// Writes the setting (through the launcher, which keeps the rest of the file and a backup) and restarts WSL
function EnableMirrored(var Problem: String): Boolean;
var
  OutFile: String;
begin
  Result := False;
  OutFile := ExpandConstant('{tmp}\wslconfig-write.txt');
  if RunProgram(ExpandConstant('{app}\{#LauncherExe}'), 'wsl-config --only-networking', OutFile) <> 0 then
  begin
    Problem := Trim(ReadWslText(OutFile));
    if Problem = '' then
      Problem := 'the launcher could not write ' + WslConfigPath;
    Exit;
  end;
  Log('wsl-config: ' + Trim(ReadWslText(OutFile)));
  WizardForm.StatusLabel.Caption := 'Restarting WSL...';
  RunWsl('--shutdown', OutFile);
  Result := True;
end;

// Step before the Linux environment: make sure mirrored networking is on. Returns True when it is.
function EnsureMirrored: Boolean;
var
  Problem: String;
begin
  Result := MirroredConfigured;
  if Result then
    Exit;
  if not WantMirroredNow then
  begin
    Log('mirrored networking left off by the user');
    Exit;
  end;
  if EnableMirrored(Problem) then
  begin
    Log('mirrored networking turned on and WSL restarted');
    Result := True;
  end
  else
  begin
    Log('could not turn on mirrored networking: ' + Problem);
    SuppressibleMsgBox('Setup could not turn on mirrored networking:' + NL + NL + Problem + NL + NL +
      'You can do it yourself: add these two lines to ' + WslConfigPath + NL + NL + '[wsl2]' + NL +
      'networkingMode=mirrored' + NL + NL + 'then run "wsl --shutdown" in a command prompt.',
      mbError, MB_OK, IDOK);
  end;
end;

// Last step: did WSL really start with mirrored networking? (It can fall back to none without saying so.)
procedure CheckMirroredWorks(const Name: String);
var
  OutFile, Mode: String;
begin
  OutFile := ExpandConstant('{tmp}\wsl-mode.txt');
  if (not DistroExists(Name)) or (RunWsl('-d ' + Name + ' --exec wslinfo --networking-mode', OutFile) <> 0) then
    Exit; // no such distribution, or a WSL too old to say: nothing to report
  Mode := Lowercase(Trim(ReadWslText(OutFile)));
  Log('WSL networking mode inside ' + Name + ': ' + Mode);
  if Mode <> 'mirrored' then
    SuppressibleMsgBox('WSL is set to use mirrored networking, but it started with networking mode "' + Mode +
      '" instead, so the simulator will not be able to connect yet.' + NL + NL +
      'Restart Windows, then open "{#AppName} Check" from the Start menu. If it still reports a networking ' +
      'problem, it shows what to try.',
      mbError, MB_OK, IDOK);
end;

// After the files are installed: networking first, then the Linux environment, then a check that it works
// The settings file is replaced by every version (its keys change between versions, so an old one is not safe to
// keep). If it differs from the shipped one, that copy is kept next to it as mrrsim.toml.previous so they can carry the changes over.
procedure BackUpSettings;
var
  Settings, Shipped: String;
begin
  Settings := ExpandConstant('{app}\config\mrrsim.toml');
  if not FileExists(Settings) then
    Exit;
  ExtractTemporaryFile('shipped-mrrsim.toml');
  Shipped := ExpandConstant('{tmp}\shipped-mrrsim.toml');
  // An untouched file is not worth a backup, and must not overwrite an earlier backup of real edits (running setup
  // twice in a row would otherwise throw away the only copy of them).
  if GetSHA256OfFile(Settings) = GetSHA256OfFile(Shipped) then
  begin
    Log('The settings file is the one that ships with setup: no backup needed');
    Exit;
  end;
  if CopyFile(Settings, Settings + '.previous', False) then
    Log('The previous settings file was kept as ' + Settings + '.previous')
  else
    Log('Could not keep a copy of the previous settings file');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Name: String;
  Mirrored: Boolean;
begin
  if CurStep = ssInstall then
  begin
    BackUpSettings;
    Exit;
  end;
  if CurStep <> ssPostInstall then
    Exit;
  Name := DistroName;
  Mirrored := EnsureMirrored;
  SetUpLinuxEnvironment(Name);
  if Mirrored then
    CheckMirroredWorks(Name)
  else
    SuppressibleMsgBox('Mirrored networking is off, so the simulator cannot connect yet.' + NL + NL +
      'To turn it on, add these two lines to ' + WslConfigPath + NL + NL + '[wsl2]' + NL +
      'networkingMode=mirrored' + NL + NL + 'then run "wsl --shutdown" in a command prompt. ' +
      '"{#AppName} Check" in the Start menu shows when it is right.',
      mbInformation, MB_OK, IDOK);
end;

// System Checks
function InitializeSetup: Boolean;
var
  Blocks, Warnings, Report, Gpus, DataPath, WslInfo: String;
  Ram, FreeGB: Int64;
begin
  Result := True;

  // Testing switch: skip every check
  if HasSwitch('/SKIPCHECKS') then
    Exit;

  // Already installed in the other mode? Stop before creating a second copy
  if InstalledInOtherMode then
  begin
    if IsAdminInstallMode then
      SuppressibleMsgBox('{#AppName} is already installed for your account only.' + NL + NL +
        'Uninstall it from Settings > Apps first, then install for all users.',
        mbCriticalError, MB_OK, IDOK)
    else
      SuppressibleMsgBox('{#AppName} is already installed for all users on this computer.',
        mbInformation, MB_OK, IDOK);
    Result := False;
    Exit;
  end;
  
  Blocks := '';
  Warnings := '';

  // Memory
  Ram := TotalRamMB;
  if Ram < 0 then
    Warnings := Warnings + '- Could not read the amount of installed memory.' + NL
  else if Ram < {#MinRamMB} then
    Blocks := Blocks + '- At least {#MinRamMB} MB of RAM is required (found ' + IntToStr(Ram) + ' MB).' + NL
  else if Ram < {#WarnRamMB} then
    Warnings := Warnings + '- This computer has less than {#WarnRamMB} MB of RAM. The simulator will work, ' +
      'but expect fewer vehicles and cameras.' + NL;

  // Disk space, on the drive where downloads will go in the chosen mode
  if IsAdminInstallMode then
    DataPath := ExpandConstant('{commonappdata}')
  else
    DataPath := ExpandConstant('{localappdata}');
  FreeGB := FreeSpaceGB(DataPath);
  if FreeGB < 0 then
    Warnings := Warnings + '- Could not read free disk space.' + NL
  else if FreeGB < {#MinFreeGB} then
    Blocks := Blocks + '- At least {#MinFreeGB} GB of free space is needed on the drive containing ' +
      DataPath + ' (found ' + IntToStr(FreeGB) + ' GB).' + NL;

  // Graphics
  Gpus := DescribeGpus;
  if Gpus = '' then
    Warnings := Warnings + '- Could not detect the graphics adapter.' + NL
  else
  begin
    if Pos('Basic Display', Gpus) > 0 then
      Blocks := Blocks + '- No graphics driver is installed (Windows is using ' +
        '"Microsoft Basic Display Adapter"). Install the Intel or AMD driver from the ' +
        'laptop manufacturer, then run setup again.' + NL;
    if (Pos('Intel(R) HD Graphics', Gpus) > 0) or (Pos('UHD Graphics 6', Gpus) > 0) then
      Warnings := Warnings + '- This is an older Intel GPU. The simulator may run slowly; ' +
        'use the Low graphics preset.' + NL;
  end;
  
  // Visual C++ runtime
  if not VCRedistInstalled then
    Warnings := Warnings + '- The Microsoft Visual C++ runtime is missing or out of date. ' +
      'Unreal needs it; install it from https://aka.ms/vs/17/release/vc_redist.x64.exe ' +
      'before launching the simulator.' + NL;

  // WSL: must already be set up; this only checks
  WslInfo := '';
  CheckWsl(Blocks, Warnings, WslInfo);

  // Summary of everything detected
  Report := 'Memory: ' + IntToStr(Ram) + ' MB' + NL +
            'Free space (' + DataPath + '): ' + IntToStr(FreeGB) + ' GB' + NL +
            WslInfo +
            'Graphics:' + NL + Gpus;

  // Testing switch: always show what was detected
  if HasSwitch('/REPORT') then
    SuppressibleMsgBox('System check results:' + NL + NL + Report,
      mbInformation, MB_OK, IDOK);

  // Any block cancels setup
  if Blocks <> '' then
  begin
    SuppressibleMsgBox('{#AppName} can''t be installed on this computer:' + NL + NL +
      Blocks + NL + 'Details:' + NL + Report, mbCriticalError, MB_OK, IDOK);
    Result := False;
    Exit;
  end;

  // Warnings let the user decide
  if Warnings <> '' then
    Result := SuppressibleMsgBox('Setup found some issues:' + NL + NL + Warnings + NL +
      'Install anyway?', mbConfirmation, MB_YESNO, IDYES) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir, Question, Ini: String;
  DistroStays: Boolean;
begin
  if CurUninstallStep = usUninstall then
  begin
    // install.ini is deleted during the uninstall, so note now whether setup created the distribution
    Ini := ExpandConstant('{app}\install.ini');
    if GetIniString('Install', 'DistroCreated', '0', Ini) = '1' then
      DistroToRemove := GetIniString('Install', 'DistroName', '', Ini);
  end;

  if CurUninstallStep = usPostUninstall then
  begin
    // The Linux environment first: its disk lives inside the data folder below. Only a distribution that
    // this setup created is offered for removal, never one that was already there.
    DistroStays := False;
    if (DistroToRemove <> '') and DistroExists(DistroToRemove) then
    begin
      if SuppressibleMsgBox('Also remove the simulator''s Linux environment (WSL distribution "' +
          DistroToRemove + '", several GB)?' + NL + NL + 'Installing again brings it back.',
          mbConfirmation, MB_YESNO, IDYES) = IDYES then
      begin
        if not RemoveDistro(DistroToRemove) then
        begin
          DistroStays := True;
          SuppressibleMsgBox('The WSL distribution "' + DistroToRemove + '" could not be removed. ' +
            'You can remove it yourself from a command prompt with:' + NL + 'wsl --unregister ' + DistroToRemove,
            mbInformation, MB_OK, IDOK);
        end;
      end
      else
        DistroStays := True;
    end;

    DataDir := GetDataDir('');
    if DirExists(DataDir) then
    begin
      Question := 'Also delete the simulator''s data files (logs and downloads) in' + NL +
        DataDir + '?';
      if DistroStays then
        Question := Question + NL + NL + 'The Linux environment you kept is not touched.';
      if IsAdminInstallMode then
        Question := Question + NL + NL +
          'This removes them for every user on this computer.';
      if SuppressibleMsgBox(Question, mbConfirmation, MB_YESNO, IDNO) = IDYES then
      begin
        if DistroStays then
          DeleteAllExcept(DataDir, 'wsl')
        else if not DelTree(DataDir, True, True, True) then
          SuppressibleMsgBox('Some files in' + NL + DataDir + NL +
            'could not be deleted. You can remove the folder manually.',
            mbInformation, MB_OK, IDOK);
      end;
    end;
  end;
end;